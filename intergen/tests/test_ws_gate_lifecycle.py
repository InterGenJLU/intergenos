# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Live WS gate-lifecycle cells — the F2-catching integration cut (harness PR1).

Drives the REAL panel /ws path against a LIVE daemon via WSGateClient and
asserts the universal per-turn liveness invariant plus the gate-lifecycle and
teaching-vs-action behaviours that F2 violated:

  * deny over the real round-trip -> the turn terminates with a non-empty
    refusal inside the deadline (the deny-hang would wedge here);
  * the universal liveness invariant holds on every turn, gated or not (the
    structural hang catch — independent of any authored scenario);
  * teaching phrasing ("how do I install <app>") routes to teaching and NEVER
    reaches the action gate (the F2 mis-route negative).

These need a live daemon + model, so they are OPT-IN: set INTERGEN_WS_HARNESS=1
on a box with a running daemon+model (e.g. .241/.218) to enable them. With the
env unset they skip — a normal suite run never drives, or hangs against, a
random daemon on :8089.

The deterministic, daemon-free companion is test_web_gate_deny_no_deadlock.py,
which pins the same deadlock at unit level (red on the pre-fix code).

The small local model is non-deterministic about emitting a clean tool call, so
gate-specific assertions are guarded by actually observing a gate (with a few
attempts); a run that never produces one SKIPS that cell honestly rather than
passing vacuously. The liveness invariant is asserted unconditionally.

Scope vs the grounded coverage matrix: these cells cover the F2-critical
{deny, allow, teaching-negative} outcomes over the /ws surface. The FULL matrix
— the 8-outcome tool-dispatch axis (executed-success/-fail, deny, gate-timeout,
cancel, policy-reject [the missing-provenance class], safety-decline,
malformed-reject) × phrasing(paraphrase-set) × gate-surface, with
missing-cell=fail — is PR3. The responder here already supports the timeout
(gate_action="ignore") and cancel branches for that buildout.

write_file / run_command gate outcomes are covered DETERMINISTICALLY, not here:
empirically (probed on the 2B) those tools do not reliably produce a live gate
over /ws — an imperative like "create a file …" makes the small model TEACH/
answer conversationally (no dispatch), and "run echo hello" classifies AUTO-safe
(tool_ack, no gate). So their gate-deny no-wedge is the authoritative coverage in
test_web_gate_deny_no_deadlock.py (cross-tool unit deny) + test_dbus_consent_surface.py
(dbus surface), and their teaching-negative in conversations.py — not a flaky live
cell. The live cells here use phrasings the 2B does reliably gate (service /
package actions).
"""

from __future__ import annotations

import asyncio
import os
import unittest

from intergen.tests.ws_harness import WSGateClient, daemon_reachable

_OPT_IN = os.environ.get("INTERGEN_WS_HARNESS") == "1"
_LIVE = _OPT_IN and daemon_reachable()
_SKIP_REASON = ("live WS gate cells are opt-in: set INTERGEN_WS_HARNESS=1 with a "
                "running daemon+model (e.g. .241/.218) to enable")

# A SECOND opt-in, for the one cell that ANSWERS A GATE WITH "ALLOW".
#
# Deny never escalates. Allow does: the registry routes an allowed privileged
# built-in through _dispatch_via_pkexec (tool_registry.py), pkexec asks polkit,
# and polkit raises an INTERACTIVE AUTHENTICATION DIALOG on the desktop of
# whoever is sitting at the machine. Measured 2026-09-16 on
# intergenos-192-r001-2: taking the INTERGEN_WS_HARNESS opt-in put a polkit
# prompt in front of the person using the box, who had to answer it.
#
# A test must never be able to ask a human anything. Nobody reads a test
# runner's terminal, a headless or unattended run has no one to answer, and a
# test that blocks on a person is not a test. So the allow branch does not run
# on the ordinary live opt-in; it runs only when a second variable says, in its
# own name, that an authentication prompt is expected and someone is there to
# answer it.
#
# This is a stated coverage gap, not a silent one: with the variable unset the
# cell SKIPS and the reason below says exactly what is not being checked. The
# deny, liveness and teaching-negative cells — the F2-critical ones — are
# unaffected and still run on the ordinary opt-in.
_ALLOW_OPT_IN = os.environ.get("INTERGEN_WS_ALLOW_PRIVILEGED") == "1"
_ALLOW_SKIP_REASON = (
    "answering a gate with ALLOW escalates through pkexec and raises an "
    "interactive polkit authentication dialog on the desktop of whoever is at "
    "the machine. NOT RUN, and the allow branch is therefore NOT verified by "
    "this run. Set INTERGEN_WS_ALLOW_PRIVILEGED=1 only on a box where an "
    "authentication prompt is expected and a person is present to answer it.")

# Phrasings the 2B tends to turn into a privileged tool call (and thus a gate).
_GATED_QUERIES = [
    "restart the sshd service",
    "enable bluetooth",
    "remove firefox",
]
_GATE_ATTEMPTS = 4


@unittest.skipUnless(_LIVE, _SKIP_REASON)
class WSGateLifecycleLiveTests(unittest.TestCase):
    def setUp(self):
        self.client = WSGateClient()

    def _drive(self, query, **kw):
        return asyncio.run(self.client.run_turn(query, **kw))

    def _drive_until_gate(self, decision):
        """Drive gated phrasings until one actually pops a gate.

        Returns the first WSTurnResult with saw_gate True, asserting liveness
        on each attempt along the way; None if no attempt gated.
        """
        last = None
        for attempt in range(_GATE_ATTEMPTS):
            q = _GATED_QUERIES[attempt % len(_GATED_QUERIES)]
            r = self._drive(q, gate_decision=decision)
            r.assert_live()  # liveness must hold whether or not it gated
            last = r
            if r.saw_gate:
                return r
        return None

    @staticmethod
    def _expected_refusal(r):
        """What the shipped refusal renderer composes for the denied call.

        The gate_prompt frame names the tool and carries its arguments, which is
        everything the renderer reads. `action` is capped at 200 characters on
        the wire, so a call whose arguments were truncated cannot be
        reconstructed; that raises here rather than silently weakening the
        assertion into a phrase match.
        """
        import json

        from intergen.interfaces.types import (
            Provenance, ToolCall, ToolResult,
        )
        from intergen.tool_registry import gate_refusal_message

        prompt = next((m for m in r.messages
                       if m.get("type") == "gate_prompt"), None)
        assert prompt is not None, "no gate_prompt frame in a turn that gated"
        args = json.loads(prompt.get("action") or "{}")
        call = ToolCall(name=prompt.get("tool_name", ""), arguments=args,
                        call_id=prompt.get("tool_call_id", ""),
                        source_of_request=Provenance.USER_DIRECT)
        denied = ToolResult(call_id=call.call_id, name=call.name,
                            content="", success=False, executed=False,
                            denied_by_user=True)
        # The renderer reads one thing from the tool object: the risk tier.
        # The live daemon's registry is not reachable from this process, so the
        # renderer is given None and takes its name-based classification — and
        # the frame carries the tier the DAEMON computed, so the two are
        # compared here instead of assumed equal. A mismatch fails loudly.
        from intergen.tool_registry import _classify_risk_tier
        here = _classify_risk_tier(None, args, call.name)
        assert here.value == prompt.get("risk_tier"), (
            f"classification differs from the daemon's: {here.value} here vs "
            f"{prompt.get('risk_tier')} on the card")
        return gate_refusal_message(call, denied, None)

    def test_universal_liveness_invariant(self):
        # The structural F2 catch: every turn terminates non-empty inside the
        # deadline. A wedged turn (the pre-fix deny-hang) fails this even with
        # no deny authored. Mix of fast-path, teaching, and gate-eligible.
        for q in ("what is my hostname",
                  "how do I install zoom",
                  "restart the sshd service"):
            r = self._drive(q, gate_decision="deny")
            r.assert_live()

    def test_deny_terminates_with_refusal_not_wedge(self):
        r = self._drive_until_gate("deny")
        if r is None:
            self.skipTest(
                f"model did not emit a gated tool call in {_GATE_ATTEMPTS} "
                "attempts — gate path not exercised this run")
        # The gate resolved as a deny and the turn ended with a non-empty,
        # user-visible refusal — promptly, not after a heartbeat-reaped close.
        self.assertEqual(r.gate_resolved_decision, "deny")
        self.assertTrue(r.liveness_ok)
        self.assertEqual(r.closed_by, "client")
        self.assertTrue(r.text.strip(), "deny produced no user-visible reply")
        # Content half: liveness catches the raw wedge; this asserts the RIGHT
        # recovery, not merely that *something* terminal arrived.
        #
        # THE DEFECT THIS CELL MEASURED, 2026-09-16: the whole answer was
        # "Tool call denied by user via review modal." — the registry's own
        # audit record of what the person had just done, delivered verbatim.
        # It is named here so it can never come back quietly.
        self.assertNotIn("denied by user via review modal", r.text.lower(),
                         f"the person was shown the audit string: {r.text!r}")
        # And the answer is the one the single refusal renderer produces for
        # the very call the card was shown for. Reconstructing the call from
        # the gate_prompt frame makes this an EQUALITY against the shipped
        # renderer rather than a search for a hopeful phrase, so a refusal that
        # is merely plausible — or that drifts on one surface only — fails.
        self.assertEqual(r.text.strip(), self._expected_refusal(r).strip(),
                         f"deny did not produce the refusal the shared "
                         f"renderer composes for this call: {r.text!r}")

    @unittest.skipUnless(_ALLOW_OPT_IN, _ALLOW_SKIP_REASON)
    def test_allow_resolves_and_terminates(self):
        r = self._drive_until_gate("allow")
        if r is None:
            self.skipTest(
                f"model did not emit a gated tool call in {_GATE_ATTEMPTS} "
                "attempts — gate path not exercised this run")
        # Allow may execute or fail-closed without a dispatch key, but either
        # way the turn must resolve the gate and reach a clean terminal.
        self.assertIn(r.gate_resolved_decision,
                      ("allow", "allow_conversation"))
        self.assertTrue(r.liveness_ok)

    def test_teaching_phrasing_never_reaches_action_gate(self):
        # F2 mis-route negative: instructional phrasing must teach the command,
        # not pop the action gate for an empty install.
        r = self._drive("how do I install zoom", gate_decision="deny")
        r.assert_live()
        self.assertFalse(
            r.saw_gate,
            "teaching phrasing reached the action gate (F2 mis-route)")
        self.assertIn("pkm", r.text.lower(),
                      "teaching answer did not surface the pkm command")


if __name__ == "__main__":
    unittest.main()
