# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Consent + honesty wave (decided 2026-07-24) — four fixes off two live
dogfood transcripts, each root-caused in code:

  A — M3(i) acceptance-restating tail: "yes, please check" over a live offer
      is the acceptance and EXECUTES; only a tail with new content keeps the
      offer armed and routes (offer-consent execution integrity — consent is
      never conditioned on magic phrasing).
  B — toolless diagnostic modifier: a with_tools=False generation must never
      be ORDERED to use tools ("Act immediately" on a toolless turn was a
      fabrication recipe); the toolless twin orders honesty instead.
  C — claim_screen verification verbs: "I checked lsblk and df -h" is an
      action claim (action-claim => tool span) and must flag on a
      zero-dispatch turn; documentation references stay unflagged.
  D — M6 ceilings re-baselined (8 of 9 paths were warn-firing on stale
      baselines); the meters must sit above the measured sizes again.

Deterministic unit tests; router partially constructed per test_offer_accept.py.
"""

from __future__ import annotations

import unittest

from intergen.llm import (build_system_prompt, system_prompt_char_budget,
                          _SYSTEM_PROMPT_CHAR_BUDGETS)
from intergen.memory import MemoryManager
from intergen.router import ConversationRouter, RouteResult
from intergen.safety import screen_execution_claim


def _bare_router() -> ConversationRouter:
    r = ConversationRouter.__new__(ConversationRouter)
    r._turn_index = None
    r._pending_action_offer = None
    r._pending_ipv6_offer = None
    r._pending_memory_offer = None
    r._record = lambda *a, **k: None
    return r


class AcceptanceRestatingTailMatcher(unittest.TestCase):
    """Fix A, the matcher: pro-verb/politeness-only tails restate acceptance;
    one residual content word fails the full match."""

    def test_restating_tails_match(self):
        for tail in ("please check", "please", "go ahead", "do it", "check",
                     "check it", "run it", "go ahead, thanks", "sure, do it",
                     "please do it now", "verify", "look", ""):
            self.assertTrue(
                MemoryManager.is_acceptance_restating_tail(tail), tail)

    def test_content_tails_do_not_match(self):
        for tail in ("and also show me disk usage",
                     "check the other printer too",
                     "but not now",
                     "what about memory?",
                     "please check the logs first",
                     "can you also update the system"):
            self.assertFalse(
                MemoryManager.is_acceptance_restating_tail(tail), tail)


class PrefixedYesAcceptanceExecutes(unittest.TestCase):
    """Fix A, the router table: the live-reproduced 'yes, please check'
    executes the staged command; a content tail still keeps the offer armed."""

    def _staged(self):
        r = _bare_router()
        r._pending_action_offer = ("lpstat -p -d", "run_command",
                                   "do I have any printers configured")
        captured = {}

        def _fake_run(cmd):
            captured["cmd"] = cmd
            return RouteResult(text="no destinations",
                               source="explain_offer_run", handled=True)

        r._run_staged_command = _fake_run
        return r, captured

    def test_yes_please_check_executes(self):
        # The live transcript's exact turn 2 (2026-07-24 11:48).
        r, captured = self._staged()
        res = r._resolve_pending_action_offer("yes, please check", 0.0)
        self.assertIsNotNone(res)
        self.assertEqual(captured["cmd"], "lpstat -p -d")
        self.assertIsNone(r._pending_action_offer)

    def test_restating_variants_execute(self):
        for msg in ("yes, go ahead", "yeah, do it", "sure, check it",
                    "ok, run it", "yes please, go ahead"):
            r, captured = self._staged()
            res = r._resolve_pending_action_offer(msg, 0.0)
            self.assertIsNotNone(res, msg)
            self.assertEqual(captured.get("cmd"), "lpstat -p -d", msg)

    def test_content_tail_still_keeps_offer_armed(self):
        # The hazard-kill retained: a new ask never fires the staged command.
        r, captured = self._staged()
        res = r._resolve_pending_action_offer(
            "yes, and also show me disk usage", 0.0)
        self.assertIsNone(res)                       # tail routes on its merits
        self.assertNotIn("cmd", captured)            # nothing executed
        self.assertIsNotNone(r._pending_action_offer)  # offer still armed

    def test_bare_yes_unchanged(self):
        r, captured = self._staged()
        res = r._resolve_pending_action_offer("yes", 0.0)
        self.assertIsNotNone(res)
        self.assertEqual(captured["cmd"], "lpstat -p -d")


class ToollessDiagnosticModifier(unittest.TestCase):
    """Fix B: the diagnostic path's tool order never ships toolless."""

    def test_with_tools_keeps_action_directive(self):
        p = build_system_prompt("diagnostic", with_tools=True)
        self.assertIn("Act immediately", p)

    def test_toolless_orders_honesty_not_action(self):
        p = build_system_prompt("diagnostic", with_tools=False)
        self.assertNotIn("Act immediately", p)
        self.assertNotIn("Use your tools", p)
        self.assertIn("could not check right now", p)
        self.assertIn("NEVER say you checked", p)

    def test_no_modifier_orders_tools_on_any_toolless_path(self):
        # The general guard the override table exists for: no toolless prompt
        # may command tool use it cannot back.
        for qt in ("general", "identity", "diagnostic", "safety", "system_map"):
            p = build_system_prompt(qt, with_tools=False)
            self.assertNotIn("Use your tools", p, qt)
            self.assertNotIn("Act immediately", p, qt)


class VerificationClaimScreen(unittest.TestCase):
    """Fix C: verification claims are action claims (action-claim => tool span)."""

    def test_live_fabrication_flags(self):
        # The live transcript's exact fabricated line (2026-07-24 11:53).
        verdict, marker = screen_execution_claim(
            "I checked lsblk and df -h — you have one disk, /dev/sda (2.0TB), "
            "with root on sda2.", dispatched=False)
        self.assertEqual(verdict, "violation")
        self.assertIsNotNone(marker)

    def test_verification_variants_flag(self):
        for draft in ("I've verified the service is running.",
                      "I just checked the logs and everything is fine.",
                      "I double-checked the disk usage for you.",
                      "I examined the journal — no errors.",
                      "I looked at the network interfaces and all are up."):
            verdict, _ = screen_execution_claim(draft, dispatched=False)
            self.assertEqual(verdict, "violation", draft)

    def test_honest_and_out_of_scope_stay_clean(self):
        for draft in ("I couldn't check right now — run `lsblk` to see your disks.",
                      "I have not verified the logs this turn.",
                      "You should check df -h for disk usage.",
                      "I checked the wiki for you and it covers this.",
                      "To check disk usage, run `df -h`."):
            verdict, marker = screen_execution_claim(draft, dispatched=False)
            self.assertEqual(verdict, "clean", f"{draft!r} -> {marker!r}")

    def test_dispatched_turn_stays_clean(self):
        verdict, _ = screen_execution_claim(
            "I checked lsblk and df -h — one disk.", dispatched=True)
        self.assertEqual(verdict, "clean")


class BudgetsSitAboveMeasured(unittest.TestCase):
    """Fix D: every pinned ceiling clears its path's measured size — a meter
    that always fires measures nothing."""

    def test_every_path_under_its_ceiling(self):
        for (qt, wt), ceiling in _SYSTEM_PROMPT_CHAR_BUDGETS.items():
            measured = len(build_system_prompt(qt, wt))
            self.assertLessEqual(
                measured, ceiling,
                f"({qt}, {wt}): measured {measured} > ceiling {ceiling}")
            self.assertEqual(system_prompt_char_budget(qt, wt), ceiling)


if __name__ == "__main__":
    unittest.main()
