# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""M7 follow-on — file-create gated-offer coverage restoration.

Trace-grounded from the live re-validation (fabrication_action class, do_for_me /
sf-offer ledger shape). Two natural phrasings routed llm_freeform and rendered a
completed-action claim that did not occur (nothing landed):
  A) "write a hello-world python script and save it"   (single-turn write-and-save)
  B) "create a file called report.md in my home folder" (named fresh create)

RULE #11 finding (OUR resolver coverage): the file branches of
detect_file_lifecycle_intent are BYTE-IDENTICAL across r48..r52 (verified by diff) —
no commit narrowed them. The gap is original-scope from 444dbf1a (r48): branch 2 was
authored explicit-path-only and branch 3 prior-draft-only. The r48 validation rep
("save it as temp.py in my home folder") is a TWO-turn save carrying a prior draft,
which masked the single-turn and named-file forms until the live natural-phrasing
re-validation exposed them.

FIX (M8-4 machinery, no widening, wave-5 defaults):
  B — branch 2b stages a gated write_file offer for a NAMED file with no explicit
      path (home default, or an explicit location tail).
  A — the artifact is model-generated, so the save is staged POST-generation
      (_maybe_stage_generate_and_save reuses branch 3 with the fresh answer as the
      draft). The r54 claim-screen is the NET — asserted here alongside the offer.

Execution byte-identical: offer detection + answer-shaping only; every staged offer
dispatches through the same ToolRegistry.execute gate.
"""

from __future__ import annotations

import os
import unittest

from intergen import safety
from intergen.router import (ConversationRouter, detect_file_lifecycle_intent,
                             _GENERATE_AND_SAVE_RE)
from intergen.semantic import SemanticMatcher
from intergen.llm import LLMRouter
from intergen.tool_registry import ToolRegistry

HOME = "/home/tester"


def _native_router():
    reg = ToolRegistry()
    reg.discover_tools()
    return ConversationRouter(
        tool_registry=reg, semantic_matcher=SemanticMatcher(embedder=None),
        llm=LLMRouter(config=None), lock_dispatch=False)


# ── B — named fresh-file create → gated write_file offer ────────────────────────
class NamedFileCreateTests(unittest.TestCase):
    def test_named_file_stages_write_file_offer(self):
        # The regressed live phrasing + siblings.
        for q, expect in [
            ("create a file called report.md in my home folder",
             os.path.join(HOME, "report.md")),
            ("make a notes.txt file", os.path.join(HOME, "notes.txt")),
            ("create a config.json file", os.path.join(HOME, "config.json")),
        ]:
            spec = detect_file_lifecycle_intent(q, home=HOME)
            self.assertIsNotNone(spec, q)
            self.assertEqual(spec["tool"], "write_file", q)
            self.assertEqual(spec["args"]["path"], expect, q)
            self.assertEqual(spec["args"]["content"], "", q)  # fresh empty create

    def test_location_tail_honored(self):
        spec = detect_file_lifecycle_intent(
            "create a file called config.json in my Downloads folder", home=HOME)
        self.assertEqual(spec["args"]["path"],
                         os.path.join(HOME, "Downloads", "config.json"))

    def test_bare_name_without_extension_declines(self):
        # "report" has no extension — ambiguous, so fall through to the model.
        self.assertIsNone(
            detect_file_lifecycle_intent(
                "create a file called report in my home folder", home=HOME))

    def test_directory_create_unaffected(self):
        spec = detect_file_lifecycle_intent("make a projects directory", home=HOME)
        self.assertEqual(spec["tool"], "run_command")  # mkdir, not write_file

    def test_route_stages_the_offer(self):
        r = _native_router()
        res = r._try_file_lifecycle(
            "create a file called report.md in my home folder", 0.0)
        self.assertIsNotNone(res)
        self.assertEqual(res.source, "file_lifecycle_offer")
        self.assertIsNotNone(r._pending_action_offer)  # gated, awaiting a bare yes


# ── A — single-turn write-and-save → post-generation gated save offer ───────────
class GenerateAndSaveTests(unittest.TestCase):
    ARTIFACT = ("Here is a hello world script:\n```python\n"
                "print(\"Hello, World!\")\n```")

    def test_gate_regex(self):
        for q in ("write a hello-world python script and save it",
                  "create a shell script and save it",
                  "compose a poem, then save it"):
            self.assertTrue(_GENERATE_AND_SAVE_RE.search(q), q)
        for q in ("what is python", "read my notes file", "save money this year"):
            self.assertFalse(_GENERATE_AND_SAVE_RE.search(q), q)

    def test_stages_save_offer_for_real_artifact(self):
        r = _native_router()
        line = r._maybe_stage_generate_and_save(
            "write a hello-world python script and save it", self.ARTIFACT)
        self.assertIsNotNone(line)
        self.assertIsNotNone(r._pending_action_offer)
        cmd, tool, _orig, args = r._pending_action_offer
        self.assertEqual(tool, "write_file")
        self.assertEqual(args["content"], self.ARTIFACT)   # the generated script
        self.assertTrue(args["path"].endswith("script.py"))  # python → .py default

    def test_skips_honesty_fallback(self):
        r = _native_router()
        line = r._maybe_stage_generate_and_save(
            "write a script and save it", safety.honest_action_fallback())
        self.assertIsNone(line)
        self.assertIsNone(r._pending_action_offer)  # never a bogus save

    def test_skips_non_save_turn(self):
        r = _native_router()
        self.assertIsNone(r._maybe_stage_generate_and_save(
            "what is the capital of France",
            "Paris is the capital of France and its largest city by far."))


# ── Regression guard — the r48-era two-turn save still works ─────────────────────
class TwoTurnSaveRegressionTests(unittest.TestCase):
    def test_two_turn_save_as_name(self):
        spec = detect_file_lifecycle_intent(
            "save it as temp.py in my home folder", prior_draft="SCRIPT", home=HOME)
        self.assertEqual(spec["tool"], "write_file")
        self.assertEqual(spec["args"]["path"], os.path.join(HOME, "temp.py"))
        self.assertEqual(spec["args"]["content"], "SCRIPT")  # content preserved

    def test_save_content_not_an_empty_create(self):
        # "save this text to a file called notes.txt" WITH a prior draft must carry the
        # CONTENT (branch 3), not fall to branch 2b's empty create.
        spec = detect_file_lifecycle_intent(
            "save this text to a file called notes.txt",
            prior_draft="THE TEXT", home=HOME)
        self.assertEqual(spec["args"]["path"], os.path.join(HOME, "notes.txt"))
        self.assertEqual(spec["args"]["content"], "THE TEXT")


# ── Belt — the r54 claim-screen net catches the fabricated completion (both layers)
class ClaimScreenNetTests(unittest.TestCase):
    def test_fabricated_completions_flagged_on_zero_dispatch(self):
        for draft in (
            "Here is the script saved directly to ~/hello.sh. I've made it "
            "executable and placed it in your home directory.",
            "I've created report.md in your home folder.",
        ):
            verdict, marker = safety.screen_execution_claim(draft, dispatched=False)
            self.assertEqual(verdict, "violation", draft)
            self.assertIsNotNone(marker)


if __name__ == "__main__":
    unittest.main()
