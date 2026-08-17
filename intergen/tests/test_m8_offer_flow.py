# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""M8 wave 5 — offer-flow quality (offer_flow_review class).

Trace-grounded from the discovery ledger (m8-wave1-ledger, class=offer_flow_review,
22 HIGH findings, all do_for_me). Fixtures-vs-tip split established by driving the
router in native posture at r50 (this suite pins that split):

  RESOLVED-BY-PRIOR-WAVE (18/22) — at r50 these route to a gated offer, not the
  pre-M8-1 freeform hedge/fabrication the ledger captured:
    * install neovim / remove transmission / restart sshd / enable bluetooth /
      disable cups / save shopping list / write+save a script  -> source=llm_tools
      (M8-1 eligibility: the model dispatches the tool, gated), and
    * update all my packages -> source=explain with a staged run offer.
    The freeform over-refusal ("transmission is a critical component" — a fabricated
    fact) and the "I don't have current data" hedges were model freeform on a pre-M8-1
    tip; at r50 the request is tool-eligible, so the fabrication/hedge is structurally
    routed onto the gated-dispatch path. (Live-9B answer QUALITY is the 9B-seat leg.)

  FIXED THIS WAVE (deterministic, 3/22) — the inline-named directory create:
    * "make a projects directory in my home folder" (sf-offer-bare-yes-5 /
      -decline-5 / -prefixed-5). The M8-4 belt's directory branch needed an explicit
      "named/called X" clause, so the far more common inline form ("a projects
      directory") fell to the model, which then interrogated for a name it could
      default. detect_file_lifecycle_intent now recognises the inline name and stages
      a gated `mkdir -p ~/<name>` offer with a SENSIBLE DEFAULT location (home) — no
      interrogation. Reuses the M8-4 offer machinery (run_command / _run_staged_action);
      no execution widening.

  DEFENSIBLE-TEACHING (1/22): "take a screenshot" (sf-offer-bare-yes-7) routes to an
    honest teaching answer (how to capture + where saved) — no fabrication, no hedge,
    no over-refusal. A gated take_screenshot dispatch offer would require widening
    _run_staged_action to a new tool (out of the reuse-don't-widen constraint), so the
    honest teaching answer stands.

Execution gating byte-identical: this leg only extends pre-model offer detection;
the offer dispatches through the same CONFIRM gate.
"""

from __future__ import annotations

import unittest
from unittest import mock

from intergen.interfaces.types import SafetyTier
from intergen.router import (ConversationRouter, detect_file_lifecycle_intent)
from intergen.semantic import SemanticMatcher
from intergen.llm import LLMRouter
from intergen.tool_registry import ToolRegistry

HOME = "/home/tester"


def _native_router():
    """A real router in NATIVE (unlocked) posture, embedder-free — drives the
    deterministic pre-model belt + the M8-1 eligibility decision (decide_only)."""
    reg = ToolRegistry()
    reg.discover_tools()
    return ConversationRouter(
        tool_registry=reg, semantic_matcher=SemanticMatcher(embedder=None),
        llm=LLMRouter(config=None), lock_dispatch=False)


class InlineDirDetectionTests(unittest.TestCase):
    """detect_file_lifecycle_intent recognises an inline-named directory with a
    sensible default location — the still-FAIL do_for_me shape."""

    def _spec(self, text):
        return detect_file_lifecycle_intent(text, prior_draft=None, home=HOME)

    def test_inline_named_dir_defaults_to_home(self):
        for text, name in (
                ("make a projects directory in my home folder", "projects"),
                ("create a scripts folder", "scripts"),
                ("make me a backup directory", "backup"),
                ("create a my-project directory", "my-project")):
            spec = self._spec(text)
            self.assertIsNotNone(spec, text)
            self.assertEqual(spec["tool"], "run_command")
            self.assertEqual(spec["display"], f"mkdir -p {HOME}/{name}")
            self.assertEqual(spec.get("default_applied"), "home")

    def test_no_obvious_name_falls_through(self):
        # "new"/"empty" occupy the name slot but do NOT name a directory → no obvious
        # default → fall through to the model (never a mkdir ~/new offer).
        for text in ("make a new folder", "create an empty directory",
                     "make another folder"):
            self.assertIsNone(self._spec(text), text)

    def test_precision_non_directory_asks_ignored(self):
        for text in ("make a config file", "write me a note",
                     "install neovim for me", "remove the transmission package"):
            self.assertIsNone(self._spec(text), text)

    def test_existing_named_clause_branch_preserved(self):
        # The count + "named …" branch is unchanged (12-folders M8-4 case).
        spec = self._spec("make me 12 folders named january through december")
        self.assertIsNotNone(spec)
        self.assertIn("January", spec["display"])
        self.assertIn("December", spec["display"])


class OfferFlowRouteTests(unittest.TestCase):
    """Native-posture routing: the inline-dir ask now stages a clean gated offer;
    action shapes still route on to M8-1 dispatch (not hijacked by the belt)."""

    @classmethod
    def setUpClass(cls):
        cls.r = _native_router()

    def _route(self, text):
        self.r._pending_action_offer = None
        res = self.r.route(text, decide_only=True)
        return res, getattr(self.r, "_pending_action_offer", None)

    def test_projects_dir_stages_clean_offer(self):
        res, offer = self._route("make a projects directory in my home folder")
        self.assertEqual(res.source, "file_lifecycle_offer")
        self.assertTrue(res.handled)
        self.assertIsNotNone(offer)
        self.assertTrue(offer[0].startswith("mkdir -p "))
        self.assertIn("projects", offer[0])

    def test_action_shape_routes_on_to_m8_1(self):
        # A package/service do_for_me action is NOT hijacked by the file belt — it
        # stays tool-eligible (M8-1), which is the resolved-by-prior-wave path.
        for text in ("install neovim for me", "restart sshd",
                     "remove the transmission package"):
            res, _ = self._route(text)
            self.assertEqual(res.source, "llm_tools", text)

    def test_glass_default_applied_visible(self):
        with mock.patch("intergen.router.glass.emit") as emit:
            self._route("create a scripts folder")
        rows = [c for c in emit.call_args_list
                if c.args[:2] == ("decision", "file_lifecycle_offer")]
        self.assertTrue(rows, "a file_lifecycle_offer glass row must fire")
        self.assertEqual(rows[-1].kwargs.get("detail", {}).get("default_applied"),
                         "home")


class GatingRegressionTests(unittest.TestCase):
    """Offer detection only — tool safety classification byte-identical (waves 1-4)."""

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
