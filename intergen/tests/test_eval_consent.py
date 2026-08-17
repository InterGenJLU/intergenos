# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Eval-mode consent responder — arming contract, armed path, production parity.

Three obligations are pinned here, in the order the policy states them:

  a. PRODUCTION-PATH REGRESSION — with the responder unarmed, both consent gates
     behave exactly as they did before this module existed.
  b. ARMED PATH — an immediate recorded deny on both gates, with a glass event,
     in bounded time (never the one-hour fallback wedge).
  c. ARMING CONTRACT — arming is refused without the explicit marker, a malformed
     marker leaves production behavior, and there is no persistent or bus-side
     arming channel.

REACHABILITY (grounded against the tree these tests run in, and stated rather
than assumed — see the module-level notes in intergen/eval_consent.py):

  * ACTION-REVIEW gate — REACHABLE from a scenario-driven turn. live_run drives
    ``transport.ask`` -> the daemon's ``ask`` -> ``router.route(review_callback=)``
    -> ``tool_registry.execute``, which invokes the callback when the dispatcher
    holds a call for review, and immediately denies when the callback is None.
    This is the gate the stalled baselines actually hit.
  * PHONE-A-FRIEND SEND gate — NOT reachable from an Ask turn as the harness
    drives it today. ``prompt_send_consent`` is called from the daemon's separate
    ``escalate`` D-Bus method and from the streamed web path; ``router.route``
    only ATTACHES an escalation_offer string and never calls escalate itself.
    It is covered here anyway because the cost of covering it is one guard, and
    an unattended run that ever reaches Escalate (a future harness path, or a
    scenario driving that method directly) would otherwise block on a modal.
"""

from __future__ import annotations

import time
import unittest
from unittest import mock

from intergen import consent_modal, eval_consent


class _Call:
    """Minimal ToolCall stand-in — the responder reads name/arguments only."""

    def __init__(self, name="write_file", arguments=None):
        self.name = name
        self.arguments = arguments if arguments is not None else {"path": "/tmp/x"}


class _Decision:
    """Minimal DispatchDecision stand-in."""

    def __init__(self, needs_pkexec=False, reason="held for review"):
        self.needs_pkexec = needs_pkexec
        self.reason = reason
        self.effective_provenance = None


class _EvalConsentTestBase(unittest.TestCase):
    def setUp(self):
        eval_consent.disarm()
        self.addCleanup(eval_consent.disarm)


# ─── (c) ARMING CONTRACT ──────────────────────────────────────────────────────
class ArmingContractTests(_EvalConsentTestBase):
    def test_disarmed_by_default(self):
        self.assertFalse(eval_consent.is_armed())

    def test_arms_only_with_the_exact_marker(self):
        self.assertTrue(eval_consent.arm(eval_consent.ARM_MARKER))
        self.assertTrue(eval_consent.is_armed())

    def test_malformed_marker_is_refused_and_leaves_production_behavior(self):
        for bad in ("", "eval-consent-deny", "EVAL-CONSENT-DENY-V1",
                    "eval-consent-deny-v2", "yes", None, 1, object()):
            with self.subTest(marker=bad):
                self.assertFalse(eval_consent.arm(bad))
                self.assertFalse(eval_consent.is_armed())

    def test_refusal_is_recorded_not_silent(self):
        with mock.patch.object(eval_consent, "_emit") as emit:
            eval_consent.arm("nope")
        emit.assert_called_once()
        self.assertEqual(emit.call_args[0][0], "arm_refused")

    def test_arming_is_a_loud_glass_event(self):
        with mock.patch.object(eval_consent, "_emit") as emit:
            eval_consent.arm(eval_consent.ARM_MARKER)
        emit.assert_called_once()
        self.assertEqual(emit.call_args[0][0], "armed")

    def test_no_persistent_or_bus_arming_channel_exists(self):
        """The module exposes no env/config/bus arming surface.

        Arming is reachable only by calling arm() with the marker, which the
        daemon does exactly once, at construction, from its own command line.
        A regression that added an env-var or D-Bus arming path would show up
        here as a new public name.
        """
        public = {n for n in dir(eval_consent) if not n.startswith("_")}
        # Nothing in the public surface may read process env or accept a bus call.
        self.assertNotIn("arm_from_env", public)
        self.assertNotIn("arm_from_config", public)
        self.assertNotIn("ArmEvalConsent", public)
        source = open(eval_consent.__file__, encoding="utf-8").read()
        self.assertNotIn("os.environ", source)
        self.assertNotIn("getenv", source)

    def test_disarm_restores_production_and_clears_observations(self):
        eval_consent.arm(eval_consent.ARM_MARKER)
        eval_consent.review_verdict(_Call(), _Decision())
        self.assertTrue(eval_consent.observations())
        eval_consent.disarm()
        self.assertFalse(eval_consent.is_armed())
        self.assertEqual(eval_consent.observations(), [])


# ─── (b) ARMED PATH ───────────────────────────────────────────────────────────
class ArmedPathTests(_EvalConsentTestBase):
    def setUp(self):
        super().setUp()
        eval_consent.arm(eval_consent.ARM_MARKER)

    def test_review_gate_denies_immediately(self):
        started = time.monotonic()
        verdict = eval_consent.review_verdict(_Call(), _Decision())
        elapsed = time.monotonic() - started
        self.assertEqual(verdict, "deny")
        # Bounded far below review_modal.FALLBACK_TIMEOUT_SECONDS — the wedge is
        # never entered. A generous ceiling keeps this from being a flaky timer.
        self.assertLess(elapsed, 1.0)

    def test_review_gate_denies_privileged_calls_too(self):
        self.assertEqual(
            eval_consent.review_verdict(_Call(), _Decision(needs_pkexec=True)),
            "deny")

    def test_review_callback_matches_the_registry_contract(self):
        cb = eval_consent.make_review_callback()
        self.assertEqual(cb(_Call(), _Decision()), "deny")

    def test_send_gate_denies_immediately(self):
        self.assertIs(eval_consent.send_verdict("secret", "anthropic", "why"), False)

    def test_denial_is_recorded_with_gate_and_action(self):
        eval_consent.review_verdict(_Call(name="run_command"), _Decision())
        eval_consent.send_verdict("payload", "openai")
        rows = eval_consent.observations()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["gate"], eval_consent.GATE_ACTION_REVIEW)
        self.assertEqual(rows[0]["action"], "run_command")
        self.assertEqual(rows[0]["verdict"], "deny")
        self.assertEqual(rows[1]["gate"], eval_consent.GATE_PHONE_A_FRIEND_SEND)
        self.assertIn("openai", rows[1]["action"])

    def test_denial_emits_a_glass_event(self):
        with mock.patch.object(eval_consent, "_emit") as emit:
            eval_consent.review_verdict(_Call(), _Decision())
        emit.assert_called_once()
        self.assertEqual(emit.call_args[0][0], "denied")

    def test_send_denial_records_size_not_the_payload(self):
        """A recorded denial must not become a second copy of a would-be secret."""
        eval_consent.send_verdict("SUPERSECRETVALUE", "anthropic")
        row = eval_consent.observations()[0]
        self.assertEqual(row["content_chars"], len("SUPERSECRETVALUE"))
        self.assertNotIn("SUPERSECRETVALUE", repr(row))

    def test_summary_rolls_up_per_gate(self):
        eval_consent.review_verdict(_Call(), _Decision())
        eval_consent.review_verdict(_Call(), _Decision())
        eval_consent.send_verdict("x", "openai")
        summary = eval_consent.observation_summary()
        self.assertTrue(summary["armed"])
        self.assertEqual(summary["policy"], "deny_and_record")
        self.assertEqual(summary["denials"], 3)
        self.assertEqual(summary["per_gate"][eval_consent.GATE_ACTION_REVIEW], 2)

    def test_recording_failure_never_breaks_the_verdict(self):
        """A glass outage must not turn a denial into an exception mid-dispatch.

        Recording is best-effort by design; the VERDICT is not. If emission
        could raise out of _record, a glass problem would surface as a failed
        tool dispatch instead of a clean deny.
        """
        with mock.patch.object(eval_consent.glass, "emit",
                               side_effect=RuntimeError("glass down")):
            self.assertEqual(
                eval_consent.review_verdict(_Call(), _Decision()), "deny")
            self.assertIs(eval_consent.send_verdict("x", "openai"), False)
        # The observation is still recorded in-process even though glass failed.
        self.assertEqual(len(eval_consent.observations()), 2)


# ─── (a) PRODUCTION-PATH REGRESSION ───────────────────────────────────────────
class ProductionPathUnchangedTests(_EvalConsentTestBase):
    """Unarmed, both gates take the identical pre-existing code path."""

    def test_send_gate_unarmed_reaches_the_real_session_probe(self):
        with mock.patch.object(consent_modal, "_session_active",
                               return_value=False) as probe, \
                mock.patch.object(consent_modal, "_prompt_consent_libnotify",
                                  return_value=False) as fallback:
            self.assertFalse(consent_modal.prompt_send_consent("hi", "anthropic"))
        probe.assert_called_once()
        fallback.assert_called_once()

    def test_send_gate_unarmed_can_still_return_true_on_explicit_send(self):
        """The unarmed path is not deny-only — proving the guard is inert."""
        with mock.patch.object(consent_modal, "_session_active", return_value=True), \
                mock.patch.object(consent_modal.consent_dialog,
                                  "run_consent_dialog", return_value=True):
            self.assertTrue(consent_modal.prompt_send_consent("hi", "anthropic"))

    def test_send_gate_armed_never_reaches_the_session_probe(self):
        eval_consent.arm(eval_consent.ARM_MARKER)
        with mock.patch.object(consent_modal, "_session_active") as probe, \
                mock.patch.object(consent_modal.consent_dialog,
                                  "run_consent_dialog") as dialog:
            self.assertFalse(consent_modal.prompt_send_consent("hi", "anthropic"))
        probe.assert_not_called()
        dialog.assert_not_called()

    def test_unarmed_responder_records_nothing(self):
        with mock.patch.object(consent_modal, "_session_active", return_value=False), \
                mock.patch.object(consent_modal, "_prompt_consent_libnotify",
                                  return_value=False):
            consent_modal.prompt_send_consent("hi", "anthropic")
        self.assertEqual(eval_consent.observations(), [])
        self.assertFalse(eval_consent.observation_summary()["armed"])


if __name__ == "__main__":
    unittest.main()


# ─── DAEMON WIRING: the arming channel + the ask() seam ordering ──────────────
class DaemonWiringTests(_EvalConsentTestBase):
    """The daemon arms only from its own construction, and only with the marker."""

    def _daemon(self, **kw):
        from intergen.dbus_daemon import InterGenDaemon
        return InterGenDaemon(**kw)

    def test_default_construction_leaves_production_behavior(self):
        self._daemon()
        self.assertFalse(eval_consent.is_armed())

    def test_constructor_marker_arms(self):
        self._daemon(eval_consent_marker=eval_consent.ARM_MARKER)
        self.assertTrue(eval_consent.is_armed())

    def test_constructor_malformed_marker_does_not_arm(self):
        self._daemon(eval_consent_marker="please")
        self.assertFalse(eval_consent.is_armed())

    def test_status_reports_the_posture(self):
        import json as _json
        d = self._daemon(eval_consent_marker=eval_consent.ARM_MARKER)
        d._review_autopilot = None
        state = _json.loads(d.status())["eval_consent"]
        self.assertTrue(state["armed"])
        self.assertEqual(state["policy"], "deny_and_record")

    def test_status_reports_disarmed_in_production(self):
        import json as _json
        d = self._daemon()
        d._review_autopilot = None
        self.assertFalse(_json.loads(d.status())["eval_consent"]["armed"])

    def test_no_dbus_setter_for_consent_posture(self):
        """The standing invariant: consent posture cannot be flipped over the bus."""
        from intergen.dbus_daemon import InterGenDaemon
        names = {n.lower() for n in dir(InterGenDaemon)}
        for forbidden in ("armevalconsent", "set_eval_consent",
                          "arm_eval_consent", "set_review_autopilot"):
            self.assertNotIn(forbidden, names)

    def test_cli_flag_is_the_only_argv_arming_path(self):
        source = open(
            __import__("intergen.dbus_daemon", fromlist=["x"]).__file__,
            encoding="utf-8").read()
        self.assertIn("--eval-consent-deny", source)
        # The marker is never read from the process environment.
        self.assertNotIn('environ.get("INTERGEN_EVAL_CONSENT', source)


class AskSeamOrderingTests(_EvalConsentTestBase):
    """review_callback selection: harness override > eval-consent > production."""

    def _cb_for(self, override, armed):
        """Reproduce ask()'s selection ladder against the real conditions."""
        from intergen.dbus_daemon import InterGenDaemon
        d = InterGenDaemon()
        d._review_callback_override = override
        if armed:
            eval_consent.arm(eval_consent.ARM_MARKER)
        if d._review_callback_override is not None:
            return "override"
        if eval_consent.is_armed():
            return "eval_consent"
        return "production"

    def test_production_when_disarmed_and_no_override(self):
        self.assertEqual(self._cb_for(None, False), "production")

    def test_eval_consent_when_armed(self):
        self.assertEqual(self._cb_for(None, True), "eval_consent")

    def test_harness_override_still_wins(self):
        self.assertEqual(self._cb_for(lambda c, d: "allow_once", True), "override")


class DaemonEntryArgvTests(unittest.TestCase):
    """Pin the unit-launch argv contract (the r111 field start-failure).

    The packaged unit's ExecStart is ``/usr/bin/intergen daemon`` — a CLI
    subcommand, not a bare entry point. main()'s argparse must therefore
    receive only the arguments AFTER the subcommand word; parsing the raw
    process argv sees ``daemon`` itself and exits 2, which took the daemon
    down on every start of the first deployed build. These cases pin both
    halves: main() honors an explicit argv, and the CLI dispatch slices it.
    """

    def test_main_rejects_subcommand_word_before_construction(self):
        from intergen import dbus_daemon
        with mock.patch.object(dbus_daemon, "InterGenDaemon") as ctor:
            with self.assertRaises(SystemExit) as cm:
                dbus_daemon.main(["daemon"])
            self.assertEqual(cm.exception.code, 2)
            ctor.assert_not_called()

    def test_main_explicit_argv_arms_the_responder(self):
        from intergen import dbus_daemon
        with mock.patch.object(
                dbus_daemon, "InterGenDaemon",
                side_effect=RuntimeError("stop-after-construction")) as ctor:
            with self.assertRaises(RuntimeError):
                dbus_daemon.main(["--eval-consent-deny"])
            ctor.assert_called_once_with(
                eval_consent_marker=eval_consent.ARM_MARKER)

    def test_main_empty_argv_is_production(self):
        from intergen import dbus_daemon
        with mock.patch.object(
                dbus_daemon, "InterGenDaemon",
                side_effect=RuntimeError("stop-after-construction")) as ctor:
            with self.assertRaises(RuntimeError):
                dbus_daemon.main([])
            ctor.assert_called_once_with(eval_consent_marker=None)

    def test_cli_dispatch_passes_post_subcommand_args(self):
        import sys as _sys

        from intergen import cli
        with mock.patch("intergen.dbus_daemon.main") as dmain, \
                mock.patch.object(_sys, "argv",
                                  ["intergen", "daemon", "--eval-consent-deny"]):
            cli.main()
            dmain.assert_called_once_with(["--eval-consent-deny"])
