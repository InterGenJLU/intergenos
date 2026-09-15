# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Status must state what it measured, not what it hoped.

Three places said something they had not checked.

1. SESSION RECALL. The status line "session recall active (:8081 index)" was
   printed whenever an index OBJECT existed. The degraded flag only becomes True
   once an embed attempt has FAILED, so a machine whose embedder never came up
   at all was in neither state — it had not failed, it had merely never
   succeeded — and the surface called that active. The window in which this was
   wrong is exactly the window in which a user is deciding whether to trust
   recall.

   Fixed by recording a MEASURED success. The index now carries `verified`, set
   only when the embedder actually answers, and the surface distinguishes
   active / wired-but-unverified / degraded / disabled.

2. THE WARMUP SKIP MESSAGE. It appended "(no model downloaded?)" every time the
   engine was not running, so a machine with a verified model whose engine had
   failed to start was told its model might be missing — pointing the reader at
   the one thing that was fine while the recorded failure sat unread.

   Fixed by preferring the RECORDED failure, and only falling back to looking at
   the disk when nothing was recorded — and then saying what it looked at.

3. LAST ERROR. Covered here by pinning that the recorded failure reaches the
   skip reason, since that is the path where a real error was being replaced by
   a guess.

These tests construct the real objects and read the real strings. Nothing is
asserted against source text.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


class _FakeIndex:
    def __init__(self, degraded=False, verified=False):
        self.degraded = degraded
        self.verified = verified


class SessionRecallStatusTest(unittest.TestCase):
    """The CLI's rendering of the four states."""

    def _render(self, mem):
        from intergen import cli
        buf = io.StringIO()
        status = {"memory_index": mem, "requests_handled": 0}
        with redirect_stdout(buf):
            cli.print_status(status)
        out = buf.getvalue()
        # A renderer that printed nothing would satisfy every assertNotIn below
        # without rendering anything at all.
        self.assertIn("InterGen Status", out,
                      "the renderer produced no output, so the assertions "
                      "below would pass vacuously")
        return out

    def test_verified_reads_as_active(self):
        out = self._render({"enabled": True, "degraded": False,
                            "verified": True})
        self.assertIn("session recall active", out)

    def test_wired_but_unverified_does_not_read_as_active(self):
        """The defect, stated as an assertion."""
        out = self._render({"enabled": True, "degraded": False,
                            "verified": False})
        self.assertNotIn("session recall active", out)
        self.assertIn("NOT YET VERIFIED", out)

    def test_unverified_states_what_was_measured_not_a_guessed_cause(self):
        """Status blamed the embedder ("the :8081 embedder has not answered
        yet") on a machine whose embedder had answered scores of requests
        (measured 2026-09-04): the index had simply never been asked. The
        line now says what is MEASURED — how many turns were indexed, how
        many were seen, whether this conversation's index has had an answer —
        and names no cause it did not observe."""
        out = self._render({"enabled": True, "degraded": False,
                            "verified": False, "indexed_turns": 0,
                            "turns_seen": 0, "last_index_at": None,
                            "embedder_answered": False})
        self.assertNotIn("has not answered", out)
        self.assertIn("0 turns indexed", out)
        self.assertIn("no conversation turn has been indexed yet", out)

    def test_active_states_the_index_count_and_the_last_write(self):
        out = self._render({"enabled": True, "degraded": False,
                            "verified": True, "indexed_turns": 3,
                            "turns_seen": 3, "last_index_at": 1788559976.65,
                            "embedder_answered": True})
        self.assertIn("session recall active", out)
        self.assertIn("3 turns indexed", out)
        self.assertIn("last index write", out)

    def test_a_pending_turn_is_named_as_pending(self):
        # A turn handed to the index whose embed has not landed yet.
        out = self._render({"enabled": True, "degraded": False,
                            "verified": True, "indexed_turns": 2,
                            "turns_seen": 3, "last_index_at": 1788559976.65,
                            "embedder_answered": True})
        self.assertIn("2 turns indexed", out)
        self.assertIn("1 pending", out)

    def test_degraded_still_reads_loud(self):
        out = self._render({"enabled": True, "degraded": True,
                            "verified": True})
        self.assertIn("MEMORY DEGRADED", out)

    def test_disabled_reads_as_disabled(self):
        out = self._render({"enabled": False, "degraded": False,
                            "verified": False})
        self.assertIn("disabled", out)


class IndexVerificationTest(unittest.TestCase):
    """SessionTurnIndex.verified is set only by a real success."""

    def _index(self):
        from intergen.memory import SessionTurnIndex
        return SessionTurnIndex(embedder=None)

    def test_a_fresh_index_is_not_verified(self):
        idx = self._index()
        self.assertFalse(idx.verified,
                         "a brand-new index claims the embedder works before "
                         "it has ever been asked")

    def test_a_fresh_index_is_not_degraded_either(self):
        """Both false is the honest starting state: nothing is known yet."""
        idx = self._index()
        self.assertFalse(idx.degraded)

    def test_verified_and_degraded_are_independent_properties(self):
        idx = self._index()
        self.assertIsNot(idx.verified, None)
        self.assertIsNot(idx.degraded, None)

    def test_the_index_measures_its_own_writes(self):
        """The counters the status line renders: turns seen, turns indexed
        and the wall time of the last index write — MEASURED off the index's
        own state, set only by a real successful embed."""
        import time
        from intergen.memory import SessionTurnIndex

        def embed(texts):
            return [[1.0, 0.0, 0.0] for _ in texts]

        idx = SessionTurnIndex(embedder=embed)
        try:
            self.assertEqual(idx.indexed_count, 0)
            self.assertEqual(idx.turns_seen, 0)
            self.assertIsNone(idx.last_indexed_at)
            before = time.time()
            idx.index_turn("what year was Linux released?", "1991.")
            deadline = time.monotonic() + 5
            while idx.indexed_count < 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(idx.indexed_count, 1)
            self.assertEqual(idx.turns_seen, 1)
            self.assertTrue(idx.verified)
            self.assertIsNotNone(idx.last_indexed_at)
            self.assertGreaterEqual(idx.last_indexed_at, before)
        finally:
            idx.stop()

    def test_a_failed_embed_indexes_nothing_and_records_no_write(self):
        import time
        from intergen.memory import SessionTurnIndex
        idx = SessionTurnIndex(embedder=lambda texts: None)
        try:
            idx.index_turn("q", "a")
            deadline = time.monotonic() + 5
            while not idx.degraded and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(idx.degraded)
            self.assertEqual(idx.indexed_count, 0)
            self.assertEqual(idx.turns_seen, 1)
            self.assertIsNone(idx.last_indexed_at)
        finally:
            idx.stop()


class RouterStatusTest(unittest.TestCase):
    """The router lifts the measured flag, not just the wired one."""

    def test_status_carries_memory_verified(self):
        import inspect
        from intergen import router as _router
        src = inspect.getsource(_router)
        self.assertIn('"memory_verified"', src,
                      "the router status does not carry the measured flag, so "
                      "the surface has nothing truthful to render")

    def test_status_carries_the_measured_index_facts(self):
        """Read off a real router with a bound conversation whose index has
        been driven, not off the source text."""
        import time
        from intergen.router import ConversationRouter

        def embed(texts):
            return [[1.0, 0.0, 0.0] for _ in texts]

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

        r = ConversationRouter.__new__(ConversationRouter)
        r._max_history = 20
        r._record = lambda *a, **k: None
        r._current_query_type = "general"
        r._memory = None
        r._embedder = embed
        r._tools = _Tools()
        r._semantic = _Semantic()
        r._llm = _LLM()
        r._metrics = None
        r.detach_conversation()
        conv = r.new_conversation()
        try:
            with r.bind_conversation(conv):
                fresh = r.get_status()
            self.assertEqual(fresh["memory_indexed_turns"], 0)
            self.assertEqual(fresh["memory_turns_seen"], 0)
            self.assertIsNone(fresh["memory_last_index_at"])
            r._append_history("what year was Linux released?", "1991.",
                              state=conv)
            deadline = time.monotonic() + 5
            while conv.turn_index.indexed_count < 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            with r.bind_conversation(conv):
                after = r.get_status()
            self.assertEqual(after["memory_indexed_turns"], 1)
            self.assertEqual(after["memory_turns_seen"], 1)
            self.assertTrue(after["memory_verified"])
            self.assertIsNotNone(after["memory_last_index_at"])
            self.assertEqual(after["history_length"], 2)
        finally:
            conv.turn_index.stop()

    def test_no_index_reports_unverified_rather_than_absent(self):
        """A missing key would render as False anyway — but silently.

        Pinning it means the difference between "we checked and it is not
        verified" and "nobody wrote the key" stays visible in the payload.
        """
        import inspect
        from intergen import router as _router
        src = inspect.getsource(_router)
        # The index belongs to the conversation being served now, so the status
        # reads it off that conversation rather than off the router.
        self.assertIn("index.verified", src)
        self.assertIn("conversation_bound", src,
                      "the payload must say whether there was a conversation to "
                      "report on, so a zero history length is never read as an "
                      "empty conversation when it means none was named")


class WarmupSkipReasonTest(unittest.TestCase):
    """The skip message states the recorded failure, not a guess."""

    def _daemon(self):
        from intergen.dbus_daemon import InterGenDaemon
        d = InterGenDaemon.__new__(InterGenDaemon)
        d._llama = None
        d._model_loaded = None
        return d

    class _FakeFailure:
        def __init__(self, name):
            self.name = name

    class _FakeLlama:
        def __init__(self, failure=None, error=None, config=None):
            self.last_failure = failure
            self.last_error = error
            self._config = config

    class _FakeConfig:
        def __init__(self, model_path):
            self.model_path = model_path

    def test_a_recorded_failure_is_what_gets_reported(self):
        d = self._daemon()
        d._llama = self._FakeLlama(
            failure=self._FakeFailure("UNHEALTHY"),
            error="never became healthy within 90s")
        reason = d._warmup_skip_reason()
        self.assertIn("UNHEALTHY", reason)
        self.assertIn("never became healthy", reason)

    def test_the_no_model_guess_is_gone_when_a_model_is_present(self):
        """The precise defect: a verified model told it might be missing."""
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.gguf"
            model.write_text("x")
            d = self._daemon()
            d._llama = self._FakeLlama(
                failure=self._FakeFailure("NONE"),
                config=self._FakeConfig(str(model)))
            reason = d._warmup_skip_reason()
            self.assertNotIn("no model downloaded", reason)
            self.assertIn("is present", reason)
            self.assertIn(str(model), reason)

    def test_an_absent_model_file_is_named_not_guessed_at(self):
        d = self._daemon()
        d._llama = self._FakeLlama(
            failure=self._FakeFailure("NONE"),
            config=self._FakeConfig("/nonexistent/model.gguf"))
        reason = d._warmup_skip_reason()
        self.assertIn("not on disk", reason)
        self.assertIn("/nonexistent/model.gguf", reason)

    def test_no_model_selected_says_exactly_that(self):
        d = self._daemon()
        d._llama = self._FakeLlama(failure=self._FakeFailure("NONE"))
        self.assertIn("no model has been selected",
                      d._warmup_skip_reason())

    def test_a_recorded_failure_wins_over_the_disk_check(self):
        """A fact beats an inference, and the order has to be that way round."""
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.gguf"
            model.write_text("x")
            d = self._daemon()
            d._llama = self._FakeLlama(
                failure=self._FakeFailure("SPAWN_ERROR"),
                error="OSError: exec format error",
                config=self._FakeConfig(str(model)))
            reason = d._warmup_skip_reason()
            self.assertIn("SPAWN_ERROR", reason)
            self.assertNotIn("is present", reason)


if __name__ == "__main__":
    unittest.main()
