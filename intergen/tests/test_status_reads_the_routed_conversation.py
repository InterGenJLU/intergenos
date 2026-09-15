# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The daemon's status reports the conversation its turns are routed on, and
a delivered turn produces the memory index events.

The defect (seen on an installed machine 2026-09-04, after two successful
index writes): status reported no bound conversation, zero history and an
unverified memory index. ``bind_conversation`` binds a conversation on the
calling THREAD — the binding ``route()`` uses — while ``get_status`` read the
router's own attribute, which the daemon detaches at start-up. The two read
different places, so the daemon bound its conversation around the status
call exactly as it does around routing and still got nothing back.

What is pinned here, in-process, on a daemon built without its heavy
start-up and a real router whose ``route()`` is replaced by a verdict that
writes the exchange through the real ``_append_history`` under the same
binding ``route()`` takes:

  * after one ``ask``, ``status`` reports the routed conversation: bound,
    history of two, one turn handed to the index and indexed, memory
    verified — RED at the base (unbound, zero, unverified), GREEN after;
  * the turn produced the memory index events (index_enqueue on the turn's
    own thread, indexed from the worker) — the "a completed turn produces no
    index event" observation of the same evaluation, measured here on the
    delivery path that writes history.
"""

from __future__ import annotations

import json
import threading
import time
import unittest
from unittest import mock

from intergen import glass
from intergen.dbus_daemon import InterGenDaemon
from intergen.interfaces.types import AnswerLinkage, RouteResult
from intergen.router import ConversationRouter


def _embed(texts):
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


def _router():
    r = ConversationRouter.__new__(ConversationRouter)
    r._max_history = 20
    r._record = lambda *a, **k: None
    r._current_query_type = "general"
    r._memory = None
    r._embedder = _embed
    r._tools = _Tools()
    r._semantic = _Semantic()
    r._llm = _LLM()
    r._metrics = None
    r.detach_conversation()

    def route(message, *, conversation=None, review_callback=None, **kw):
        # What every code-owned route does: bind the named conversation for
        # the turn and write the exchange through the single history writer.
        with r.bind_conversation(conversation):
            answer = "Linux was first released in 1991."
            r._append_history(message, answer)
        return RouteResult(text=answer, source="direct_answer", handled=True,
                           answer_linkage=AnswerLinkage(kind="code",
                                                        renderer="test"))

    r.route = route
    return r


def _daemon(router):
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
    d._review_callback_override = None
    d._paused = False
    d._pause_holds = []
    d._conversation = router.new_conversation()
    return d


class StatusReadsTheRoutedConversationTests(unittest.TestCase):

    def setUp(self):
        self.router = _router()
        self.daemon = _daemon(self.router)
        self.addCleanup(lambda: self.daemon._conversation.turn_index.stop())

    def _ask_and_wait(self, message):
        events = []
        lock = threading.Lock()
        real_emit = glass.emit

        def record(phase, event, *a, **k):
            with lock:
                events.append((phase, event))
            return real_emit(phase, event, *a, **k)

        with mock.patch.object(glass, "emit", side_effect=record):
            reply = json.loads(self.daemon.ask(message))
            index = self.daemon._conversation.turn_index
            deadline = time.monotonic() + 5
            while index.indexed_count < 1 and time.monotonic() < deadline:
                time.sleep(0.01)
        return reply, events

    def test_status_reports_the_conversation_the_turn_was_routed_on(self):
        reply, _events = self._ask_and_wait("What year was Linux first released?")
        self.assertEqual(reply["source"], "direct_answer")
        status = json.loads(self.daemon.status())
        rs = status["router_status"]
        self.assertTrue(rs["conversation_bound"], rs)
        self.assertEqual(rs["history_length"], 2, rs)
        mem = status["memory_index"]
        self.assertTrue(mem["enabled"])
        self.assertTrue(mem["verified"], mem)
        self.assertEqual(mem["indexed_turns"], 1, mem)
        self.assertEqual(mem["turns_seen"], 1, mem)
        self.assertIsNotNone(mem["last_index_at"])
        self.assertTrue(mem["embedder_answered"])

    def test_a_delivered_turn_produces_the_index_events(self):
        _reply, events = self._ask_and_wait("What year was Linux first released?")
        self.assertIn(("memory", "index_enqueue"), events)
        self.assertIn(("memory", "indexed"), events)
        self.assertIn(("decision", "history_write"), events)


if __name__ == "__main__":
    unittest.main()
