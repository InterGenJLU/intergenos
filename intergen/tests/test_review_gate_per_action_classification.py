# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Per-ACTION risk classification for mixed-capability tools, and review copy
that describes the class it is actually asking about.

Observed 2026-08-12 on a running desktop: a `manage_services` call with action
`list` — a pure read — raised the approval modal, and the modal told the user
the request CHANGES THE SYSTEM. Measured chain behind it:

  1. `list` is not in the tool's schema enum, not in AUTO_ACTIONS and not in
     CONFIRM_ACTIONS. BaseTool.validate_arguments only checks that required
     parameters are PRESENT, so an out-of-enum value reaches classification.
  2. classify_safety returns CONFIRM for anything it does not recognise, and
     manage_services is in tool_registry._PRIVILEGED_TOOLS, so the unrecognised
     read is classified privileged_state_changing.
  3. ToolRegistry.execute prompts whenever the tier is privileged, independent
     of the gate's own verdict — so the read reached the modal.
  4. Had the user approved it, the tool would have run `systemctl list`, which
     systemd refuses ("Unknown command verb 'list'"). The modal asked for
     administrator approval for a command that cannot run.
  5. The review copy never consults the classification at all: the branded
     dialog's risk text is keyed on PROVENANCE only, and the zenity header is a
     fixed string asserting a system action.

Three properties are pinned here:

  * An information-only action of a mixed-capability tool is classified
    read-only and never reaches the approval surface — a person may always
    inspect their own machine.
  * An action the tool does not recognise is REFUSED before the gate, with the
    accepted values named. Unrecognised is not "most dangerous" and is not
    something to ask a human to authorise; it is an input error. Nothing
    executes, so this stays fail-closed.
  * Review copy is derived from the computed class. An unknown class keeps the
    most severe wording, so a missing tier can never soften a prompt.

Runs on any host: no display, no daemon, no privileged call, and the tool's own
execute() is stubbed wherever a gate path is exercised.
"""

from __future__ import annotations

import unittest
from unittest import mock

from intergen.consent_dialog_sanitize import review_risk_copy
from intergen.interfaces.provenance import (
    ConversationTrustState,
    IngressTracker,
    Provenance,
    ToolRiskTier,
)
from intergen.interfaces.types import ToolCall, ToolResult
from intergen.provenance import verify_tool_call
from intergen.tool_registry import ToolRegistry, _classify_risk_tier
from intergen.tools.manage_services import ManageServicesTool

# Every manage_services action that only reads state. `list` is included
# deliberately: manage_packages already accepts it as a read, so the two
# sibling tools disagreed on the spelling of the most basic query, which is
# what the model reached for.
READ_ONLY_SERVICE_ACTIONS = (
    "status", "is-active", "is-enabled", "is-failed",
    "list", "list-units", "list-unit-files", "show", "cat",
    "list-timers", "list-sockets", "list-dependencies",
)

STATE_CHANGING_SERVICE_ACTIONS = (
    "start", "stop", "restart", "reload",
    "enable", "disable", "mask", "unmask", "daemon-reload",
)


def _call(action: str, service: str = "sshd",
          provenance: Provenance = Provenance.USER_DIRECT) -> ToolCall:
    args: dict[str, object] = {"action": action}
    if service:
        args["service"] = service
    return ToolCall(
        name="manage_services",
        arguments=args,
        source_of_request=provenance,
    )


class ReadOnlyActionsAreClassifiedReadOnly(unittest.TestCase):
    """A read of your own machine is a read, whichever tool carries it."""

    def setUp(self):
        self.tool = ManageServicesTool()

    def test_every_information_action_is_read_only(self):
        for action in READ_ONLY_SERVICE_ACTIONS:
            with self.subTest(action=action):
                tier = _classify_risk_tier(
                    self.tool, {"action": action, "service": "sshd"},
                    "manage_services",
                )
                self.assertIs(
                    tier, ToolRiskTier.READ_ONLY,
                    f"{action!r} only reads state and must not be classified "
                    f"{tier.value}",
                )

    def test_read_only_actions_do_not_need_administrator_approval(self):
        for action in READ_ONLY_SERVICE_ACTIONS:
            with self.subTest(action=action):
                decision = verify_tool_call(
                    _call(action), IngressTracker(), ConversationTrustState(),
                    _classify_risk_tier(
                        self.tool, {"action": action}, "manage_services"),
                )
                self.assertEqual(decision.action, "execute")
                self.assertFalse(decision.needs_pkexec)

    def test_list_is_recognised_and_resolves_to_a_real_systemctl_verb(self):
        # `systemctl list` does not exist; the tool must run the verb that does.
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            self.tool.execute({"action": "list"})
        self.assertTrue(run.called, "execute() never invoked systemctl")
        argv = run.call_args[0][0]
        self.assertIn("list-units", argv)
        self.assertNotIn(
            "list", argv,
            "the bare verb `list` reached systemd, which refuses it",
        )


class StateChangingActionsStillGate(unittest.TestCase):
    """The fix must not open the actions the gate exists for."""

    def setUp(self):
        self.tool = ManageServicesTool()
        self.registry = ToolRegistry()
        self.registry.discover_tools()

    def test_every_state_changing_action_stays_privileged(self):
        for action in STATE_CHANGING_SERVICE_ACTIONS:
            with self.subTest(action=action):
                tier = _classify_risk_tier(
                    self.tool, {"action": action, "service": "sshd"},
                    "manage_services",
                )
                self.assertIs(tier, ToolRiskTier.PRIVILEGED_STATE_CHANGING)

    def test_state_change_without_a_review_surface_fails_closed(self):
        result = self.registry.execute(_call("restart"), review_callback=None)
        self.assertFalse(result.success)
        self.assertIn("review", result.content.lower())

    def test_state_change_reaches_the_review_surface(self):
        seen = []
        with mock.patch.object(
            self.registry, "_dispatch_via_pkexec",
            return_value=ToolResult(call_id="", name="manage_services",
                                    content="ok", success=True),
        ):
            self.registry.execute(
                _call("restart"),
                review_callback=lambda c, d: seen.append(d) or "deny",
            )
        self.assertEqual(len(seen), 1, "a state change must still be reviewed")


class ReadOnlyActionsNeverReachTheReviewSurface(unittest.TestCase):
    """The observed defect, at the seam the operator actually met."""

    def setUp(self):
        self.registry = ToolRegistry()
        self.registry.discover_tools()
        self.tool = self.registry.get_tool("manage_services")
        self._exec = mock.patch.object(
            type(self.tool), "execute",
            return_value=ToolResult(call_id="", name="manage_services",
                                    content="(stubbed)", success=True),
        )
        self._exec.start()
        self.addCleanup(self._exec.stop)

    def test_no_information_action_invokes_the_review_callback(self):
        for action in READ_ONLY_SERVICE_ACTIONS:
            with self.subTest(action=action):
                prompted = []
                result = self.registry.execute(
                    _call(action),
                    review_callback=lambda c, d: prompted.append(d) or "deny",
                )
                self.assertEqual(
                    prompted, [],
                    f"reading {action!r} raised an approval prompt",
                )
                self.assertTrue(result.success)


class UnrecognisedActionsAreRefusedNotGated(unittest.TestCase):
    """An action the tool does not know is an input error, not a decision to
    put in front of a person."""

    def setUp(self):
        self.tool = ManageServicesTool()
        self.registry = ToolRegistry()
        self.registry.discover_tools()

    def test_out_of_enum_action_is_a_validation_error(self):
        message = self.tool.validate_arguments(
            {"action": "frobnicate", "service": "sshd"})
        self.assertIsNotNone(
            message, "an action outside the declared enum passed validation")
        self.assertIn("frobnicate", message)
        self.assertIn("status", message,
                      "the refusal must name the accepted values")

    def test_declared_actions_still_validate(self):
        for action in READ_ONLY_SERVICE_ACTIONS + STATE_CHANGING_SERVICE_ACTIONS:
            with self.subTest(action=action):
                self.assertIsNone(
                    self.tool.validate_arguments(
                        {"action": action, "service": "sshd"}))

    def test_unrecognised_action_never_reaches_the_review_surface(self):
        prompted = []
        # The callback denies, and the privileged dispatcher is stubbed: at the
        # unfixed base this call DOES reach the review surface, and an approval
        # there would run the real pkexec path, which blocks on an
        # authentication dialog. A test may never be able to ask a person
        # anything, including on the run where it fails.
        with mock.patch.object(
            self.registry, "_dispatch_via_pkexec",
            return_value=ToolResult(call_id="", name="manage_services",
                                    content="(stubbed)", success=False),
        ):
            result = self.registry.execute(
                _call("frobnicate"),
                review_callback=lambda c, d: prompted.append(d) or "deny",
            )
        self.assertEqual(
            prompted, [],
            "an action the tool cannot run was put to the user for approval")
        self.assertFalse(result.success)
        self.assertFalse(result.executed)

    def test_enum_validation_is_generic_across_tools(self):
        # The hole is in the shared base class, so the guard belongs there too.
        packages = self.registry.get_tool("manage_packages")
        self.assertIsNotNone(
            packages.validate_arguments({"action": "frobnicate"}))


class ReviewCopyDescribesTheComputedClass(unittest.TestCase):
    """A prompt that misdescribes its own subject cannot be consented to."""

    def test_dispatch_decision_carries_the_risk_tier(self):
        decision = verify_tool_call(
            _call("status"), IngressTracker(), ConversationTrustState(),
            ToolRiskTier.READ_ONLY,
        )
        self.assertIs(decision.risk_tier, ToolRiskTier.READ_ONLY)

    def test_held_decision_carries_the_risk_tier(self):
        # privileged + user_DIRECT is `execute` in the matrix (the registry
        # prompts anyway, on needs_pkexec); user_implied is the combination
        # that the gate itself holds.
        decision = verify_tool_call(
            _call("restart", provenance=Provenance.USER_IMPLIED),
            IngressTracker(), ConversationTrustState(),
            ToolRiskTier.PRIVILEGED_STATE_CHANGING,
        )
        self.assertEqual(decision.action, "hold_for_review")
        self.assertIs(
            decision.risk_tier, ToolRiskTier.PRIVILEGED_STATE_CHANGING)

    def test_read_only_copy_does_not_assert_a_system_change(self):
        _sev, headline, detail = review_risk_copy(
            Provenance.USER_DIRECT.value, ToolRiskTier.READ_ONLY.value)
        text = f"{headline} {detail}".lower()
        self.assertNotIn("changes your system", text)
        self.assertIn("read", text,
                      "a read must be described as a read")

    def test_state_changing_copy_still_says_it_changes_the_system(self):
        for tier in (ToolRiskTier.USER_SCOPE_STATE_CHANGING,
                     ToolRiskTier.PRIVILEGED_STATE_CHANGING):
            with self.subTest(tier=tier.value):
                _sev, headline, detail = review_risk_copy(
                    Provenance.USER_DIRECT.value, tier.value)
                self.assertIn("change", f"{headline} {detail}".lower())

    def test_unknown_tier_keeps_the_severe_wording(self):
        # Fail-closed: a missing classification must never soften a prompt.
        _sev, headline, detail = review_risk_copy(
            Provenance.USER_DIRECT.value, None)
        self.assertIn("change", f"{headline} {detail}".lower())

    def test_unknown_provenance_still_reads_as_untrusted(self):
        severity, headline, _detail = review_risk_copy(
            "not-a-provenance", ToolRiskTier.READ_ONLY.value)
        self.assertEqual(severity, "danger")
        self.assertIn("recognize", headline.lower())


class ZenityHeaderMatchesTheClass(unittest.TestCase):
    """The fallback modal's first line is copy too."""

    def test_header_for_a_read_does_not_claim_a_system_action(self):
        from intergen.review_modal import review_header
        decision = verify_tool_call(
            _call("status"), IngressTracker(), ConversationTrustState(),
            ToolRiskTier.READ_ONLY,
        )
        header = review_header(decision).lower()
        self.assertNotIn("system action", header)
        self.assertIn("read", header)

    def test_header_for_a_privileged_change_names_the_change(self):
        from intergen.review_modal import review_header
        decision = verify_tool_call(
            _call("restart"), IngressTracker(), ConversationTrustState(),
            ToolRiskTier.PRIVILEGED_STATE_CHANGING,
        )
        self.assertIn("change", review_header(decision).lower())

    def test_header_without_a_tier_keeps_the_severe_wording(self):
        from intergen.review_modal import review_header
        decision = verify_tool_call(
            _call("restart"), IngressTracker(), ConversationTrustState(),
            ToolRiskTier.PRIVILEGED_STATE_CHANGING,
        )
        decision.risk_tier = None
        self.assertIn("change", review_header(decision).lower())


if __name__ == "__main__":
    unittest.main()
