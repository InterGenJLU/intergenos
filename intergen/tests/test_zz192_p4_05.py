# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A conversation reset is not an embed.

``SessionTurnIndex.verified`` exists to separate "session recall works" from
"session recall is configured". Its own docstring states the contract::

    True once the embedder has actually answered at least once.
    ... Only a MEASURED success sets it, so a status surface can say what it
    observed rather than what it hoped.

The field was added because Status said "session recall active" on the strength
of an index OBJECT existing, on machines where the embedder had never come up.

``SessionTurnIndex.clear()`` -- the ResetConversation path -- sets
``self._verified = True`` under the comment "A success here is the only proof
the embedder is actually up", copied from the two sites where an embed really
did answer (``_index_one`` and ``_note_embed_success``). No embed happens in
clear(). So on a machine whose embedding server never came up, ending one
conversation and starting the next MANUFACTURES the measurement: Status then
reports verified / embedder answered / not degraded, and the CLI prints
"session recall active", in exactly the window in which the person is deciding
whether to trust recall.

These cases assert the contract the docstring promises, at three layers: the
index, the router status payload, and the rendered user line. Each is driven
with the real objects and a down embedder (a callable that answers None, which
is what this module documents as the server-down signal). Two controls pin the
other direction -- a measured success must not be ERASED by a reset, and the
renderer must really be rendering.
"""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout


def _down_embedder(texts):
    """The embedding server never came up: every call answers None.

    memory.SessionTurnIndex documents a None return as the degrade signal
    ("server down / malformed"), so this is the wiring of a daemon whose
    :8081 embedder is not answering, not a stub of a private method.
    """
    return None


def _live_embedder(texts):
    """A server that answers -- used only by the controls."""
    return [[1.0, 0.0, 0.0] for _ in texts]


class AConversationResetIsNotAnEmbed(unittest.TestCase):
    """The index layer: clear() must not set the measured flag."""

    def _index(self, embedder):
        from intergen.memory import SessionTurnIndex
        return SessionTurnIndex(embedder=embedder)

    def test_a_conversation_reset_does_not_mark_the_embedder_verified(self):
        idx = self._index(_down_embedder)
        try:
            # One turn's query embed is attempted and fails: this is the state
            # of a daemon whose embedding server never came up.
            self.assertIsNone(idx.embed_query("what did I say about the car?"),
                              "control: the down embedder answered, so this "
                              "case is not measuring an embedder-down machine")
            self.assertTrue(idx.degraded,
                            "control: a failed query embed did not raise the "
                            "loud degraded flag")
            self.assertFalse(idx.verified,
                             "control: the index claimed a measured success "
                             "before the reset, so the reset is not what this "
                             "case would be measuring")

            # The person ends the conversation and starts a new one
            # (D-Bus ResetConversation / the browser's new_session both land
            # here via ConversationRouter.reset_conversation_state).
            idx.clear()

            self.assertFalse(
                idx.verified,
                "a conversation reset marked the embedder VERIFIED on a "
                "machine where no embed has ever succeeded: clear() performs "
                "no embed, and `verified` is documented as set only by a "
                "MEASURED success")
        finally:
            idx.stop()

    def test_a_reset_does_not_erase_a_measured_success(self):
        """CONTROL, and the boundary of the fix. That the embedder answered is
        a fact about the machine, not about the conversation, so a reset must
        leave a real verification standing."""
        import time
        idx = self._index(_live_embedder)
        try:
            idx.index_turn("what year was Linux released?", "1991.")
            deadline = time.monotonic() + 5
            while idx.indexed_count < 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(idx.indexed_count, 1,
                             "control: nothing was indexed, so there is no "
                             "measured success for the reset to keep")
            self.assertTrue(idx.verified)
            idx.clear()
            self.assertTrue(idx.verified,
                            "a reset threw away a MEASURED embed success; the "
                            "embedder answering is a fact about the machine, "
                            "not about the conversation")
        finally:
            idx.stop()

    def test_a_reset_does_not_manufacture_an_index_write(self):
        """The neighbouring measurements stay honest across the same reset:
        nothing was indexed and nothing was written, and the surface renders
        those. Pinned here so a fix to `verified` is not read as covering them
        and so the contradiction in the rendered line is visible."""
        idx = self._index(_down_embedder)
        try:
            idx.embed_query("q")
            idx.clear()
            self.assertEqual(idx.indexed_count, 0)
            self.assertEqual(idx.turns_seen, 0)
            self.assertIsNone(idx.last_indexed_at,
                              "a reset invented an index write")
        finally:
            idx.stop()


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
    """A real ConversationRouter with the collaborators status reads.

    Built the same way intergen/tests/test_status_truthfulness.py builds it, so
    the status payload here is the payload the daemon lifts.
    """
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


class TheStatusPayloadAfterAReset(unittest.TestCase):
    """The router layer: what a frontend is actually told after the reset."""

    def test_status_does_not_report_a_verified_embedder_after_a_reset(self):
        r = _router(_down_embedder)
        conv = r.new_conversation()
        try:
            self.assertIsNotNone(conv.turn_index,
                                 "control: no index was wired, so the reset "
                                 "path under test is not reachable")
            # A turn is attempted on a machine whose embedder never came up.
            conv.turn_index.embed_query("what did I say about the car?")
            with r.bind_conversation(conv):
                before = r.get_status()
            self.assertFalse(before["memory_verified"],
                             "control: status claimed a measured success "
                             "before the reset")
            self.assertTrue(before["memory_degraded"],
                            "control: the failed embed never reached status")

            # The person starts a new conversation.
            r.reset_conversation_state(conv)

            with r.bind_conversation(conv):
                after = r.get_status()
            self.assertFalse(
                after["memory_verified"],
                "after a conversation reset the status payload reports the "
                "embedder as VERIFIED on a machine where it has never "
                "answered; memory_verified is the MEASURED flag and nothing "
                "measured anything")
        finally:
            if conv.turn_index is not None:
                conv.turn_index.stop()

    def test_the_user_line_does_not_say_recall_is_active_after_a_reset(self):
        """The rendered sentence, which is what the person reads."""
        from intergen import cli
        r = _router(_down_embedder)
        conv = r.new_conversation()
        try:
            conv.turn_index.embed_query("what did I say about the car?")
            r.reset_conversation_state(conv)
            with r.bind_conversation(conv):
                rs = r.get_status()
            # The daemon's own mapping of the router payload onto the
            # first-class status field (intergen/dbus_daemon.py, the
            # status["memory_index"] block): every value comes from the real
            # router payload above.
            mem = {
                "enabled": bool(rs["memory_enabled"]),
                "degraded": bool(rs["memory_degraded"]),
                "verified": bool(rs["memory_verified"]),
                "indexed_turns": int(rs["memory_indexed_turns"]),
                "turns_seen": int(rs["memory_turns_seen"]),
                "last_index_at": rs["memory_last_index_at"],
                "embedder_answered": bool(rs["memory_verified"]),
            }
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli.print_status({"memory_index": mem, "requests_handled": 0})
            out = buf.getvalue()
            self.assertIn("InterGen Status", out,
                          "control: the renderer produced no output, so the "
                          "assertions below would pass vacuously")
            self.assertNotIn(
                "session recall active", out,
                "the status line tells the person session recall is ACTIVE "
                "after a conversation reset on a machine whose embedder has "
                "never answered")
            self.assertNotIn(
                "embedder answered this index: yes", out,
                "the status line tells the person the embedder answered this "
                "index; no embed has ever succeeded and the same line reports "
                "0 turns indexed and no index write")
        finally:
            if conv.turn_index is not None:
                conv.turn_index.stop()


if __name__ == "__main__":
    unittest.main()
