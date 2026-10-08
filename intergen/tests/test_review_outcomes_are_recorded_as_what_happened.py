# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A review nobody decided is recorded as what happened, and runs nothing.

The web review bridge answered the registry "deny" for every outcome that was
not an approval, and the registry recorded every "deny" as the person's:
``user_decision`` "denied", ``result_summary`` "User denied via review modal".
A card that went unanswered for five minutes, a second card refused because
one under the same id was already open, and a card that could not be shown at
all each left an audit record saying the person had refused the action.

The bridge now answers with what became of the card, and the registry records
each as its own outcome, naming what happened: review_timed_out,
review_refused, review_failed. The person's own Deny is recorded as before.

The registry also treated any answer that was not one of the two denials as an
approval and ran the held call. The new answers make that a hole, so an answer
that is not an approval now runs nothing, and one the registry does not know is
recorded as a failed review and quoted.

The cases drive the real ToolRegistry through the real bridge
(_make_web_review_callback) and the real card on one connection, the registry
in a worker thread as the web server runs it. Nothing privileged runs: the root
dispatch is replaced by a recorder, the dispatch token by a fixed value, and
the audit writer by a list.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from intergen import tool_registry as registry_module
from intergen import web_server as web_server_module
from intergen.interfaces.provenance import Provenance
from intergen.interfaces.types import ToolCall, ToolResult
from intergen.tool_registry import ToolRegistry
from intergen.web_server import ConnectionContext, WebServer

# A call the registry holds for review (write_file is dispatched as root), at a
# path that is never written: the root dispatch is a recorder here.
_ARGS = {"path": "/tmp/review-outcome-case-never-written.txt",
         "content": "x"}


class _RecordingWS:
    def __init__(self, fail_prompt: bool = False) -> None:
        self.sent: list[dict] = []
        self.fail_prompt = fail_prompt
        self._prompted: dict[str, asyncio.Event] = {}

    async def send_json(self, frame: dict) -> None:
        if frame.get("type") == "gate_prompt":
            if self.fail_prompt:
                raise ConnectionResetError("the page went away")
            self._event(frame.get("tool_call_id")).set()
        self.sent.append(frame)

    async def close(self, *a, **k) -> None:
        return None

    def _event(self, call_id: str) -> asyncio.Event:
        return self._prompted.setdefault(call_id, asyncio.Event())

    async def wait_for_card(self, call_id: str) -> None:
        await asyncio.wait_for(self._event(call_id).wait(), timeout=5.0)


def _call(call_id: str) -> ToolCall:
    return ToolCall(name="write_file", arguments=dict(_ARGS), call_id=call_id,
                    source_of_request=Provenance.USER_DIRECT)


class _IntegrityFailure:
    """A governance stand-in that refuses every call outright, as it does when
    its integrity check fails."""

    def evaluate(self, tool_call_id, **_kw):
        from intergen.governance import GovernanceCheck, GovernanceDecision
        return GovernanceDecision(
            tool_call_id=tool_call_id, allowed=False,
            blocked_by="hash_integrity",
            checks=[GovernanceCheck(gate_name="hash_integrity", passed=False)])


class _Case(unittest.TestCase):

    def setUp(self) -> None:
        self.records: list = []
        self.dispatched: list = []
        self.registry = ToolRegistry()
        self.registry.discover_tools()
        for patcher in (
            mock.patch.object(registry_module, "write_record",
                              side_effect=self._record),
            mock.patch.object(registry_module, "mint_token",
                              return_value="case-token"),
            mock.patch.object(self.registry, "_dispatch_via_pkexec",
                              side_effect=self._dispatch),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _record(self, record, *a, **k) -> bool:
        self.records.append(record)
        return True

    def _dispatch(self, call, *a, **k) -> ToolResult:
        self.dispatched.append(call.arguments)
        return ToolResult(call_id=call.call_id, name=call.name, content="ok",
                          success=True)

    def _server(self, ws: _RecordingWS) -> tuple[WebServer, ConnectionContext]:
        server = WebServer()
        server._tools = self.registry
        server._governance = None
        ctx = ConnectionContext(client_id="review-outcome-case",
                                source_interface="web", ws=ws)
        return server, ctx

    @staticmethod
    async def _through_the_bridge(server: WebServer, ctx: ConnectionContext,
                                  call: ToolCall,
                                  answer: str | None = None) -> ToolResult:
        loop = asyncio.get_running_loop()
        bridge = server._make_web_review_callback(ctx, "turn-1", loop)
        pending = loop.run_in_executor(
            None, lambda: server._tools.execute(call, review_callback=bridge))
        if answer is not None:
            await ctx.ws.wait_for_card(call.call_id)
            await server._handle_gate_decision(
                ctx, {"tool_call_id": call.call_id, "decision": answer})
        return await asyncio.wait_for(pending, timeout=15.0)

    def _the_record(self):
        self.assertEqual(len(self.records), 1,
                         f"expected one audit record, got {self.records}")
        return self.records[0]

    def _nothing_ran_and_no_one_denied(self, result: ToolResult,
                                       outcome: str, words: str) -> None:
        record = self._the_record()
        self.assertEqual(self.dispatched, [], "the held call ran")
        self.assertFalse(result.executed)
        self.assertFalse(result.success)
        self.assertFalse(
            result.denied_by_user,
            "the result says the person denied an action nobody decided")
        self.assertEqual(
            record.user_decision, outcome,
            f"recorded as {record.user_decision!r}: {record.result_summary!r}")
        self.assertNotIn("User denied", record.result_summary)
        self.assertIn(words, record.result_summary)
        self.assertIn(words, result.content)


class AReviewNobodyDecidedIsRecordedAsWhatHappened(_Case):

    def test_a_card_nobody_answered_is_recorded_as_timed_out(self) -> None:
        server, ctx = self._server(_RecordingWS())
        with mock.patch.object(web_server_module, "_REVIEW_CARD_TIMEOUT_S",
                               0.2):
            result = asyncio.run(
                self._through_the_bridge(server, ctx, _call("timed")))
        self._nothing_ran_and_no_one_denied(
            result, "review_timed_out", "not answered before its deadline")

    def test_a_second_card_under_an_open_id_is_recorded_as_refused(
            self) -> None:
        server, ctx = self._server(_RecordingWS())

        async def scenario() -> ToolResult:
            from intergen.interfaces.provenance import ToolRiskTier
            first = asyncio.create_task(server._render_review_card(
                ctx, "turn-0", _call("same"),
                ToolRiskTier.PRIVILEGED_STATE_CHANGING))
            await ctx.ws.wait_for_card("same")
            try:
                return await self._through_the_bridge(server, ctx,
                                                      _call("same"))
            finally:
                await server._handle_gate_decision(
                    ctx, {"tool_call_id": "same", "decision": "deny"})
                await asyncio.wait_for(first, timeout=5.0)

        result = asyncio.run(scenario())
        self._nothing_ran_and_no_one_denied(
            result, "review_refused", "already open")

    def test_a_card_that_could_not_be_shown_is_recorded_as_failed(
            self) -> None:
        server, ctx = self._server(_RecordingWS(fail_prompt=True))
        result = asyncio.run(self._through_the_bridge(server, ctx,
                                                      _call("unshown")))
        self._nothing_ran_and_no_one_denied(
            result, "review_failed", "could not be shown")


class ACardANewConversationEndedIsRecordedAsEnded(_Case):
    """A new conversation ends every card the old one left open, and the card
    answers its waiter "deny" so the action cannot run. Through the bridge that
    reached the registry as the person's own Deny."""

    def test_a_card_a_new_conversation_ended(self) -> None:
        server, ctx = self._server(_RecordingWS())

        async def scenario() -> ToolResult:
            loop = asyncio.get_running_loop()
            bridge = server._make_web_review_callback(ctx, "turn-1", loop)
            call = _call("ended")
            pending = loop.run_in_executor(
                None,
                lambda: server._tools.execute(call, review_callback=bridge))
            await ctx.ws.wait_for_card("ended")
            await server._handle_new_session(ctx, {})
            return await asyncio.wait_for(pending, timeout=15.0)

        result = asyncio.run(scenario())
        self._nothing_ran_and_no_one_denied(
            result, "review_ended", "conversation it belonged to ended")


class AnActionRefusedBeforeAnyCardIsRecordedAsRefused(_Case):
    """The bridge refuses two classes of action before raising a card: an
    action in a forbidden zone, and one governance refuses outright (an
    integrity failure, an owner-only action). It answered "deny" for both, so
    the record named the person for a refusal no card ever offered them."""

    def _refused_before_any_card(self, server, ctx, call) -> None:
        result = asyncio.run(self._through_the_bridge(server, ctx, call))
        self.assertEqual(
            [f for f in ctx.ws.sent if f.get("type") == "gate_prompt"], [],
            "a card was raised for an action refused before review")
        self._nothing_ran_and_no_one_denied(
            result, "refused_without_review", "refuses the action outright")

    def test_an_action_in_a_forbidden_zone(self) -> None:
        from intergen import zones
        call = ToolCall(name="manage_services",
                        arguments={"action": "restart",
                                   "service": "intergen"},
                        call_id="forbidden",
                        source_of_request=Provenance.USER_DIRECT)
        self.assertTrue(zones.forbidden_reason(call.name, call.arguments),
                        "the case's call is no longer in a forbidden zone")
        server, ctx = self._server(_RecordingWS())
        self._refused_before_any_card(server, ctx, call)

    def test_an_action_governance_refuses_outright(self) -> None:
        server, ctx = self._server(_RecordingWS())
        server._governance = _IntegrityFailure()
        self._refused_before_any_card(server, ctx, _call("integrity"))


class AnAnswerThatIsNotAnApprovalRunsNothing(_Case):

    def test_an_answer_the_registry_does_not_know_runs_nothing(self) -> None:
        result = self.registry.execute(
            _call("unknown"), review_callback=lambda c, d: "later")
        self._nothing_ran_and_no_one_denied(
            result, "review_failed", "'later'")

    def test_flagged_content_is_withheld_on_an_answer_that_is_not_an_approval(
            self) -> None:
        result = _flagged_call(lambda c, d: "later")
        self.assertNotIn("RETURNED TEXT", result.content,
                         "flagged content was admitted on an answer that is "
                         "not an approval")


class ACommandsNotRunLineSaysWhatHappened(_Case):
    """A command the person invoked (/file) that does not run is reported on
    the page as a tool_executed line. It said "the request was denied" for
    every outcome: a card nobody answered and a refusal no card offered read
    as a refusal the person gave."""

    _PATH = {"path": "/etc/hostname"}

    def _not_run_line(self, server, ctx) -> dict:
        lines = [f for f in ctx.ws.sent if f.get("type") == "tool_executed"]
        self.assertEqual(len(lines), 1, ctx.ws.sent)
        self.assertFalse(lines[0]["success"])
        self.assertIn("was not run", lines[0]["summary"])
        return lines[0]

    def test_a_card_nobody_answered(self) -> None:
        server, ctx = self._server(_RecordingWS())
        with mock.patch.object(web_server_module, "_REVIEW_CARD_TIMEOUT_S",
                               0.2):
            asyncio.run(server._run_user_invoked_tool(ctx, "read_file",
                                                      dict(self._PATH)))
        line = self._not_run_line(server, ctx)
        self.assertIn("not answered in time", line["summary"])
        self.assertNotIn("denied", line["summary"])

    def test_a_refusal_no_card_offered(self) -> None:
        server, ctx = self._server(_RecordingWS())
        server._governance = _IntegrityFailure()
        asyncio.run(server._run_user_invoked_tool(ctx, "read_file",
                                                  dict(self._PATH)))
        line = self._not_run_line(server, ctx)
        self.assertIn("refuses that action outright", line["summary"])
        self.assertNotIn("denied", line["summary"])

    def test_the_persons_deny(self) -> None:
        """Control: the person's Deny still reads as theirs."""
        server, ctx = self._server(_RecordingWS())

        async def scenario() -> None:
            task = asyncio.create_task(server._run_user_invoked_tool(
                ctx, "read_file", dict(self._PATH)))
            prompts: list = []
            for _ in range(500):
                prompts = [f for f in ctx.ws.sent
                           if f.get("type") == "gate_prompt"]
                if prompts:
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(prompts, "the command raised no card")
            await server._handle_gate_decision(
                ctx, {"tool_call_id": prompts[0]["tool_call_id"],
                      "decision": "deny"})
            await asyncio.wait_for(task, timeout=5.0)

        asyncio.run(scenario())
        self.assertIn("the request was denied",
                      self._not_run_line(server, ctx)["summary"])


def _flagged_call(review_callback) -> ToolResult:
    """An external tool whose returned text the ingress scan flags, run
    through the registry with ``review_callback`` (None: no review surface)."""
    from intergen.interfaces.scanner import (
        ScanDirection, ScanDisposition, Scanner, ScanVerdict,
    )
    from intergen.scanner.policy import ScannerPolicy

    class _FlagIngress(Scanner):
        name = "case"
        is_local = True

        def scan(self, content, ctx):
            disposition = (ScanDisposition.FLAG
                           if ctx.direction is ScanDirection.INGRESS
                           else ScanDisposition.ALLOW)
            return ScanVerdict(disposition=disposition,
                               reason="looks like an instruction",
                               scanner="case")

    registry = ToolRegistry(
        scanner_policy=ScannerPolicy(local_rules=_FlagIngress()))
    registry.register_external(
        name="mcp_case_tool", schema=None,
        handler=lambda args: "RETURNED TEXT", system_prompt_rule="case")
    return registry.execute(
        ToolCall(name="mcp_case_tool", arguments={"q": "x"},
                 source_of_request=Provenance.USER_DIRECT),
        review_callback=review_callback)


class FlaggedContentNobodyApprovedIsRecordedAsWhatHappened(_Case):
    """The ingress-flag review withholds flagged content on any answer that is
    not an approval. It recorded every one as "+ingress_flag_denied", "FLAG
    denied": a card nobody answered, a review that failed and a machine with no
    review surface all read as the person's refusal."""

    def test_each_outcome_is_recorded_as_itself(self) -> None:
        for answer, outcome in (
                ("review_timed_out", "review_timed_out"),
                ("review_failed", "review_failed"),
                ("later", "review_failed"),
                (None, "no_review_ui")):
            with self.subTest(answer=answer):
                self.records.clear()
                callback = (None if answer is None
                            else (lambda c, d, _a=answer: _a))
                result = _flagged_call(callback)
                record = self._the_record()
                self.assertNotIn("RETURNED TEXT", result.content)
                self.assertTrue(
                    record.user_decision.endswith(f"+ingress_flag_{outcome}"),
                    f"recorded as {record.user_decision!r}: "
                    f"{record.result_summary!r}")
                self.assertNotIn("FLAG denied", record.result_summary)

    def test_the_persons_deny_is_recorded_as_before(self) -> None:
        """Control."""
        result = _flagged_call(lambda c, d: "deny")
        record = self._the_record()
        self.assertNotIn("RETURNED TEXT", result.content)
        self.assertTrue(record.user_decision.endswith("+ingress_flag_denied"))
        self.assertIn("FLAG denied", record.result_summary)


class ThePersonsOwnAnswersAreUnchanged(_Case):
    """Controls: the person's Deny is still recorded as theirs, and their
    Approve still runs the call."""

    def test_the_persons_deny_is_recorded_as_theirs(self) -> None:
        server, ctx = self._server(_RecordingWS())
        result = asyncio.run(self._through_the_bridge(
            server, ctx, _call("denied"), answer="deny"))
        record = self._the_record()
        self.assertEqual(self.dispatched, [])
        self.assertTrue(result.denied_by_user)
        self.assertEqual(record.user_decision, "denied")
        self.assertEqual(record.result_summary, "User denied via review modal")

    def test_the_persons_approve_runs_the_call(self) -> None:
        server, ctx = self._server(_RecordingWS())
        result = asyncio.run(self._through_the_bridge(
            server, ctx, _call("approved"), answer="allow"))
        self.assertEqual(self.dispatched, [_ARGS])
        self.assertTrue(result.success)
        self.assertEqual(self._the_record().user_decision, "allowed_once")


class AHeldCallWithNoReviewSurfaceIsRecordedAsSuch(_Case):
    """A held call with no review surface at all (no review callback) was
    recorded "denied", the person's word, beside a summary saying no review UI
    was available: the same field the person's own Deny writes. It is recorded
    as no_review_ui, the word the inbound review already uses for the same
    fact."""

    def test_no_review_surface_is_not_the_persons_deny(self) -> None:
        result = self.registry.execute(_call("no-ui"), review_callback=None)
        self._nothing_ran_and_no_one_denied(result, "no_review_ui",
                                            "review UI")

    def test_it_differs_from_the_persons_deny(self) -> None:
        self.registry.execute(_call("no-ui"), review_callback=None)
        self.registry.execute(_call("deny"),
                              review_callback=lambda c, d: "deny")
        no_ui, deny = (r.user_decision for r in self.records)
        self.assertEqual(deny, "denied")
        self.assertNotEqual(no_ui, deny)


class AnApprovalWhoseTokenCouldNotBeMintedIsNotADenial(_Case):
    """The person approved a privileged call, and the single-use token that
    carries the approval to the root boundary could not be minted. Nothing
    ran — correctly — and the record said "denied", although the only
    decision the person made was an approval."""

    def test_the_failed_token_is_recorded_as_what_happened(self) -> None:
        with mock.patch.object(
                registry_module, "mint_token",
                side_effect=registry_module.DispatchTokenError(
                    "signing key missing")):
            result = self.registry.execute(
                _call("token"), review_callback=lambda c, d: "allow_once")
        self._nothing_ran_and_no_one_denied(
            result, "dispatch_token_failed", "token")
        self.assertIn("signing key missing", self._the_record().result_summary)


if __name__ == "__main__":
    unittest.main()
