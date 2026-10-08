# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A conversation reset is not an embed — the rest of the same defect.

``SessionTurnIndex.clear()`` ran the body of a successful embed without one:
it set ``verified`` (the embedder has answered) AND cleared ``degraded`` (the
embedder was found down). test_zz192_p4_05.py pins the first half on a machine
whose embedder never came up. These cases pin what else that did:

  * on a machine whose embedder answered once and then went down, ending the
    conversation turned "MEMORY DEGRADED" back into "session recall active",
    with nothing having answered since — the failure is a measurement too, and
    it is about the machine, not the conversation;
  * a reset of an index that was never asked anything reported a verified
    embedder, without one call being made to it;
  * the daemon's own ResetConversation and GetStatus bodies
    (``InterGenDaemon.reset_conversation`` / ``InterGenDaemon.status``) carried
    both, so the payload ``intergen status`` renders said so;
  * the glass record held the failure while the status said otherwise.

Controls pin the other direction: the reset still empties the conversation,
a measured success still survives it, and the next real embed after a reset
still settles both flags either way — so the fix cannot latch a failure.
"""
from __future__ import annotations

import io
import json
import threading
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock


def _live(texts):
    """An embedding server that answers."""
    return [[1.0, 0.0, 0.0] for _ in texts]


class _Switch:
    """An embedder that answers or does not, flipped by the case. A None return
    is what memory.SessionTurnIndex documents as the server-down signal."""

    def __init__(self, up: bool):
        self.up = up
        self.calls = 0

    def __call__(self, texts):
        self.calls += 1
        return _live(texts) if self.up else None


def _index(embedder):
    from intergen.memory import SessionTurnIndex
    return SessionTurnIndex(embedder=embedder)


def _wait(predicate, seconds=5.0):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    return predicate()


class AMeasuredFailureSurvivesAReset(unittest.TestCase):
    """The index layer: the reset must not clear a degraded flag it did not
    measure."""

    def _answered_then_down(self):
        sw = _Switch(up=True)
        idx = _index(sw)
        self.addCleanup(idx.stop)
        idx.index_turn("what year was Linux released?", "1991.")
        self.assertTrue(_wait(lambda: idx.indexed_count == 1),
                        "control: the answering embedder indexed nothing")
        self.assertTrue(idx.verified, "control: an answered embed did not verify")
        sw.up = False
        self.assertIsNone(idx.embed_query("what did I say about the car?"),
                          "control: the embedder still answered, so this case "
                          "is not measuring a machine whose embedder went down")
        self.assertTrue(idx.degraded,
                        "control: the failed embed did not raise the loud flag")
        return sw, idx

    def test_a_reset_keeps_a_measured_failure(self):
        sw, idx = self._answered_then_down()
        calls = sw.calls
        idx.clear()
        self.assertEqual(sw.calls, calls, "the reset called the embedder")
        self.assertTrue(
            idx.degraded,
            "a conversation reset cleared the degraded flag on a machine whose "
            "last embed failed; nothing answered in between, and the reset "
            "performs no embed")

    def test_a_reset_keeps_the_earlier_measured_success_too(self):
        """CONTROL: both measurements stand; the reset decides neither."""
        _sw, idx = self._answered_then_down()
        idx.clear()
        self.assertTrue(idx.verified)

    def test_the_reset_still_empties_the_conversation(self):
        """CONTROL: what the reset IS for still happens."""
        _sw, idx = self._answered_then_down()
        idx.clear()
        self.assertEqual(idx.indexed_count, 0)
        self.assertEqual(idx.turns_seen, 0)


class AnIndexNeverAskedClaimsNothingAfterAReset(unittest.TestCase):
    """The never-asked state: an embedder wired and never called."""

    def test_a_fresh_index_starts_with_nothing_measured(self):
        """CONTROL, beside the case below: the honest starting state."""
        sw = _Switch(up=True)
        idx = _index(sw)
        self.addCleanup(idx.stop)
        self.assertFalse(idx.verified)
        self.assertFalse(idx.degraded)
        self.assertEqual(sw.calls, 0)

    def test_a_reset_of_an_index_never_asked_claims_nothing(self):
        sw = _Switch(up=True)
        idx = _index(sw)
        self.addCleanup(idx.stop)
        idx.clear()
        self.assertEqual(sw.calls, 0,
                         "control: the embedder was called, so a verified flag "
                         "here could be a real measurement")
        self.assertFalse(
            idx.verified,
            "a reset of an index that never called its embedder reported the "
            "embedder as verified")
        self.assertFalse(idx.degraded)


class TheNextRealEmbedStillSettlesBothFlags(unittest.TestCase):
    """CONTROLS: keeping the measurements across a reset must not latch them.
    The next embed that really happens decides, in either direction."""

    def test_a_success_after_the_reset_clears_degraded_and_verifies(self):
        sw = _Switch(up=False)
        idx = _index(sw)
        self.addCleanup(idx.stop)
        self.assertIsNone(idx.embed_query("q"))
        idx.clear()
        sw.up = True
        self.assertIsNotNone(idx.embed_query("q"))
        self.assertFalse(idx.degraded, "a real success after the reset did not "
                                       "clear the degraded flag")
        self.assertTrue(idx.verified)

    def test_a_failure_after_the_reset_is_reported(self):
        sw = _Switch(up=True)
        idx = _index(sw)
        self.addCleanup(idx.stop)
        self.assertIsNotNone(idx.embed_query("q"))
        idx.clear()
        sw.up = False
        self.assertIsNone(idx.embed_query("q"))
        self.assertTrue(idx.degraded)
        self.assertTrue(idx.verified, "the earlier measured success was lost")


class TheResetIsRecordedAndTheStatusAgreesWithTheRecord(unittest.TestCase):
    """The glass record and the index's own flags, read side by side."""

    def test_after_the_reset_the_flags_agree_with_the_last_recorded_embed(self):
        from intergen import glass
        events = []
        lock = threading.Lock()
        real_emit = glass.emit

        def record(phase, event, *a, **k):
            with lock:
                events.append((phase, event))
            return real_emit(phase, event, *a, **k)

        sw = _Switch(up=True)
        with mock.patch.object(glass, "emit", side_effect=record):
            idx = _index(sw)
            self.addCleanup(idx.stop)
            idx.index_turn("what year was Linux released?", "1991.")
            # The worker writes its "indexed" row AFTER the turn is counted, so
            # wait for the row itself or it can land after the failure below.
            self.assertTrue(_wait(lambda: ("memory", "indexed") in events),
                            "control: the answering embedder indexed nothing")
            sw.up = False
            idx.embed_query("what did I say about the car?")
            idx.clear()
        with lock:
            seen = list(events)
        self.assertIn(("memory", "index_cleared"), seen,
                      "control: the reset left no record of itself")
        before = seen[:seen.index(("memory", "index_cleared"))]
        outcomes = [e for p, e in before if p == "memory" and e in
                    ("indexed", "recovered", "degraded", "index_degraded")]
        self.assertEqual(outcomes[-1:], ["degraded"],
                         "control: the record does not end on the failure")
        self.assertTrue(
            idx.degraded,
            "the record's last embed outcome before the reset is a failure and "
            "the index reports the embedder as not degraded")


# ── The daemon's own reset and status bodies ─────────────────────────────


class _Tools:
    tool_count = 0


class _Semantic:
    @staticmethod
    def get_intent_count():
        return 0


class _LLM:
    @staticmethod
    def get_escalation_mode():
        class _M:
            value = "auto"
        return _M()


def _router(embedder):
    """A real ConversationRouter with the collaborators status reads, built as
    intergen/tests/test_status_reads_the_routed_conversation.py builds it."""
    from intergen.router import ConversationRouter
    r = ConversationRouter.__new__(ConversationRouter)
    r._max_history = 20
    r._record = lambda *a, **k: None
    r._current_query_type = "general"
    r._memory = None
    r._embedder = embedder
    r._tools = _Tools()
    r._semantic = _Semantic()
    r._llm = _LLM()
    r._metrics = None
    r.detach_conversation()
    return r


def _refusing_review(*_a, **_k):
    raise AssertionError("no tool is dispatched here, so no review surface "
                         "may be asked anything")


def _daemon(router):
    """The daemon without its heavy start-up, as the same test builds it; its
    reset_conversation() and status() are the D-Bus ResetConversation and
    GetStatus bodies."""
    from intergen.dbus_daemon import InterGenDaemon
    d = InterGenDaemon.__new__(InterGenDaemon)
    d._running = True
    d._hardware_tier = None
    d._model_loaded = None
    d._requests_handled = 0
    d._last_error = None
    d._model_server_integrity_failure = None
    d._llama = None
    d._router = router
    d._matcher = None
    d._tools = None
    d._memory = None
    d._watchdog = None
    d._metrics = None
    d._review_autopilot = None
    d._review_callback_override = _refusing_review
    d._paused = False
    d._pause_holds = []
    d._conversation = router.new_conversation()
    return d


def _rendered(status: dict) -> str:
    from intergen import cli
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.print_status(status)
    return buf.getvalue()


class TheDaemonsResetAndStatusOnAMachineWhoseEmbedderWentDown(unittest.TestCase):
    """What `intergen status` is handed after ResetConversation."""

    def setUp(self):
        self.sw = _Switch(up=True)
        self.daemon = _daemon(_router(self.sw))
        self.index = self.daemon._conversation.turn_index
        self.addCleanup(self.index.stop)
        self.index.index_turn("what year was Linux released?", "1991.")
        self.assertTrue(_wait(lambda: self.index.indexed_count == 1),
                        "control: the answering embedder indexed nothing")
        self.sw.up = False
        self.index.embed_query("what did I say about the car?")

    def test_before_the_reset_the_line_reads_degraded(self):
        """CONTROL: the renderer is rendering, and the failure reaches it."""
        status = json.loads(self.daemon.status())
        self.assertTrue(status["memory_index"]["degraded"])
        out = _rendered(status)
        self.assertIn("InterGen Status", out)
        self.assertIn("MEMORY DEGRADED", out)

    def test_after_the_reset_the_payload_and_the_line_still_read_degraded(self):
        self.assertEqual(json.loads(self.daemon.reset_conversation()),
                         {"reset": True}, "control: the reset did not run")
        status = json.loads(self.daemon.status())
        self.assertTrue(
            status["memory_index"]["degraded"],
            "after ResetConversation the status payload reports a machine "
            "whose last embed failed as not degraded")
        out = _rendered(status)
        self.assertIn("InterGen Status", out,
                      "control: the renderer produced no output")
        self.assertIn("MEMORY DEGRADED", out)
        self.assertNotIn(
            "session recall active", out,
            "after ResetConversation the status line tells the person session "
            "recall is active; the last embed failed and none has answered since")


class TheDaemonsResetOnAMachineWhoseEmbedderNeverCameUp(unittest.TestCase):
    """The confirmed case, through the daemon's own bodies rather than the
    router's reset and a copy of the daemon's mapping."""

    def setUp(self):
        self.sw = _Switch(up=False)
        self.daemon = _daemon(_router(self.sw))
        self.index = self.daemon._conversation.turn_index
        self.addCleanup(self.index.stop)
        self.index.embed_query("what did I say about the car?")

    def test_before_the_reset_nothing_is_verified(self):
        """CONTROL, beside the case below."""
        mem = json.loads(self.daemon.status())["memory_index"]
        self.assertFalse(mem["verified"])
        self.assertFalse(mem["embedder_answered"])
        self.assertTrue(mem["degraded"])

    def test_after_the_reset_nothing_is_verified_either(self):
        self.assertEqual(json.loads(self.daemon.reset_conversation()),
                         {"reset": True}, "control: the reset did not run")
        status = json.loads(self.daemon.status())
        mem = status["memory_index"]
        self.assertFalse(
            mem["verified"],
            "after ResetConversation the status payload reports an embedder "
            "that has never answered as verified")
        self.assertFalse(mem["embedder_answered"])
        out = _rendered(status)
        self.assertIn("InterGen Status", out,
                      "control: the renderer produced no output")
        self.assertNotIn("session recall active", out)
        self.assertNotIn("embedder answered this index: yes", out)


class AMeasuredSuccessSurvivesTheDaemonsReset(unittest.TestCase):
    """CONTROL at the daemon layer: an embedder that answers is still reported
    as working after ResetConversation."""

    def test_the_line_still_reads_active_after_the_reset(self):
        daemon = _daemon(_router(_live))
        index = daemon._conversation.turn_index
        self.addCleanup(index.stop)
        index.index_turn("what year was Linux released?", "1991.")
        self.assertTrue(_wait(lambda: index.indexed_count == 1))
        daemon.reset_conversation()
        status = json.loads(daemon.status())
        self.assertTrue(status["memory_index"]["verified"])
        self.assertFalse(status["memory_index"]["degraded"])
        self.assertIn("session recall active", _rendered(status))


if __name__ == "__main__":
    unittest.main()
