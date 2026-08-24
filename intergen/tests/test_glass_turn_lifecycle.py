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

⚠️ ONE TERMINAL KIND IS SPECIFIED HERE AND NOT EXERCISED AGAINST ITS PRODUCER:
the route deadline. It is being added on a different lane (the turn-lifecycle
branch) and is NOT in this branch's base, so no test here can drive it. The
vocabulary includes it so that lane's timeout slots into this invariant without
touching this gate — but that it does is UNPROVEN here and is named as such in
the delivery.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import tempfile
import unittest
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
        """Specified for the route deadline arriving on another lane. NOT
        exercised against its producer here — that code is not in this base."""
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
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "run"):
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


if __name__ == "__main__":
    unittest.main()
