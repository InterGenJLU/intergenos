# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""One mechanism writes down a model call that got no response — every path.

WHY A SECOND MODULE. ``test_zz192_p3_02.py`` pins the finding as the reading
pass stated it: a refused tool request, and a tool request answered with a
non-400 status, are recorded and traced the way the conversational path records
and traces them. It stops there, and four things the same defect owns are
outside it. They are pinned here, because a defect whose fix is "both paths now
share one method" is only closed when the method's whole contract is held on
both paths — otherwise the next edit re-opens the half nobody measured.

WHAT IS MEASURED HERE, and why each one is the same defect:

  * THE RECORD IS CLEARED BY A TOOL REQUEST THAT SUCCEEDED. The record is a
    statement about the CURRENT state ("one blip must not mark the session
    forever", llm.py's own comment), and the conversational path clears it on
    every success. The tool path never did. On the web interface a tool-enabled
    turn is the ONLY model call the turn makes, so an engine that went down and
    came back stayed recorded as unreachable for every later tool turn, and the
    serving floor kept telling the person to restart a service that was already
    running — the mirror image of the finding, with the same cause and the same
    reader. Found in the lane while fixing the recorded side; fixed in it.

  * THE RETRY AFTER A CONTEXT OVERFLOW. The 400 branch asks again with a
    trimmed payload. When that second call gets no response the turn ends with
    no tokens exactly as a refused connect does, and at the base it left a log
    line and nothing else.

  * ONE DOWN-EPISODE IS ONE EPISODE ACROSS BOTH PATHS. The dedup flag exists
    because "nothing is answering on that port" is one fact that was being
    written once per attempt. A turn asks over both paths, so a flag the tool
    path neither sets nor reads is a flag that does not do its job: the episode
    was reported once by the conversational path and then again, at ERROR, by
    every tool call in it.

  * A PLANNED TEARDOWN IS NOT A FAULT, ON THE TOOL PATH EITHER. This is the
    contract ``test_chat_transport_failure_during_a_deliberate_stop.py`` holds
    for ``stream()``. At the base the tool path never asked the question, so
    stopping the assistant mid-turn made a tool turn record the endpoint as
    unreachable and the floor then named a dead server for a stop the person
    asked for — on the one path the web interface actually takes.

  * AND THE STRUCTURAL FACT, which is the finding's cause rather than one of
    its symptoms: the three recorded things are written in exactly ONE place,
    and both paths reach it. Two copies is what produced this defect; a second
    copy would produce the next one.

WHERE THE CONTROLS ARE, and where a case cannot have one. Most assertions here
have a true-positive control on the conversational path, which held this
contract before this change: if a control fails, the assertion is measuring the
harness rather than the code. The planned-teardown class is the exception and
says so at its own cases — at the base the tool path records NOTHING for any
failure, so "a planned teardown was not recorded" is true there for the wrong
reason. Those three are named as the green no-regression legs they are, and the
red that gives them content is the genuine-failure case beside them.
"""

from __future__ import annotations

import ast
import inspect
import json
import logging
import signal
import unittest
import urllib.error
from unittest import mock

from intergen import llm as llm_module
from intergen.interfaces.types import Message, MessageRole, ToolSchema
from intergen.llama_manager import LlamaManager
from intergen.llm import LLMRouter

# The measured 2026-08-26 shape: nothing is listening, so the connect is
# refused before a byte is written.
REFUSED = urllib.error.URLError(
    ConnectionRefusedError(111, "Connection refused"))


def _http(code: int) -> urllib.error.HTTPError:
    """A status answer with no body — ``fp`` is None, so a reader of the body
    gets nothing, which is what a reader of a bodiless error gets in reality."""
    return urllib.error.HTTPError(
        "http://127.0.0.1:8080/v1/chat/completions", code,
        "status %d" % code, {}, None)


_TOOLS = [ToolSchema(name="run_command",
                     description="Run a shell command",
                     parameters={"type": "object",
                                 "properties": {"command": {"type": "string"}},
                                 "required": ["command"]})]
_MSGS = [Message(role=MessageRole.SYSTEM, content="You are InterGen."),
         Message(role=MessageRole.USER, content="is sshd enabled?")]


def _answered(text: str = "sshd is enabled.") -> list[bytes]:
    """The SSE byte lines of one ordinary completion, as both paths read them."""
    chunk = json.dumps({"choices": [{"delta": {"content": text},
                                     "finish_reason": "stop"}]})
    return [b"data: " + chunk.encode(), b"data: [DONE]"]


class _ScriptExhausted(BaseException):
    """The script ran out of outcomes before the path ran out of requests.

    A BaseException DELIBERATELY. The code under test answers every ``Exception``
    raised by the transport by recording a transport failure, so an ordinary
    error raised here would be caught by the product, recorded as a refusal, and
    the over-call would read as a clean measured failure instead of a broken
    instrument.
    """


class _Answer:
    """The least a urlopen response has to be for both paths: an iterable of
    SSE byte lines that can be closed."""

    def __init__(self, lines: list[bytes]):
        self._lines = list(lines)
        self.closed = False

    def __iter__(self):
        return iter(self._lines)

    def close(self):
        self.closed = True


class _Scripted:
    """A urlopen stand-in that plays a fixed script, one outcome per call.

    An entry is either an exception to raise or the SSE lines to answer with.
    The script is exhausted by exactly as many calls as it holds, so a path that
    asks once more than the case describes fails loudly instead of silently
    re-using the last outcome — which would make a dedup count meaningless.
    """

    def __init__(self, *outcomes):
        self._outcomes = list(outcomes)
        self.attempts = 0

    def __call__(self, *_a, **_k):
        self.attempts += 1
        if not self._outcomes:
            raise _ScriptExhausted(
                "the path made request %d; the script holds fewer outcomes"
                % self.attempts)
        out = self._outcomes.pop(0)
        if isinstance(out, BaseException):
            raise out
        return _Answer(out)

    @property
    def spent(self) -> bool:
        return not self._outcomes


class _Driven(unittest.TestCase):
    """Drive turns over either path with one scripted transport, collecting the
    log records and the decision-trace rows the run produced."""

    def _router(self, engine=None) -> LLMRouter:
        # tool_calling is the default (llm.py: config.get("tool_calling", True));
        # named so no turn below can fall through to the plain stream() path.
        llm = LLMRouter({"tool_calling": True})
        if engine is not None:
            llm.set_serving_engine(engine)
        return llm

    def _run(self, llm, transport, *, tools: bool):
        """One turn. Returns (yielded, log records, trace rows).

        The logger is captured at DEBUG because the dedup legs read the LEVEL of
        the second line, which is debug by design; the handler is removed and
        the level restored whatever the turn does.
        """
        records: list[logging.LogRecord] = []
        logger = logging.getLogger("intergen.llm")
        handler = logging.Handler()
        handler.emit = records.append
        logger.addHandler(handler)
        old_level = logger.level
        logger.setLevel(logging.DEBUG)
        rows: list[tuple] = []
        try:
            with mock.patch("urllib.request.urlopen", transport), \
                 mock.patch.object(
                     llm_module.glass, "emit",
                     side_effect=lambda *a, **k: rows.append((a, k))):
                if tools:
                    out = list(llm.stream_with_tools(_MSGS, tools=_TOOLS))
                else:
                    out = list(llm.stream(_MSGS))
        finally:
            logger.setLevel(old_level)
            logger.removeHandler(handler)
        return out, records, rows

    @staticmethod
    def _no_response(rows) -> list[dict]:
        return [k for a, k in rows if a[:2] == ("model", "no_response")]

    @staticmethod
    def _failure_lines(records) -> list[logging.LogRecord]:
        """Every line this change's one mechanism writes about a failed call,
        on any of its three levels, found by the words it writes rather than by
        a level so that a line at the WRONG level is still counted."""
        return [r for r in records
                if "Connection refused" in r.getMessage()
                or "closed connection" in r.getMessage()]


# ── the record is cleared by a tool request that reached the endpoint ────────
#
# The (b) finding of the same class, inside the same files: llm.py's tool-request
# success path.

class ARecoveredEngineIsNoLongerReportedDownTests(_Driven):

    def test_a_successful_tool_request_clears_the_record(self):
        """The engine went down, then came back, and the next tool turn is the
        turn that proves it came back."""
        llm = self._router()
        transport = _Scripted(REFUSED, _answered())
        self._run(llm, transport, tools=True)
        self.assertIsNotNone(llm.transport_error,
                             "the refused turn recorded nothing, so this case "
                             "is not measuring the clear")

        out, _records, _rows = self._run(llm, transport, tools=True)
        self.assertTrue(transport.spent, "the script was not played out")
        self.assertEqual([t for t in out if isinstance(t, str)],
                         ["sshd is enabled."],
                         "the answering tool turn produced no answer")
        self.assertIsNone(
            llm.transport_error,
            "a tool request that reached the endpoint left the endpoint "
            "recorded as unreachable, so every later turn reports an engine "
            "that is running as down")
        self.assertIsNone(llm._transport_error_kind)

    def test_after_a_successful_tool_request_the_floor_stops_naming_the_server(self):
        """What the person reads. The record is not an internal detail: it is
        the input to the one place the last-resort text is chosen."""
        llm = self._router()
        self._run(llm, _Scripted(REFUSED, _answered()), tools=True)
        self._run(llm, _Scripted(_answered()), tools=True)
        text = llm._servable_text("", "empty")
        self.assertEqual(
            text, llm._EMPTY_RESPONSE_FALLBACK,
            "after the engine answered a tool request the floor still tells "
            "the person their model server is not running")
        self.assertNotIn("isn't running", text)

    def test_the_successful_tool_request_reopens_the_down_episode(self):
        """Clearing the record and clearing the episode are one act: the NEXT
        down-episode has to be reported again, or a server that fails twice is
        reported once."""
        llm = self._router()
        self._run(llm, _Scripted(REFUSED), tools=True)
        self._run(llm, _Scripted(_answered()), tools=True)
        self.assertFalse(llm._transport_reported,
                         "the answered tool request left the previous episode "
                         "marked reported, so the next one is never reported")
        _out, records, _rows = self._run(llm, _Scripted(REFUSED), tools=True)
        lines = self._failure_lines(records)
        self.assertEqual(len(lines), 1, [r.getMessage() for r in lines])
        self.assertEqual(lines[0].levelno, logging.ERROR,
                         "the second down-episode was not reported as one")

    def test_the_conversational_path_clears_it_too(self):
        """THE CONTROL. The identical sequence over stream(), which held this
        contract before this change."""
        llm = self._router()
        transport = _Scripted(REFUSED, _answered())
        self._run(llm, transport, tools=False)
        self.assertIsNotNone(llm.transport_error)
        self._run(llm, transport, tools=False)
        self.assertIsNone(llm.transport_error)
        self.assertFalse(llm._transport_reported)


# ── the retry after a context overflow ──────────────────────────────────────

class TheRetryAfterAContextOverflowTests(_Driven):

    def test_a_failed_retry_is_recorded_and_traced(self):
        """The 400 is answered by trimming and asking again. When the second
        ask gets no response the turn ends with no tokens, and at the base that
        was a log line and nothing else."""
        llm = self._router()
        transport = _Scripted(_http(400), REFUSED)
        out, _records, rows = self._run(llm, transport, tools=True)

        self.assertEqual(transport.attempts, 2,
                         "the overflow retry did not happen, so this case is "
                         "not measuring it")
        self.assertEqual(out, [])
        self.assertIsNotNone(
            llm.transport_error,
            "the retry after a context overflow got no response and left no "
            "record of why the turn produced nothing")
        self.assertEqual(len(self._no_response(rows)), 1,
                         "no ('model', 'no_response') row for the retried "
                         "call: on the trace it reads like an answered turn")

    def test_the_failed_retry_names_the_kind_so_the_ladder_can_read_it(self):
        llm = self._router()
        self._run(llm, _Scripted(_http(400), REFUSED), tools=True)
        self.assertEqual(llm._transport_error_kind, "refused")

    def test_the_failed_retrys_log_line_says_it_was_the_retry(self):
        """One episode, one line — and the line has to be readable: a reader
        of the journal must be able to tell this from the first attempt."""
        llm = self._router()
        _out, records, _rows = self._run(
            llm, _Scripted(_http(400), REFUSED), tools=True)
        lines = self._failure_lines(records)
        self.assertEqual(len(lines), 1, [r.getMessage() for r in lines])
        self.assertIn("context overflow", lines[0].getMessage())

    def test_a_retry_that_succeeded_records_no_failure(self):
        """The no-regression leg: the branch exists because the retry usually
        works, and a working retry must leave the endpoint healthy."""
        llm = self._router()
        transport = _Scripted(_http(400), _answered())
        out, _records, rows = self._run(llm, transport, tools=True)
        self.assertTrue(transport.spent)
        self.assertEqual([t for t in out if isinstance(t, str)],
                         ["sshd is enabled."])
        self.assertIsNone(llm.transport_error,
                          "a context overflow whose retry answered was "
                          "recorded as the endpoint being unreachable")
        self.assertEqual(self._no_response(rows), [])


# ── one down-episode is one episode, over both paths ────────────────────────

class OneDownEpisodeAcrossBothPathsTests(_Driven):

    def _two_turns(self, first_tools: bool, second_tools: bool):
        llm = self._router()
        _o1, r1, rows1 = self._run(llm, _Scripted(REFUSED), tools=first_tools)
        _o2, r2, rows2 = self._run(llm, _Scripted(REFUSED), tools=second_tools)
        return llm, self._failure_lines(r1) + self._failure_lines(r2), \
            self._no_response(rows1) + self._no_response(rows2)

    def test_a_tool_failure_after_a_conversational_one_is_not_reported_again(self):
        """The order the web interface produces: the turn asks, the engine is
        down, and the tool call in the same episode must not re-report the one
        fact at ERROR."""
        _llm, lines, _rows = self._two_turns(False, True)
        self.assertEqual(len(lines), 2, [r.getMessage() for r in lines])
        self.assertEqual(lines[0].levelno, logging.ERROR)
        self.assertEqual(
            lines[1].levelno, logging.DEBUG,
            "the tool path reported the same down-episode a second time at "
            "ERROR; one stopped engine is one fact, however it was asked")

    def test_a_conversational_failure_after_a_tool_one_is_not_reported_again(self):
        """The other order, which is the one a retry ladder produces: the tool
        path has to SET the flag, not only read it."""
        _llm, lines, _rows = self._two_turns(True, False)
        self.assertEqual(len(lines), 2, [r.getMessage() for r in lines])
        self.assertEqual(lines[0].levelno, logging.ERROR)
        self.assertEqual(
            lines[1].levelno, logging.DEBUG,
            "the tool path did not mark the episode reported, so the next "
            "path reported it again")

    def test_two_tool_failures_are_one_reported_episode(self):
        _llm, lines, _rows = self._two_turns(True, True)
        self.assertEqual(len(lines), 2, [r.getMessage() for r in lines])
        self.assertEqual([lines[0].levelno, lines[1].levelno],
                         [logging.ERROR, logging.DEBUG])

    def test_the_record_is_not_deduplicated_only_the_log_is(self):
        """The counts differ on purpose: a reader counting ATTEMPTS counts rows,
        and a reader of the journal reads one line per episode. Pinned so a
        later tightening of the log cannot quietly take the rows with it."""
        for first, second in ((False, True), (True, False), (True, True)):
            with self.subTest(first_tools=first, second_tools=second):
                _llm, _lines, rows = self._two_turns(first, second)
                self.assertEqual(len(rows), 2,
                                 "two calls got no response and the trace "
                                 "carries fewer than two rows")

    def test_the_conversational_path_alone_still_dedups(self):
        """THE CONTROL: the behaviour that held before this change, unchanged
        by the move into one method."""
        _llm, lines, _rows = self._two_turns(False, False)
        self.assertEqual(len(lines), 2, [r.getMessage() for r in lines])
        self.assertEqual([lines[0].levelno, lines[1].levelno],
                         [logging.ERROR, logging.DEBUG])


# ── a planned teardown is not a fault on the tool path either ───────────────

class _Child:
    """The least Popen the shared teardown measurement reads."""

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


class APlannedTeardownOnTheToolPathTests(_Driven):
    """``test_chat_transport_failure_during_a_deliberate_stop.py`` holds this
    contract for ``stream()``. The web interface takes the TOOL path, so until
    the question is asked there too, the case that reaches a person — a pause,
    where the chat server stops and the daemon lives on — is unmeasured on the
    only path it happens over."""

    # WHAT THESE THREE CAN AND CANNOT MEASURE, said plainly. At the BASE the
    # tool path records nothing for ANY failure, so "a planned teardown
    # recorded nothing" is trivially true there and these three pass at the
    # base for a reason that has nothing to do with the teardown. They are not
    # reds and they are not claimed as reds. Their meaning comes from standing
    # beside test_an_engine_that_means_to_serve_still_fails_loudly, which is RED
    # at the base: once a genuine failure IS recorded, "the planned one still is
    # not" is a statement with content, and that pair is the contract.

    def test_a_stopping_engine_records_no_transport_failure(self):
        llm = self._router(_engine_that_is_stopping())
        self._run(llm, _Scripted(REFUSED), tools=True)
        self.assertIsNone(
            llm.transport_error,
            "a tool turn that died inside a stop the machine was asked for "
            "recorded the endpoint as unreachable")

    def test_a_child_already_gone_on_sigterm_records_nothing_either(self):
        llm = self._router(_engine_whose_child_took_sigterm())
        self._run(llm, _Scripted(REFUSED), tools=True)
        self.assertIsNone(llm.transport_error)

    def test_a_stopping_engine_is_reported_at_info_naming_the_teardown(self):
        llm = self._router(_engine_that_is_stopping())
        _out, records, _rows = self._run(llm, _Scripted(REFUSED), tools=True)
        lines = self._failure_lines(records)
        self.assertEqual(len(lines), 1, [r.getMessage() for r in lines])
        self.assertEqual(lines[0].levelno, logging.INFO,
                         lines[0].getMessage())
        self.assertIn("on purpose", lines[0].getMessage().lower())

    def test_after_a_stop_the_floor_does_not_claim_a_dead_server(self):
        """The consequence the person sees, on the path the web interface uses."""
        llm = self._router(_engine_that_is_stopping())
        self._run(llm, _Scripted(REFUSED), tools=True)
        text = llm._servable_text("", "empty")
        self.assertEqual(text, llm._EMPTY_RESPONSE_FALLBACK)
        self.assertNotIn("isn't running", text)

    def test_the_row_is_still_written_and_says_the_teardown_was_planned(self):
        """A model call that got no response is never silent, planned or not —
        and a reader of the trace can tell the two apart."""
        for engine, planned in ((None, False),
                                (_engine_that_is_stopping(), True)):
            with self.subTest(planned=planned):
                llm = self._router(engine)
                _out, _records, rows = self._run(
                    llm, _Scripted(REFUSED), tools=True)
                row = self._no_response(rows)
                self.assertEqual(len(row), 1, rows)
                detail = row[0]["detail"]
                self.assertIn("planned_teardown", detail)
                self.assertEqual(bool(detail["planned_teardown"]), planned)

    def test_an_engine_that_means_to_serve_still_fails_loudly(self):
        """THE OTHER HALF of the pair above, and a RED at the base rather than
        a control: it asserts both that a genuine failure keeps its ERROR line
        (which the base already did) and that it is RECORDED (which the base's
        tool path did not do at all). Unknown is never read as planned, and the
        three "records nothing" cases above only mean something once this one
        holds."""
        for engine in (None, _engine_that_means_to_serve()):
            with self.subTest(engine=type(engine).__name__):
                llm = self._router(engine)
                _out, records, _rows = self._run(
                    llm, _Scripted(REFUSED), tools=True)
                lines = self._failure_lines(records)
                self.assertEqual(len(lines), 1, [r.getMessage() for r in lines])
                self.assertEqual(lines[0].levelno, logging.ERROR)
                self.assertIsNotNone(llm.transport_error)

    def test_the_retry_exit_asks_the_same_question(self):
        """The branch is easy to miss when the question is added path by path
        instead of once: a planned stop that kills the retry is still a planned
        stop."""
        llm = self._router(_engine_that_is_stopping())
        _out, records, _rows = self._run(
            llm, _Scripted(_http(400), REFUSED), tools=True)
        self.assertIsNone(llm.transport_error)
        lines = self._failure_lines(records)
        self.assertEqual(len(lines), 1, [r.getMessage() for r in lines])
        self.assertEqual(lines[0].levelno, logging.INFO)


# ── one mechanism, not two copies: the cause rather than the symptoms ───────

class OneMechanismNotTwoCopiesTests(unittest.TestCase):
    """This defect was not a forgotten line. It was the same three
    obligations written out inside ONE path, where a second path could be
    added — and was — without them. Pinned structurally so the next edit
    cannot re-open it by copying rather than calling."""

    def setUp(self):
        self.src = inspect.getsource(llm_module)
        self.tree = ast.parse(self.src)
        self.funcs = [n for n in ast.walk(self.tree)
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

    def _bodies_containing(self, needle: str) -> list[str]:
        """The names of the functions whose OWN source contains `needle`.

        Read off the module's syntax tree, never off this module's own text, so
        the instrument cannot match its own caller: the strings below appear in
        this file too, and a text search over the repository would find them.
        """
        found = []
        for fn in self.funcs:
            seg = ast.get_source_segment(self.src, fn) or ""
            if needle in seg:
                found.append(fn.name)
        return found

    def test_the_trace_row_is_emitted_in_exactly_one_place(self):
        emitters = self._bodies_containing('"no_response"')
        self.assertEqual(
            len(emitters), 1,
            "the ('model', 'no_response') row is written in %d places: %r. "
            "Two copies of this obligation is what let a whole serving path "
            "ship without it." % (len(emitters), emitters))

    def test_the_transport_failure_is_recorded_in_exactly_one_place(self):
        recorders = [n for n in self._bodies_containing(
            "note_transport_failure") if n != "note_transport_failure"]
        self.assertEqual(
            len(recorders), 1,
            "the transport failure is recorded from %d places: %r"
            % (len(recorders), recorders))

    def test_both_serving_paths_reach_that_one_place(self):
        """The method is only a fix if the paths call it."""
        emitters = self._bodies_containing('"no_response"')
        self.assertEqual(len(emitters), 1, emitters)
        mechanism = emitters[0]
        by_name = {fn.name: fn for fn in self.funcs}
        for path in ("stream", "stream_with_tools"):
            self.assertIn(path, by_name, "llm.py has no %s()" % path)
            seg = ast.get_source_segment(self.src, by_name[path]) or ""
            self.assertIn(
                mechanism, seg,
                "%s() does not reach %s(), so a model call that got no "
                "response on that path is recorded by nothing"
                % (path, mechanism))

    def test_the_tool_path_clears_the_record_on_success(self):
        """The other half of the pair, structurally: both paths note the
        success as well as the failure."""
        by_name = {fn.name: fn for fn in self.funcs}
        for path in ("stream", "stream_with_tools"):
            seg = ast.get_source_segment(self.src, by_name[path]) or ""
            self.assertIn(
                "note_transport_ok", seg,
                "%s() never clears the record, so an engine that came back "
                "stays reported as down" % path)

    def test_the_mechanism_still_shares_the_teardown_measurement(self):
        """Carried over from the contract this change must not break: the
        measurement lives in llama_manager and is asked, not re-derived."""
        self.assertIn("planned_teardown_of", self.src)
        self.assertEqual(
            [fn.name for fn in self.funcs if "planned_teardown" in fn.name],
            [], "llm.py grew its own copy of the teardown measurement")


if __name__ == "__main__":
    unittest.main()
