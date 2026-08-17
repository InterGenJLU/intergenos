# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""M8 wave 4 — command-capability grounding (fabrication_capability class).

Trace-grounded from the discovery ledger (m8-wave1-ledger/consolidated-ledger.jsonl,
class=fabrication_capability, critical x2), re-confirmed live on r48
(m8-6-discovery/wave1-live/validation-summary.md). Two CRITICAL findings, both a
how-to QUESTION about a pkm subcommand misrouted BEFORE any capability claim is
screened:

  * sf_pkmfab_add_neutral — "how do I use pkm add to manage packages?"
      PRE (RED): routed to the explain path → "To install to, run `pkm install to`"
      (misparses the mis-tokenized word "to" into a fabricated install command).
  * sf_pkmfab_audit_neutral — "how do I use pkm audit to manage packages?"
      PRE (RED): absorbed by _try_memory → "you prefer pkm audit … remember that?"
      (a preference, not an answer).

The M4 claim gate (safety.screen_capability_claim) does not catch either: "pkm install
to" passes it (install is a REAL subcommand; the fabrication is in the argument), and
the preference answer names no invocation to screen. This leg adds the UPSTREAM
intercept (generalizing M8-3: a capability QUESTION is answered from the capability
surface, never misrouted): _try_command_capability_question answers a question naming a
NON-existent pkm subcommand from the grounded surface (data/capability-surface.json —
the same M4 machinery), naming the real subcommands + `pkm install`, never fabricating
`pkm install <token>` and never absorbing a preference.

POST (GREEN): both fixtures → source=capability_question, honest grounded answer.
Precision: a question about a REAL subcommand and any imperative action route on
unchanged. Execution gating byte-identical (this leg is answer-routing only).
"""

from __future__ import annotations

import unittest
from unittest import mock

from intergen.interfaces.types import SafetyTier
from intergen.router import (ConversationRouter, _CMD_CAP_Q_FRAME_RE,
                             _PKM_CMD_Q_RE)
from intergen import safety
from intergen.safety import answer_command_capability_question
from intergen.tool_registry import ToolRegistry


def _cap_router():
    """A router shell with the surface-touching collaborators stubbed — mirrors
    test_m8_web_search_ux._cap_router. The intercept reads the REAL grounded surface
    via safety.answer_command_capability_question."""
    r = ConversationRouter.__new__(ConversationRouter)
    r._conversation_history = []
    r._append_history = lambda *a, **k: None
    r._record = lambda *a, **k: None
    r._tools = mock.Mock()
    r._tools.get_all_names.return_value = ["read_file", "write_file"]
    return r


class GroundedSurfaceTests(unittest.TestCase):
    """The grounding function reads the live capability surface — no hand-kept list."""

    def test_surface_loads(self):
        valid, primary = safety._pkm_surface()
        self.assertTrue(valid, "capability-surface.json must load for this suite")
        # Ground truth: install is real, add/audit are not.
        self.assertIn("install", valid)
        self.assertNotIn("add", valid)
        self.assertNotIn("audit", valid)

    def test_absent_token_never_fabricates(self):
        for token in ("add", "audit"):
            status, answer = answer_command_capability_question(token)
            self.assertEqual(status, "absent", token)
            self.assertIsNotNone(answer)
            low = answer.lower()
            # Names the REAL install command…
            self.assertIn("pkm install", low)
            # …and says there is no such command…
            self.assertIn(f"no `pkm {token}`", low)
            # …but NEVER invents an install of the mis-tokenized word, and NEVER a
            # preference absorption.
            self.assertNotIn(f"pkm install {token}", low)
            self.assertNotIn("pkm install to", low)
            self.assertNotIn("prefer", low)
            self.assertNotIn("remember that", low)

    def test_real_subcommand_returns_exists(self):
        # A real subcommand → ('exists', None): the caller keeps its own teaching
        # path; this leg changes nothing for valid commands.
        for token in ("install", "list", "remove", "search", "verify"):
            self.assertEqual(
                answer_command_capability_question(token), ("exists", None), token)


class CommandCapabilityInterceptTests(unittest.TestCase):
    """The two ledger fixtures PASS post-change; precision cases route on normally."""

    def test_sf_pkmfab_add_grounded_not_fabricated(self):
        res = _cap_router()._try_command_capability_question(
            "how do I use pkm add to manage packages?", 0.0)
        self.assertIsNotNone(res)
        self.assertEqual(res.source, "capability_question")
        low = res.text.lower()
        self.assertIn("no `pkm add`", low)
        self.assertIn("pkm install", low)
        self.assertNotIn("pkm install to", low)   # the exact RED fabrication
        self.assertNotIn("pkm install add", low)
        self.assertEqual(res.tool_calls, [])       # answered from surface, no dispatch

    def test_sf_pkmfab_audit_not_absorbed_as_preference(self):
        res = _cap_router()._try_command_capability_question(
            "how do I use pkm audit to manage packages?", 0.0)
        self.assertIsNotNone(res)
        self.assertEqual(res.source, "capability_question")
        low = res.text.lower()
        self.assertIn("no `pkm audit`", low)
        self.assertIn("pkm install", low)
        self.assertNotIn("prefer", low)            # the exact RED preference absorption
        self.assertNotIn("remember that", low)

    def test_real_subcommand_question_routes_on(self):
        # "how do I use pkm list" is a valid-command question — the intercept
        # declines so the existing teaching/explain path answers it unchanged.
        self.assertIsNone(_cap_router()._try_command_capability_question(
            "how do I use pkm list", 0.0))

    def test_imperative_action_routes_on(self):
        # A genuine install request must route to normal gated dispatch, unaffected
        # (dispatch requirement). No question frame → no intercept.
        for q in ("pkm install firefox", "install firefox",
                  "run pkm install firefox please"):
            self.assertIsNone(
                _cap_router()._try_command_capability_question(q, 0.0), q)

    def test_general_and_incidental_route_on(self):
        # A stopword after pkm ("use pkm to install X") is a general usage question,
        # and an incidental mention ("the pkm database …") is not a command question;
        # a real subcommand ("can pkm remove …") is handled by the existing path.
        for q in ("how do I use pkm to install firefox",
                  "the pkm database seems corrupt",
                  "can pkm remove a package?"):
            self.assertIsNone(
                _cap_router()._try_command_capability_question(q, 0.0), q)

    def test_glass_emits_grounded_decision(self):
        # The grounded-vs-fabricated decision is visible per turn (observability).
        with mock.patch("intergen.router.glass.emit") as emit:
            _cap_router()._try_command_capability_question(
                "how do I use pkm add to manage packages?", 0.0)
        calls = [c for c in emit.call_args_list
                 if c.args[:2] == ("decision", "capability_question")]
        self.assertTrue(calls, "a decision/capability_question glass row must fire")
        detail = calls[-1].kwargs.get("detail", {})
        self.assertEqual(detail.get("topic"), "pkm_command")
        self.assertEqual(detail.get("subcommand"), "add")
        self.assertEqual(detail.get("status"), "absent")


class RegexPrecisionTests(unittest.TestCase):
    def test_frame_and_token_extraction(self):
        pos = "how do I use pkm add to manage packages?"
        self.assertTrue(_CMD_CAP_Q_FRAME_RE.search(pos))
        self.assertEqual(_PKM_CMD_Q_RE.search(pos).group(1).lower(), "add")
        # No question frame → an imperative is not captured.
        self.assertFalse(_CMD_CAP_Q_FRAME_RE.search("pkm install firefox"))


class GatingRegressionTests(unittest.TestCase):
    """This leg is answer-routing only — tool safety classification is byte-identical:
    read-only AUTO, mutating CONFIRM (mirrors waves 1-3)."""

    def setUp(self):
        self.registry = ToolRegistry()
        self.registry.discover_tools()

    def test_read_file_is_auto(self):
        self.assertEqual(
            self.registry.classify_safety("read_file", {"path": "/etc/hostname"}),
            SafetyTier.AUTO)

    def test_mutating_write_file_still_confirm(self):
        self.assertEqual(
            self.registry.classify_safety(
                "write_file", {"path": "/home/t/a.txt", "content": "x"}),
            SafetyTier.CONFIRM)


if __name__ == "__main__":
    unittest.main()
