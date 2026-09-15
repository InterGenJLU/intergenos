# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A turn answered from the system-state cache records its exchange.

Every other code-owned fast path on the desktop bus writes its exchange
through the router's single history writer, which is also what hands the
exchange to the session-memory index. The state-cache path returned its
answer without writing anything: the person asked "what kernel am I
running?", was answered, and the conversation held no trace of it — no
history for a follow-up to resolve against, no memory index event for the
turn (the "a completed turn produces no index event" observation of the
2026-09-04 evaluation, for this disposition). The browser server's own
write-back hid the gap on the web surface; the bus had no such backstop.
"""

from __future__ import annotations

import time
import unittest

from intergen.interfaces.types import MessageRole
from intergen.router import ConversationRouter


class _StateCache:
    def lookup_for_query(self, query):
        return "6.18.10-igos-21" if "kernel" in query.lower() else None


def _router():
    r = ConversationRouter.__new__(ConversationRouter)
    r._max_history = 20
    r._record = lambda *a, **k: None
    r._won = lambda *a, **k: None
    r._current_query_type = "general"
    r._memory = None
    r._embedder = lambda texts: [[1.0, 0.0, 0.0] for _ in texts]
    r._state_cache = _StateCache()
    r.detach_conversation()
    return r


class CacheServedTurnWritesHistoryTests(unittest.TestCase):

    def test_the_exchange_is_recorded_once_and_indexed(self):
        r = _router()
        conv = r.new_conversation()
        try:
            with r.bind_conversation(conv):
                result = r._try_state_cache("what kernel am I running?",
                                            time.monotonic())
            self.assertIsNotNone(result)
            self.assertEqual(result.source, "cache")
            self.assertIn("6.18.10-igos-21", result.text)
            pairs = [(m.role, m.content) for m in conv.history]
            self.assertEqual(pairs, [
                (MessageRole.USER, "what kernel am I running?"),
                (MessageRole.ASSISTANT, result.text)])
            deadline = time.monotonic() + 5
            while conv.turn_index.indexed_count < 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(conv.turn_index.turns_seen, 1)
            self.assertEqual(conv.turn_index.indexed_count, 1)
        finally:
            conv.turn_index.stop()

    def test_a_miss_writes_nothing(self):
        r = _router()
        conv = r.new_conversation()
        try:
            with r.bind_conversation(conv):
                result = r._try_state_cache("who wrote Hamlet?", time.monotonic())
            self.assertIsNone(result)
            self.assertEqual(conv.history, [])
            self.assertEqual(conv.turn_index.turns_seen, 0)
        finally:
            conv.turn_index.stop()


if __name__ == "__main__":
    unittest.main()
