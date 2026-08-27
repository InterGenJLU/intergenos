# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A dispatch that was refused says why, instead of being narrated away.

THE MEASURED CASE, 2026-08-27, on the 2B at compose #62. "check if docker is
installed and if not, install it" decomposes into two clauses. Clause 1
dispatched manage_packages and answered truthfully. Clause 2 — "install it" —
resolved its referent correctly, built manage_packages(action=install,
package=docker), and the tool RAN AND REFUSED: a mutating package action
requires root, this process is not root, so it returned success=False with a
message saying exactly that and that nothing was attempted. That refusal is the
tool being right.

WHAT WENT WRONG AFTERWARDS. The keyword rung turns an unsuccessful ToolResult
into a bare handled=False and DISCARDS the call and the result. The clause then
falls down the ladder to a freeform model answer, which said: "you can use the
following command: pkm install docker". So a correct, specific refusal was
replaced by prose describing a command, and the person is left unable to tell
that the machine tried and was refused for want of privilege. The glass row for
that clause records tools=[], because the discarded call never reached the
result — a dispatch that genuinely happened is invisible in the record.

WHAT THIS FIXES, AND WHAT IT DELIBERATELY DOES NOT.
  * A tool that RAN and refused with something to say: its own message is the
    answer. It is concrete, it is true, and it tells the person what to do next.
  * A carrier that could build no arguments (arguments_indeterminate): nothing
    ran, there is no message, and the documented remedy is still to fall through
    and ask which one. That path is untouched — a clarify is the honest answer
    when the request genuinely did not name its object.
  * The attempt is recorded either way: the declined result now carries the call
    it made and the result it got, so the per-clause row shows a dispatch that
    happened and why it did not stand.

WHAT THIS IS NOT. It is not a change to what gets installed, and nothing here
makes a privileged action succeed. An evaluation must never perform a real
package installation to prove a point, and must never reach an authentication
prompt; the fixtures below drive a stub tool that refuses exactly as the real
one does, and no privileged path is entered.
"""

from __future__ import annotations

import unittest
from unittest import mock

from intergen.interfaces.types import Provenance, RouteResult, ToolCall, ToolResult


class _RefusingTool:
    """Refuses like manage_packages does when it is not root."""

    REFUSAL = (
        "the package action 'install' changes the system and must run as root, "
        "but this process is running as uid 1000 (checked). It was not "
        "attempted."
    )

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def execute(self, arguments):
        self.calls.append(dict(arguments or {}))
        return ToolResult(call_id="", name="manage_packages",
                          content=self.REFUSAL, success=False)


class TheDeclinedResultCarriesItsAttempt(unittest.TestCase):
    """(2) tools=[] must not hide a dispatch that happened."""

    def test_route_result_can_carry_a_refused_dispatch(self):
        """The shape the fix relies on: a not-handled result with its attempt.

        RouteResult already has the fields; nothing forbade a declined result
        from filling them, and the keyword rung simply did not.
        """
        call = ToolCall(name="manage_packages",
                        arguments={"action": "install", "package": "docker"},
                        source_of_request=Provenance.USER_DIRECT)
        result = ToolResult(call_id="", name="manage_packages",
                            content=_RefusingTool.REFUSAL, success=False)
        rr = RouteResult(handled=False, decline_reason="dispatch_failed",
                         tool_calls=[call], tool_results=[result])
        self.assertFalse(rr.handled)
        self.assertEqual([c.name for c in rr.tool_calls], ["manage_packages"])
        self.assertFalse(rr.tool_results[0].success)

    def test_the_keyword_rung_returns_the_attempt_on_a_failed_dispatch(self):
        """RED BEFORE THE FIX: the rung returns RouteResult(handled=False,
        decline_reason=...) and drops the call and result on the floor."""
        from intergen import router as router_mod

        import ast
        import inspect
        import textwrap

        src = textwrap.dedent(
            inspect.getsource(router_mod.ConversationRouter._try_keyword_match))
        tree = ast.parse(src)
        # The decline is the return that names decline_reason. Read THAT return,
        # not the method as a whole: the success path a few lines above already
        # passes tool_calls, so a substring search over the method would pass
        # while the decline path still dropped its attempt.
        # Only the declines that could have dispatched. The rung's first
        # decline is a literal decline_reason="no_intent" — nothing claimed the
        # clause, nothing ran, and there is no attempt to carry. Requiring one
        # there would be demanding a record of something that never happened.
        declines = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Return) and isinstance(n.value, ast.Call)
            and any(k.arg == "decline_reason" for k in n.value.keywords)
            and not any(k.arg == "decline_reason"
                        and isinstance(k.value, ast.Constant)
                        and k.value.value == "no_intent"
                        for k in n.value.keywords)
        ]
        self.assertTrue(
            declines, "no decline return found in the keyword rung; re-point "
                      "this test at where it declines now")
        carried = [
            d for d in declines
            if {"tool_calls", "tool_results"} <= {k.arg for k in d.value.keywords}
        ]
        self.assertEqual(
            len(carried), len(declines),
            f"{len(declines) - len(carried)} of {len(declines)} decline returns "
            f"in the keyword rung drop the call and result they made, so a "
            f"dispatch that was attempted and refused is invisible in the "
            f"per-clause record")


class TheRefusalReachesTheAnswer(unittest.TestCase):
    """(1) a refused dispatch is never replaced by prose describing the command."""

    def test_a_refusal_with_a_message_is_the_answer(self):
        from intergen.router import _decline_answer

        rr = _decline_answer(
            "dispatch_failed",
            ToolResult(call_id="", name="manage_packages",
                       content=_RefusingTool.REFUSAL, success=False))
        self.assertIsNotNone(
            rr, "a tool that ran and refused with a message produced no answer, "
                "so the clause falls through to the model and the refusal is lost")
        self.assertTrue(rr.handled)
        self.assertIn("must run as root", rr.text)
        self.assertIn("not attempted", rr.text.lower())

    def test_the_answer_does_not_describe_the_command_as_the_result(self):
        from intergen.router import _decline_answer

        rr = _decline_answer(
            "dispatch_failed",
            ToolResult(call_id="", name="manage_packages",
                       content=_RefusingTool.REFUSAL, success=False))
        low = rr.text.lower()
        self.assertNotIn(
            "you can use the following command", low,
            "the refusal answer narrates a command instead of reporting the "
            "refusal — the exact shape this fix exists to end")

    def test_an_indeterminate_argument_still_falls_through_to_a_clarify(self):
        """The carve-out. Nothing ran, there is no message, and the documented
        remedy is to ask which one — that path must be untouched."""
        from intergen.router import _decline_answer

        self.assertIsNone(
            _decline_answer("arguments_indeterminate", None),
            "a carrier that built no arguments must still fall through so the "
            "turn can ask which one; inventing an answer here would replace a "
            "clarify with a non-answer")

    def test_an_unmatched_clause_still_falls_through(self):
        from intergen.router import _decline_answer

        self.assertIsNone(_decline_answer("no_intent", None))
        self.assertIsNone(_decline_answer("intent_without_tool", None))

    def test_a_failed_dispatch_with_no_message_falls_through(self):
        """A refusal that says nothing is not an answer.

        Surfacing an empty string would replace a model's attempt to help with
        silence, which is worse than the fall-through it replaced.
        """
        from intergen.router import _decline_answer

        self.assertIsNone(
            _decline_answer("dispatch_failed",
                            ToolResult(call_id="", name="manage_packages",
                                       content="   ", success=False)))

    def test_a_successful_result_is_not_a_decline(self):
        """Defensive: this helper must never claim a successful dispatch."""
        from intergen.router import _decline_answer

        self.assertIsNone(
            _decline_answer("dispatch_failed",
                            ToolResult(call_id="", name="manage_packages",
                                       content="installed", success=True)))

    def test_the_answer_is_marked_as_code_owned_not_model_prose(self):
        """The text is the tool's, so the linkage must say so — otherwise the
        delivery surfaces record it as an uninstrumented path."""
        from intergen.router import _decline_answer

        rr = _decline_answer(
            "dispatch_failed",
            ToolResult(call_id="", name="manage_packages",
                       content=_RefusingTool.REFUSAL, success=False))
        self.assertFalse(rr.used_llm,
                         "the refusal text came from the tool, not the model")
        self.assertIsNotNone(
            rr.answer_linkage,
            "a composed answer with no declared linkage is recorded as an "
            "uninstrumented path")
        self.assertEqual(rr.answer_linkage.tool, "manage_packages")


if __name__ == "__main__":
    unittest.main()
