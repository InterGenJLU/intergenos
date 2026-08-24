# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""REC-17 — a turn's glass record must be JOINABLE and must TERMINATE.

Two defects, both measured on the shipped code path before this file existed
(evidence/stepA-measure/a-shipped-measurement.log):

  1. THE PATH IS NOT JOINABLE. web_server hands the router to a worker thread
     with ``loop.run_in_executor(None, lambda: self._router.route(...))`` and no
     context bind. A ContextVar does not cross a thread, so every row the router
     writes lands with ``turn_id`` = "no-turn" and cannot be joined to the turn
     that caused it. Measured: the LLM handoff — the one site that DOES bind —
     comes back correct in the same run, so this is a per-site omission and not
     a property of the mechanism.

  2. THE TURN NEED NOT TERMINATE. "delivery/final" is emitted on the three happy
     exits. A turn that crashes reaches the no-wedge backstop, which sends the
     client an error and emits NOTHING; a turn refused early for an empty
     message or an unavailable router returns before any terminal row. So a
     reader can see a turn begin and never learn how it ended, which is the
     shape REC-17 names — "no guaranteed terminal event".

WHAT IS ASSERTED HERE, and where.

The terminal guarantee belongs in glass.turn(), not in each interface's exit
paths. Every interface — web, dbus, and whatever comes next — passes through
that one context manager, and an invariant that each call site must remember is
an invariant that the next call site forgets. So the tests below drive the
CONTRACT: a turn that ends any way at all leaves exactly one terminal row, and a
second terminal for the same turn cannot be written.

The joinability half cannot be fixed inside glass — a ContextVar genuinely
cannot cross a thread, and pretending otherwise would be the wrong fix. It is
fixed at the handoffs, and guarded here by a structural gate over web_server's
own source that fails when a thread handoff does not carry the context. That
gate carries a true-positive control, because a gate that has only ever seen
correct code has not been shown to detect incorrect code.

NOTE ON THE ROUTE DEADLINE. Its terminal kind, "timeout", is exercised against
its real producer here: TheShippedWebPathTerminatesAndJoins drives web_server's
own _run_turn with its own deadline code and reads back the glass file that run
wrote. An earlier revision of this file could not do that, because the deadline
was not in the base it was written against; it is in the base now, so the
vocabulary test below is backed by a test that drives the producer rather than
standing alone.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import intergen.glass as glass


def _reset(tmp: str) -> None:
    os.environ["XDG_STATE_HOME"] = tmp
    os.environ.pop("INTERGEN_GLASS", None)
    glass._glass = None


def _rows(tmp: str) -> list[dict]:
    p = Path(tmp) / "intergen" / "glass.jsonl"
    if not p.exists():
        return []
    with open(p) as f:
        return [json.loads(x) for x in f]


class EveryTurnReachesExactlyOneTerminalEvent(unittest.TestCase):
    """The invariant REC-17 asks for, at the one place every interface passes."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="glass-lifecycle-")
        _reset(self.tmp)

    def _terminals(self, turn_id: str) -> list[dict]:
        return [r for r in _rows(self.tmp)
                if r.get("turn_id") == turn_id
                and glass.is_terminal_event(r.get("phase", ""), r.get("event", ""))]

    def test_a_turn_that_delivers_has_exactly_one_terminal(self) -> None:
        tid = glass.new_turn_id()
        with glass.turn(tid, "web"):
            glass.emit("route", "turn_start", detail={})
            glass.emit("delivery", "final", detail={"text": "hi"})
        self.assertEqual(len(self._terminals(tid)), 1)

    def test_a_turn_that_raises_still_terminates(self) -> None:
        """The no-wedge backstop's blind spot. The exception is re-raised
        unchanged — recording the end must not swallow the failure."""
        tid = glass.new_turn_id()
        with self.assertRaises(RuntimeError):
            with glass.turn(tid, "web"):
                glass.emit("route", "turn_start", detail={})
                raise RuntimeError("turn blew up")
        terminals = self._terminals(tid)
        self.assertEqual(len(terminals), 1, _rows(self.tmp))
        self.assertEqual(terminals[0]["event"], "error")

    def test_a_turn_that_is_cancelled_still_terminates(self) -> None:
        """A client that disappears mid-turn is an OUTCOME, not an absence of
        one. The record must say the turn was cancelled rather than stop."""
        tid = glass.new_turn_id()
        with self.assertRaises(asyncio.CancelledError):
            with glass.turn(tid, "web"):
                glass.emit("route", "turn_start", detail={})
                raise asyncio.CancelledError()
        terminals = self._terminals(tid)
        self.assertEqual(len(terminals), 1)
        self.assertEqual(terminals[0]["event"], "cancelled")

    def test_a_turn_that_returns_early_still_terminates(self) -> None:
        """The empty-message and router-unavailable refusals: the turn ends
        without ever reaching a delivery, and the reader is still owed the end."""
        tid = glass.new_turn_id()
        with glass.turn(tid, "web"):
            glass.emit("route", "turn_start", detail={})
        terminals = self._terminals(tid)
        self.assertEqual(len(terminals), 1)
        self.assertEqual(terminals[0]["event"], "unreported")

    def test_the_fallback_says_it_is_a_fallback(self) -> None:
        """A terminal nobody asked for must be distinguishable from one a call
        site meant. Otherwise the gate turns a silent gap into a silent pass."""
        tid = glass.new_turn_id()
        with glass.turn(tid, "web"):
            glass.emit("route", "turn_start", detail={})
        row = self._terminals(tid)[0]
        self.assertTrue(row["detail"].get("synthesized"), row)

    def test_a_second_terminal_for_one_turn_is_refused(self) -> None:
        """EXACTLY one. Two 'final' rows for one turn is the same joinability
        failure from the other end — a reader cannot tell which one ended it."""
        tid = glass.new_turn_id()
        with glass.turn(tid, "web"):
            glass.emit("delivery", "final", detail={"text": "first"})
            glass.emit("delivery", "final", detail={"text": "second"})
        terminals = self._terminals(tid)
        self.assertEqual(len(terminals), 1, terminals)
        self.assertEqual(terminals[0]["detail"].get("text"), "first")

    def test_a_refused_second_terminal_is_recorded_not_dropped(self) -> None:
        """Refusing it silently would hide a real defect in a call site. The
        attempt is kept as an ordinary row so it can be found."""
        tid = glass.new_turn_id()
        with glass.turn(tid, "web"):
            glass.emit("delivery", "final", detail={"text": "first"})
            glass.emit("delivery", "final", detail={"text": "second"})
        extra = [r for r in _rows(self.tmp)
                 if r.get("event") == "terminal_after_terminal"]
        self.assertEqual(len(extra), 1, _rows(self.tmp))

    def test_concurrent_turns_each_get_exactly_one_terminal(self) -> None:
        """The concurrent-lifecycle matrix. Turns overlap on the event loop, so
        a per-turn invariant implemented with process-wide state would pass
        every test above and fail here."""
        async def one(kind: str) -> str:
            tid = glass.new_turn_id()
            with glass.turn(tid, "web"):
                glass.emit("route", "turn_start", detail={})
                await asyncio.sleep(0)
                if kind == "deliver":
                    glass.emit("delivery", "final", detail={})
                elif kind == "raise":
                    try:
                        raise RuntimeError("x")
                    except RuntimeError:
                        pass
                    glass.emit("delivery", "final", detail={})
                # "silent" falls out with no terminal at all
            return tid

        async def run() -> list[str]:
            return await asyncio.gather(
                *(one(k) for k in
                  ("deliver", "silent", "raise", "silent", "deliver")))

        tids = asyncio.run(run())
        self.assertEqual(len(set(tids)), 5, "turn ids collided")
        for tid in tids:
            with self.subTest(turn=tid):
                self.assertEqual(len(self._terminals(tid)), 1)

    def test_a_turn_is_joinable_from_its_id_alone(self) -> None:
        """What the invariant is FOR: one id recovers the whole turn, start to
        end, with nothing of that turn left outside it."""
        tid = glass.new_turn_id()
        with glass.turn(tid, "web"):
            glass.emit("route", "turn_start", detail={})
            glass.emit("route", "verdict", detail={})
            glass.emit("delivery", "final", detail={})
        mine = [r for r in _rows(self.tmp) if r.get("turn_id") == tid]
        self.assertEqual([r["event"] for r in mine],
                         ["turn_start", "verdict", "final"])
        self.assertEqual(
            [r for r in _rows(self.tmp) if r.get("turn_id") == "no-turn"], [],
            "a row of this turn was written outside it")


class TerminalVocabulary(unittest.TestCase):
    """The kinds an interface may end a turn with, named in one place."""

    def test_the_happy_delivery_is_terminal(self) -> None:
        self.assertTrue(glass.is_terminal_event("delivery", "final"))

    def test_a_refusal_is_terminal(self) -> None:
        self.assertTrue(glass.is_terminal_event("delivery", "refused"))

    def test_an_error_is_terminal(self) -> None:
        self.assertTrue(glass.is_terminal_event("delivery", "error"))

    def test_a_timeout_is_terminal(self) -> None:
        """The name half. Its producer — the web server's route deadline — is
        driven for real in TheShippedWebPathTerminatesAndJoins below."""
        self.assertTrue(glass.is_terminal_event("delivery", "timeout"))

    def test_an_ordinary_row_is_not_terminal(self) -> None:
        self.assertFalse(glass.is_terminal_event("route", "verdict"))
        self.assertFalse(glass.is_terminal_event("delivery",
                                                 "dispatch_unconsumed"))


_WEB_SERVER = Path(__file__).resolve().parents[1] / "web_server.py"


class EveryThreadHandoffCarriesTheTurn(unittest.TestCase):
    """The joinability half — structural, because it is a property of the CALL.

    A ContextVar cannot cross a thread; that is the mechanism working as
    designed. So the thing to check is that every place web_server hands work to
    a thread carries the context with it. Checked by parsing, not by grepping:
    the shapes are `loop.run_in_executor(...)` and `executor.submit(...)`, and a
    substring search cannot tell a bound call from an unbound one.
    """

    @staticmethod
    def _unbound_handoffs(tree: ast.AST) -> list[int]:
        """Line numbers of thread handoffs whose callable is not context-bound.

        BOUND means the submitted callable routes through a captured context —
        `ctx.run(...)` inside the lambda, or a `functools.partial(ctx.run, ...)`.
        Anything else hands a bare callable to a thread, where the turn id is
        gone by construction.
        """
        bad: list[int] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not isinstance(fn, ast.Attribute):
                continue
            if fn.attr not in ("run_in_executor", "submit"):
                continue
            # The submitted callable: run_in_executor(executor, fn, *args)
            # and submit(fn, *args).
            args = node.args[1:] if fn.attr == "run_in_executor" else node.args
            if not args:
                continue
            if not EveryThreadHandoffCarriesTheTurn._is_bound(args[0]):
                bad.append(node.lineno)
        return bad

    @staticmethod
    def _is_bound(node: ast.AST) -> bool:
        """Two accepted shapes, and no others.

        A call to ``<something>.run(...)`` anywhere inside the submitted
        callable, or ``partial(<something>.run, ...)`` where the bound method is
        handed over without being called yet. The second shape was named in this
        class's docstring before it was implemented here, which would have made
        the gate REFUSE a correctly bound handoff — a gate that rejects correct
        code is as much a defect as one that accepts incorrect code, so both
        shapes now have a control below.
        """
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            fn = inner.func
            if isinstance(fn, ast.Attribute) and fn.attr == "run":
                return True
            name = (fn.attr if isinstance(fn, ast.Attribute)
                    else fn.id if isinstance(fn, ast.Name) else "")
            if name == "partial" and inner.args:
                first = inner.args[0]
                if isinstance(first, ast.Attribute) and first.attr == "run":
                    return True
        return False

    def test_no_thread_handoff_loses_the_turn_context(self) -> None:
        tree = ast.parse(_WEB_SERVER.read_text())
        bad = self._unbound_handoffs(tree)
        self.assertEqual(
            bad, [],
            f"web_server.py hands work to a thread without carrying the glass "
            f"turn context at line(s) {bad}; every row those calls write lands "
            f"as 'no-turn' and cannot be joined to the turn that caused it",
        )

    def test_the_scanner_detects_an_unbound_handoff(self) -> None:
        """TRUE-POSITIVE CONTROL. A gate that has only ever seen correct code
        has not been shown to detect incorrect code."""
        planted = ast.parse(
            "loop.run_in_executor(None, lambda: router.route(msg))")
        self.assertEqual(self._unbound_handoffs(planted), [1])

    def test_the_scanner_accepts_a_bound_handoff(self) -> None:
        """The other half of the control: it must not simply refuse everything."""
        planted = ast.parse(
            "loop.run_in_executor(None, lambda: ctx.run(router.route, msg))")
        self.assertEqual(self._unbound_handoffs(planted), [])

    def test_the_scanner_accepts_a_partial_bound_handoff(self) -> None:
        """The second shape the docstring names. A gate that refuses correctly
        bound code would push authors toward working around it."""
        planted = ast.parse(
            "executor.submit(functools.partial(ctx.run, router.route, msg))")
        self.assertEqual(self._unbound_handoffs(planted), [])

    def test_the_scanner_rejects_a_partial_that_binds_nothing(self) -> None:
        """The control for the control: partial alone is not a binding."""
        planted = ast.parse(
            "executor.submit(functools.partial(router.route, msg))")
        self.assertEqual(self._unbound_handoffs(planted), [1])

    def test_a_bound_handoff_really_carries_the_turn_id(self) -> None:
        """The structural gate above asserts the SHAPE. This asserts the shape
        actually does the job, so the two together mean something."""
        tmp = tempfile.mkdtemp(prefix="glass-handoff-")
        _reset(tmp)

        async def run() -> str:
            loop = asyncio.get_running_loop()
            tid = glass.new_turn_id()
            with glass.turn(tid, "web"):
                bound = glass.bind_context()
                await loop.run_in_executor(
                    None, lambda: bound.run(glass.emit, "route", "verdict",
                                            detail={}))
                glass.emit("delivery", "final", detail={})
            return tid

        tid = asyncio.run(run())
        verdict = [r for r in _rows(tmp) if r.get("event") == "verdict"]
        self.assertEqual(len(verdict), 1)
        self.assertEqual(verdict[0]["turn_id"], tid)


class _RecordingWS:
    """A WebSocket stand-in that records the frames a turn sent."""

    def __init__(self) -> None:
        self.frames: list[dict] = []
        self.closed = False

    async def send_json(self, obj: dict) -> None:
        self.frames.append(obj)

    async def close(self, *a, **k) -> None:
        self.closed = True

    def turn_id(self) -> str:
        for f in self.frames:
            if f.get("type") == "turn_ack":
                return f.get("turn_id", "")
        raise AssertionError(f"no turn_ack frame was sent: {self.frames}")


class _Result:
    """The shape _handle_client_message expects back from route()."""

    def __init__(self, source: str = "keyword", text: str = "an answer"):
        self.source = source
        self.text = text
        self.handled = True
        self.tool_results: list = []
        self.full_output = ""
        self.reoffer_reminder = None
        self.answer_linkage = None


class _EmittingRouter:
    """A router that WRITES A GLASS ROW from inside the worker thread.

    That row is the whole point: it is written where a ContextVar does not
    reach, so whether it carries the turn id is a fact about the real handoff in
    web_server, not about a fixture.
    """

    def __init__(self, stall_s: float = 0.0, source: str = "keyword"):
        self._stall = stall_s
        self._source = source
        self.route_calls = 0

    def route(self, user_msg, decide_only=True, review_callback=None):
        self.route_calls += 1
        glass.emit("route", "verdict", detail={"where": "worker thread"})
        if self._stall:
            time.sleep(self._stall)
        return _Result(source=self._source)

    def last_route_confidence(self):
        return None

    def _append_history(self, *a, **k):
        return None


class TheShippedWebPathTerminatesAndJoins(unittest.TestCase):
    """The invariant driven through the REAL server, not a model of it.

    Everything above this class tests glass and a parsed source file. These
    tests run web_server's own turn, with its own deadline code, and read the
    glass file it wrote. A fixture agreeing with itself is not evidence that the
    shipped path holds.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="glass-webpath-")
        _reset(self.tmp)
        # Imported here rather than at module scope: the modules above are pure,
        # and a failure to import the web stack should name THESE tests.
        from intergen import web_server as web_server_module
        self.web_server_module = web_server_module

    def _terminals(self, turn_id: str) -> list[dict]:
        return [r for r in _rows(self.tmp)
                if r.get("turn_id") == turn_id
                and glass.is_terminal_event(r.get("phase", ""), r.get("event", ""))]

    def _turn(self, router, deadline_s: float | None = None,
              wait_s: float = 5.0) -> "_RecordingWS":
        mod = self.web_server_module
        ws = _RecordingWS()

        async def scenario() -> None:
            server = mod.WebServer()
            server._router = router
            ctx = mod.ConnectionContext(client_id="glass-lifecycle-test",
                                        source_interface="web", ws=ws)
            await asyncio.wait_for(
                server._run_turn(ctx, {"type": "message",
                                       "content": "how much memory is there"}),
                timeout=wait_s)

        original = getattr(mod, "SERVER_ROUTE_DEADLINE_S", None)
        if deadline_s is not None:
            mod.SERVER_ROUTE_DEADLINE_S = deadline_s
        try:
            asyncio.run(scenario())
        finally:
            if deadline_s is not None:
                mod.SERVER_ROUTE_DEADLINE_S = original
        return ws

    def test_the_route_deadline_ends_the_turn_as_a_timeout(self) -> None:
        """The one terminal kind the earlier vocabulary test could only assert
        the NAME of. Its producer is in this tree now, so it is exercised: a
        routing stall past the server's own deadline must leave exactly one
        terminal row, and it must read "timeout" rather than the word for an
        ending nobody accounted for."""
        ws = self._turn(_EmittingRouter(stall_s=3.0), deadline_s=0.2,
                        wait_s=2.0)
        tid = ws.turn_id()
        terminals = self._terminals(tid)
        self.assertEqual(len(terminals), 1, _rows(self.tmp))
        self.assertEqual(terminals[0]["event"], "timeout")
        self.assertFalse(terminals[0]["detail"].get("synthesized"),
                         "the deadline is an accounted-for ending; a "
                         "synthesized row here means the site stayed silent")

    def test_the_deadline_turn_is_still_joinable_to_its_router_row(self) -> None:
        """The other half of REC-17 on the same real turn: the row the router
        wrote from the worker thread carries the turn id."""
        ws = self._turn(_EmittingRouter(stall_s=3.0), deadline_s=0.2,
                        wait_s=2.0)
        tid = ws.turn_id()
        verdicts = [r for r in _rows(self.tmp) if r.get("event") == "verdict"]
        self.assertEqual(len(verdicts), 1, _rows(self.tmp))
        self.assertEqual(
            verdicts[0]["turn_id"], tid,
            "the router's row did not join the turn — the thread handoff is "
            "not carrying the context")

    def test_a_delivered_turn_has_one_terminal_and_a_joined_router_row(self) -> None:
        """The happy path through the same shipped code, so the deadline test
        above is not the only evidence the handoff bind works."""
        ws = self._turn(_EmittingRouter())
        tid = ws.turn_id()
        terminals = self._terminals(tid)
        self.assertEqual(len(terminals), 1, _rows(self.tmp))
        self.assertEqual(terminals[0]["event"], "final")
        self.assertEqual(
            [r for r in _rows(self.tmp) if r.get("turn_id") == "no-turn"], [],
            "a row of this turn was written outside it")

    def test_a_crashing_turn_leaves_one_error_terminal(self) -> None:
        """web_server's no-wedge backstop answers the client and emits nothing
        of its own; the record must still say the turn failed."""
        mod = self.web_server_module
        ws = _RecordingWS()

        async def scenario() -> None:
            server = mod.WebServer()

            async def exploding_body(c, data):
                raise RuntimeError("turn body blew up")

            server._handle_client_message = exploding_body
            ctx = mod.ConnectionContext(client_id="glass-lifecycle-test",
                                        source_interface="web", ws=ws)
            await asyncio.wait_for(
                server._run_turn(ctx, {"type": "message", "content": "x"}),
                timeout=5.0)

        asyncio.run(scenario())
        tid = ws.turn_id()
        terminals = self._terminals(tid)
        self.assertEqual(len(terminals), 1, _rows(self.tmp))
        self.assertEqual(terminals[0]["event"], "error")
        self.assertEqual(terminals[0]["detail"].get("exception"),
                         "RuntimeError")



class TheTerminalHoldsAcrossTheThreadSeam(unittest.TestCase):
    """The seam the shared cell exists for, exercised rather than argued.

    bind_context() copies the context for a worker thread, and a copy carries
    VALUES. If the turn-ended flag were a plain bool, a terminal written in the
    worker thread would set it in the copy only; the turn's own exit would still
    read False and would synthesize a second ending — two endings for one turn,
    in exactly the place the bind exists to hold together. These tests write the
    terminal from the thread, and then from eight threads at once.

    Each thread gets its OWN bind. A Context cannot be entered from two places
    at once — contextvars refuses it — so sharing one snapshot across racing
    threads would fail on that rather than on the property under test. The
    turn-ended cell is shared between those snapshots regardless, because a
    context copy copies the mapping and the cell is the same object in each.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="glass-threadseam-")
        _reset(self.tmp)

    def _terminals(self, turn_id: str) -> list[dict]:
        return [r for r in _rows(self.tmp)
                if r.get("turn_id") == turn_id
                and glass.is_terminal_event(r.get("phase", ""), r.get("event", ""))]

    def test_a_terminal_written_in_a_worker_thread_counts_as_the_ending(self) -> None:
        tid = glass.new_turn_id()
        with glass.turn(tid, "web"):
            bound = glass.bind_context()
            with ThreadPoolExecutor(max_workers=1) as ex:
                ex.submit(lambda: bound.run(
                    glass.emit, "delivery", "final",
                    detail={"text": "from the thread"})).result()
        terminals = self._terminals(tid)
        self.assertEqual(len(terminals), 1, _rows(self.tmp))
        self.assertEqual(terminals[0]["event"], "final")
        self.assertFalse(
            terminals[0]["detail"].get("synthesized"),
            "the turn's exit synthesized an ending over a terminal the worker "
            "thread had already written")

    def test_eight_threads_racing_to_end_one_turn_leave_one_terminal(self) -> None:
        """Repeated, because a claim that is not atomic passes a single
        attempt most of the time."""
        for attempt in range(20):
            with self.subTest(attempt=attempt):
                tid = glass.new_turn_id()
                gate = threading.Barrier(8)
                with glass.turn(tid, "web"):
                    bounds = [glass.bind_context() for _ in range(8)]

                    def end(ctx) -> None:
                        gate.wait(timeout=30)
                        ctx.run(glass.emit, "delivery", "final",
                                detail={"text": "race"})

                    with ThreadPoolExecutor(max_workers=8) as ex:
                        for f in [ex.submit(end, c) for c in bounds]:
                            f.result()
                terminals = self._terminals(tid)
                self.assertEqual(len(terminals), 1, terminals)
                refused = [r for r in _rows(self.tmp)
                           if r.get("turn_id") == tid
                           and r.get("event") == "terminal_after_terminal"]
                self.assertEqual(
                    len(refused), 7,
                    "every attempt that lost the race must still be recorded, "
                    "or a call site that ends an already-ended turn becomes "
                    "invisible")

if __name__ == "__main__":
    unittest.main()
