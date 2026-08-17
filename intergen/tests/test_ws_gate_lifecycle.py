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
        # recovery, not merely that *something* terminal arrived. A denied gated
        # action takes the deterministic friendly refusal
        # (web_server.py:1476-1498), never an error or raw jargon.
        self.assertIn("not able to do that from here", r.text.lower(),
                      f"deny did not produce the friendly refusal: {r.text!r}")

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
