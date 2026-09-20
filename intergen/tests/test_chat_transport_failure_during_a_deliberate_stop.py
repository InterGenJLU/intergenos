# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""An engine that was asked to stop is not an engine that failed.

WHAT WAS MEASURED. On 2026-09-20, proving the embedding path's own version of
this defect, a run left exactly one error line behind:

    intergen.llm ERROR Local LLM request failed: Remote end closed connection
    without response

written in the same second the chat server was stopped on purpose. Nothing had
failed. It is the embedding path's finding in a different module, and it is
worse here than a noisy log line, because this handler does not only log: it
calls ``note_transport_failure()``, which records the endpoint as unreachable.
That record is what the last-resort text reads when a reply cannot be served,
so a deliberate stop makes the assistant tell the person the model server is
not running — a claim about a fault, made about a stop they asked for.

WHAT IS PINNED HERE:

  * the LEVEL — a request that dies inside a planned teardown is reported at
    INFO and names the teardown; one that dies while there was a server meant
    to answer keeps its ERROR;
  * the STATE WRITE — a planned teardown does NOT record a transport failure,
    so nothing downstream reports a stopped engine as a failed one; a genuine
    failure still records it, exactly as before;
  * the consequence the person sees — after a deliberate stop the last-resort
    text does not claim the model server is not running;
  * the row on the decision trace is still emitted either way, because the
    whole point of that row is that a model call which got no response is never
    silent; when the teardown was planned the row says so;
  * ONE helper, shared with the embedding path, not a second copy of the
    measurement;
  * nothing wired means nothing claimed: with no serving engine to ask, the
    behaviour is exactly today's.
"""

from __future__ import annotations

import ast
import inspect
import logging
import signal
import unittest
from unittest import mock

from intergen import dbus_daemon, llm as llm_module
from intergen.llama_manager import LlamaManager
from intergen.interfaces.types import Message, MessageRole
from intergen.llm import LLMRouter


def _shared_helper():
    """The shared teardown measurement, imported here rather than at module
    scope so that at the base — where it does not exist yet — every test below
    fails on its own assertion instead of the whole file failing to import."""
    from intergen import llama_manager
    return getattr(llama_manager, "planned_teardown_of", None)


class _Child:
    """The least Popen the teardown measurement reads."""

    def __init__(self, status=None):
        self.pid = 4242
        self.stdout = None
        self.stderr = None
        self.status = status

    def poll(self):
        return self.status

    def send_signal(self, _sig):
        return None

    def wait(self, timeout=None):
        return 0

    def kill(self):
        return None


def _engine_that_is_stopping() -> LlamaManager:
    mgr = LlamaManager()
    mgr.stop()                      # marks before it signals; no child to stop
    return mgr


def _engine_whose_child_took_sigterm() -> LlamaManager:
    mgr = LlamaManager()
    mgr._process = _Child(status=-signal.SIGTERM)
    return mgr


def _engine_that_means_to_serve() -> LlamaManager:
    mgr = LlamaManager()
    mgr._mark_ready()
    return mgr


class _Boom(Exception):
    pass


class TheChatTransportFailureTests(unittest.TestCase):

    def _router(self, engine):
        llm = LLMRouter(config=None)
        # Wired when the router can be told. At the BASE it cannot, and the
        # tests below then fail on the BEHAVIOUR they are about — the level and
        # the state write — rather than on a missing method, which is the red
        # this change has to answer. That the method must exist at all is
        # asserted once, on its own, further down.
        wire = getattr(llm, "set_serving_engine", None)
        if engine is not None and wire is not None:
            wire(engine)
        return llm

    def _one_failing_request(self, llm):
        """Drive one chat request whose transport dies; return its log records
        and the rows it put on the decision trace."""
        records = []
        logger = logging.getLogger("intergen.llm")
        handler = logging.Handler()
        handler.emit = records.append
        logger.addHandler(handler)
        old_level = logger.level
        logger.setLevel(logging.DEBUG)
        rows = []
        boom = _Boom("Remote end closed connection without response")
        try:
            with mock.patch("urllib.request.urlopen", side_effect=boom), \
                 mock.patch.object(llm_module.glass, "emit",
                                   side_effect=lambda *a, **k: rows.append((a, k))):
                out = list(llm.stream(
                    [Message(role=MessageRole.USER, content="hello")]))
        finally:
            logger.setLevel(old_level)
            logger.removeHandler(handler)
        self.assertEqual(out, [], "a failed request yields no tokens")
        return records, rows

    def _the_line(self, records):
        lines = [r for r in records
                 if "Remote end closed connection" in r.getMessage()]
        self.assertEqual(len(lines), 1, [r.getMessage() for r in records])
        return lines[0]

    # ---- the level ----------------------------------------------------------

    def test_with_no_engine_wired_it_is_an_error(self):
        """The control, and also today's behaviour for any caller that wires
        nothing: unknown is never read as planned."""
        llm = self._router(None)
        records, _rows = self._one_failing_request(llm)
        self.assertEqual(self._the_line(records).levelno, logging.ERROR)

    def test_with_an_engine_meant_to_serve_it_is_an_error(self):
        llm = self._router(_engine_that_means_to_serve())
        records, _rows = self._one_failing_request(llm)
        self.assertEqual(self._the_line(records).levelno, logging.ERROR)

    def test_inside_a_deliberate_stop_it_is_an_info_naming_the_teardown(self):
        llm = self._router(_engine_that_is_stopping())
        records, _rows = self._one_failing_request(llm)
        line = self._the_line(records)
        self.assertEqual(line.levelno, logging.INFO, line.getMessage())
        self.assertIn("on purpose", line.getMessage().lower())

    def test_a_child_already_gone_on_sigterm_is_an_info_too(self):
        llm = self._router(_engine_whose_child_took_sigterm())
        records, _rows = self._one_failing_request(llm)
        line = self._the_line(records)
        self.assertEqual(line.levelno, logging.INFO, line.getMessage())
        self.assertIn("SIGTERM", line.getMessage())

    # ---- the state write, which is the part that reaches the person ---------

    def test_a_deliberate_stop_does_not_mark_the_engine_failed(self):
        llm = self._router(_engine_that_is_stopping())
        self._one_failing_request(llm)
        self.assertIsNone(
            llm.transport_error,
            "a stop the machine was asked for was recorded as the endpoint "
            "being unreachable")

    def test_a_genuine_failure_still_marks_it(self):
        for engine in (None, _engine_that_means_to_serve()):
            with self.subTest(engine=type(engine).__name__):
                llm = self._router(engine)
                self._one_failing_request(llm)
                self.assertIsNotNone(llm.transport_error)

    def test_after_a_deliberate_stop_the_reply_does_not_claim_a_dead_server(self):
        """What the person actually reads."""
        llm = self._router(_engine_that_is_stopping())
        self._one_failing_request(llm)
        text = llm._servable_text("", "empty")
        self.assertEqual(text, llm._EMPTY_RESPONSE_FALLBACK)
        self.assertNotIn("not running", text.lower())

    def test_after_a_genuine_failure_the_reply_still_names_the_dead_server(self):
        llm = self._router(_engine_that_means_to_serve())
        self._one_failing_request(llm)
        self.assertEqual(llm._servable_text("", "empty"),
                         llm._MODEL_SERVER_DOWN_FALLBACK)

    # ---- the trace row is never dropped ------------------------------------

    def test_the_no_response_row_is_emitted_either_way(self):
        for engine, planned in ((None, False),
                                (_engine_that_is_stopping(), True)):
            with self.subTest(planned=planned):
                llm = self._router(engine)
                _records, rows = self._one_failing_request(llm)
                no_response = [k for a, k in rows
                               if a[:2] == ("model", "no_response")]
                self.assertEqual(len(no_response), 1, rows)
                detail = no_response[0]["detail"]
                self.assertIn("planned_teardown", detail)
                self.assertEqual(bool(detail["planned_teardown"]), planned)

    # ---- one helper, not a copy --------------------------------------------

    def test_the_chat_path_uses_the_shared_helper(self):
        src = inspect.getsource(llm_module)
        self.assertIn("planned_teardown_of", src)
        tree = ast.parse(src)
        self.assertEqual(
            [n.name for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef)
             and "planned_teardown" in n.name], [],
            "the chat path defines its own copy of the teardown measurement "
            "instead of sharing the one in llama_manager")

    def test_the_embedding_path_uses_the_same_helper(self):
        from intergen import llama_manager
        src = inspect.getsource(llama_manager)
        tree = ast.parse(src)
        defs = [n.name for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef)
                and "planned_teardown" in n.name]
        self.assertIn("planned_teardown_of", defs,
                      "the shared helper must be a module-level function both "
                      "paths can call")

    def test_the_helper_claims_nothing_about_nothing(self):
        helper = _shared_helper()
        self.assertIsNotNone(helper, "there is no shared helper to call")
        self.assertIsNone(helper(None))
        self.assertIsNone(helper(object()))

    def test_the_helper_answers_for_a_manager_directly(self):
        helper = _shared_helper()
        self.assertIsNotNone(helper, "there is no shared helper to call")
        self.assertIsNotNone(helper(_engine_that_is_stopping()))
        self.assertIsNotNone(helper(_engine_whose_child_took_sigterm()))
        self.assertIsNone(helper(_engine_that_means_to_serve()))

    def test_the_pause_path_is_the_case_that_reaches_a_person(self):
        """A PAUSE is the case where a stale "your server is down" record would
        actually be read by someone: the chat server is stopped on purpose and
        the daemon LIVES ON, so the record outlives the stop. The daemon pauses
        by calling stop() on the manager itself, which sets the mark before the
        child is signalled — so this is the case the measurement covers most
        completely, and it is pinned here rather than left to be inferred.
        """
        src = inspect.getsource(dbus_daemon)
        tree = ast.parse(src)
        pause = [n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef)
                 and "pause" in n.name.lower() and "stop" in n.name.lower()]
        self.assertTrue(pause, "no pause-stop path found in the daemon")
        body = ast.get_source_segment(src, pause[0])
        self.assertIn("_llama.stop()", body,
                      "the pause no longer stops the chat server through the "
                      "manager, so the stop mark is no longer set for it")
        # And the state that stop() leaves behind is the one the router reads.
        mgr = LlamaManager()
        mgr.stop()
        llm = self._router(mgr)
        self._one_failing_request(llm)
        self.assertIsNone(llm.transport_error)
        self.assertEqual(llm._servable_text("", "empty"),
                         llm._EMPTY_RESPONSE_FALLBACK)

    # ---- the daemon hands the router its engine ----------------------------

    def test_the_daemon_wires_the_chat_engine_into_the_router(self):
        src = inspect.getsource(dbus_daemon)
        self.assertIn("set_serving_engine", src,
                      "the daemon builds the router with the chat manager in "
                      "hand and must hand it over, or the router can never "
                      "tell a stop from a fault")

    def test_wiring_an_engine_is_optional_and_replaceable(self):
        llm = LLMRouter(config=None)
        self.assertTrue(hasattr(llm, "set_serving_engine"),
                        "LLMRouter cannot be told which engine serves it")
        self.assertIsNone(llm._serving_engine)
        mgr = _engine_that_is_stopping()
        llm.set_serving_engine(mgr)
        self.assertIs(llm._serving_engine, mgr)
        llm.set_serving_engine(None)
        self.assertIsNone(llm._serving_engine)


if __name__ == "__main__":
    unittest.main()
