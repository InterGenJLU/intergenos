# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A person who denies an action is answered in their language, not the log's.

THE MEASURED CASE, 2026-09-16, against a live daemon on the shipped web panel.
A person asks "restart the sshd service", is shown the consent card, and presses
DENY. The terminal frame carries, as the whole user-visible answer:

    Tool call denied by user via review modal.

That sentence is the registry's own AUDIT RECORD of what the person just did
(tool_registry.py, the deny return). It was delivered verbatim, in the machine's
vocabulary, and it says nothing about what the person can do instead.

WHY IT REACHED THEM. The keyword rung dispatches through the registry, the
registry pops the consent card through the review bridge, and the deny comes
back as an unsuccessful ToolResult. ``_decline_answer`` then delivers a refused
dispatch's content as the answer — correct, and measured, for the refusal class
it was written for:

  * a TOOL REFUSAL — the tool RAN and refused with a sentence written for a
    person ("this changes the system and must run as root; it was not
    attempted"). Delivering it beats letting the model improvise advice, which
    is the shape test_declined_dispatch_says_why.py pins. That behaviour must
    not be undone, and the cell below asserts it directly.
  * a GATE DENIAL — the person themselves pressed deny. The registry's content
    for that case is an audit string.

The renderer written for the second case already exists and already says the
honest thing (name the action, say plainly why it cannot proceed here, hand over
the exact command). It was unreachable from this path, because the router
answered first.

WHAT THIS PINS. The registry MARKS a user denial structurally, the decline
answer renders that class through the same single source every other surface
uses, and the registry's audit string never reaches a person on any path.

No privileged action is performed here and no authentication prompt can be
reached: every cell drives constructed results and a stub registry.
"""

from __future__ import annotations

import unittest

from intergen.interfaces.types import (
    AnswerLinkage, Provenance, ToolCall, ToolResult,
)

# The registry's audit record of a denial, verbatim. A person must never be
# shown this sentence; it is the thing the whole file exists to keep out of an
# answer, so it is named once here and asserted against everywhere.
AUDIT_STRING = "Tool call denied by user via review modal."


def _denied_result(name="manage_services", call_id="c1") -> ToolResult:
    """What the registry returns when the person presses deny at the card."""
    return ToolResult(call_id=call_id, name=name, content=AUDIT_STRING,
                      success=False, executed=False, denied_by_user=True)


def _service_call(action="restart", service="sshd") -> ToolCall:
    return ToolCall(name="manage_services",
                    arguments={"action": action, "service": service},
                    call_id="c1", source_of_request=Provenance.USER_DIRECT)


class TheRegistryMarksADenialStructurally(unittest.TestCase):
    """The signal is a field, not a sentence.

    Matching the audit text would make the answer depend on the wording of a
    log line, so any later edit to that line would silently restore the defect.
    """

    def test_tool_result_carries_a_denied_by_user_field(self):
        self.assertFalse(ToolResult(call_id="", name="t", content="").
                         denied_by_user)
        self.assertTrue(_denied_result().denied_by_user)

    def test_both_deny_choices_mark_the_result(self):
        """deny and deny_conversation are both the person pressing deny."""
        import ast
        import inspect
        import textwrap

        from intergen import tool_registry as reg_mod

        src = textwrap.dedent(inspect.getsource(reg_mod.ToolRegistry))
        tree = ast.parse(src)
        # The deny branch is the one testing user_choice against the two deny
        # choices; the ToolResult it returns must set the mark. The whole class
        # is read rather than one named method, so moving the gate between the
        # registry's own methods cannot make this cell pass vacuously.
        marked = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            branch_src = ast.dump(node.test)
            if "deny_conversation" not in branch_src:
                continue
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Call)
                        and getattr(inner.func, "id", "") == "ToolResult"):
                    marked.append(any(k.arg == "denied_by_user"
                                      and getattr(k.value, "value", False) is True
                                      for k in inner.keywords))
        self.assertTrue(marked,
                        "no deny branch found in ToolRegistry; re-point this "
                        "cell at where the deny returns now")
        self.assertTrue(all(marked),
                        "a deny branch returns a ToolResult that is not marked "
                        "denied_by_user, so the router cannot tell it from a "
                        "tool that ran and refused")


class TheDeniedAnswerIsWrittenForThePerson(unittest.TestCase):
    """RED BEFORE THE FIX: the audit string was the answer."""

    def _decline(self, tr, call=None):
        from intergen.router import _decline_answer
        return _decline_answer("dispatch_failed", tr, tool_call=call)

    def test_the_audit_string_never_reaches_the_person(self):
        rr = self._decline(_denied_result(), _service_call())
        self.assertIsNotNone(
            rr, "a denial must still be answered here; falling through sends "
                "the clause to the model, which is the improvised-advice shape "
                "this path exists to prevent")
        self.assertNotIn(AUDIT_STRING.lower(), rr.text.lower())

    def test_the_answer_names_the_action_and_the_command(self):
        """The 3-part honest handoff, from the one source every surface uses."""
        rr = self._decline(_denied_result(), _service_call())
        low = rr.text.lower()
        self.assertIn("sshd", low)
        self.assertIn("systemctl restart sshd", low)

    def test_the_answer_is_recorded_as_code_owned_not_as_a_dispatch(self):
        """The words are the code's, so the linkage must not claim a dispatch.

        A composed refusal recorded as kind="dispatch" would read, to every
        delivery surface, as text taken from the tool result — which is the one
        thing it is not.
        """
        rr = self._decline(_denied_result(), _service_call())
        link = rr.answer_linkage
        self.assertIsInstance(link, AnswerLinkage)
        self.assertEqual(link.kind, "code")
        self.assertEqual(link.renderer, "gate_refusal")

    def test_a_denial_with_no_command_still_answers_plainly(self):
        """A tool with no command line to hand over gets the plain refusal,
        never an invented command and never the audit string."""
        tr = _denied_result(name="take_screenshot")
        call = ToolCall(name="take_screenshot", arguments={}, call_id="c1",
                        source_of_request=Provenance.USER_DIRECT)
        rr = self._decline(tr, call)
        self.assertNotIn(AUDIT_STRING.lower(), rr.text.lower())
        self.assertIn("not able to do that from here", rr.text.lower())

    def test_a_blocked_action_is_never_handed_a_command_to_run(self):
        """Fail-closed: a hard-refused action must not be handed to the person
        as something to run themselves."""
        tr = ToolResult(call_id="c1", name="run_command", content=AUDIT_STRING,
                        success=False, executed=False, blocked=True,
                        denied_by_user=True)
        call = ToolCall(name="run_command", arguments={"command": "rm -rf /"},
                        call_id="c1", source_of_request=Provenance.USER_DIRECT)
        rr = self._decline(tr, call)
        self.assertNotIn("rm -rf", rr.text)
        self.assertIn("not able to do that from here", rr.text.lower())


class TheOtherRefusalClassKeepsItsVerbatimDelivery(unittest.TestCase):
    """The measured fix this must not undo.

    A tool that RAN and refused has said something specific and true, and
    replacing it with a generic refusal would lose the reason — the exact loss
    test_declined_dispatch_says_why.py was written to stop.
    """

    REFUSAL = ("the package action 'install' changes the system and must run "
               "as root, but this process is running as uid 1000 (checked). "
               "It was not attempted.")

    def test_a_tool_that_ran_and_refused_is_still_delivered_verbatim(self):
        from intergen.router import _decline_answer
        tr = ToolResult(call_id="c2", name="manage_packages",
                        content=self.REFUSAL, success=False)
        rr = _decline_answer("dispatch_failed", tr)
        self.assertEqual(rr.text, self.REFUSAL)
        self.assertEqual(rr.answer_linkage.kind, "dispatch")
        self.assertEqual(rr.answer_linkage.renderer, "tool_refusal")

    def test_an_unmarked_not_executed_refusal_keeps_its_own_words(self):
        """Only a MARKED denial is re-rendered.

        The registry has other not-executed refusals whose content is already
        written for a person — the approval token that could not be minted names
        the recovery step. Re-rendering those would throw that sentence away.
        """
        from intergen.router import _decline_answer
        content = ("Privileged action approved, but the approval token could "
                   "not be minted; refusing dispatch. Run 'intergen setup'.")
        tr = ToolResult(call_id="c3", name="manage_services", content=content,
                        success=False, executed=False)
        rr = _decline_answer("dispatch_failed", tr, tool_call=_service_call())
        self.assertEqual(rr.text, content)

    def test_a_successful_result_is_never_a_decline(self):
        from intergen.router import _decline_answer
        tr = ToolResult(call_id="c4", name="manage_services", content="done",
                        success=True)
        self.assertIsNone(_decline_answer("dispatch_failed", tr))


class TheKeywordRungHandsTheDeclineItsCall(unittest.TestCase):
    """The rung must pass the call, or the answer cannot name the action."""

    def test_the_decline_site_passes_the_tool_call(self):
        import ast
        import inspect
        import textwrap

        from intergen import router as router_mod

        src = textwrap.dedent(
            inspect.getsource(router_mod.ConversationRouter._try_keyword_match))
        tree = ast.parse(src)
        sites = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and getattr(n.func, "id", "") == "_decline_answer"]
        self.assertTrue(sites, "no _decline_answer call found in the keyword "
                               "rung; re-point this cell at where it declines")
        for site in sites:
            self.assertTrue(
                any(k.arg == "tool_call" for k in site.keywords),
                "the rung calls _decline_answer without the tool call, so a "
                "denied action cannot be named in the answer")


class TheOneRendererServesEverySurface(unittest.TestCase):
    """The web card's refusal and the router's are the same function.

    Two copies of this text would drift, and the drift would be invisible: each
    surface would look right on its own.
    """

    def test_the_web_gate_refusal_delegates_to_the_shared_renderer(self):
        import inspect

        from intergen import tool_registry as reg_mod
        from intergen.web_server import WebServer

        self.assertTrue(hasattr(reg_mod, "gate_refusal_message"),
                        "the shared renderer is missing from tool_registry")
        src = inspect.getsource(WebServer._gate_refusal_message)
        self.assertIn("gate_refusal_message", src,
                      "the web surface no longer renders its refusal through "
                      "the shared renderer, so the two can drift apart")

    def test_the_shared_renderer_is_what_the_router_uses(self):
        import inspect

        from intergen import router as router_mod

        src = inspect.getsource(router_mod._decline_answer)
        self.assertIn("gate_refusal_message", src)


if __name__ == "__main__":
    unittest.main()
