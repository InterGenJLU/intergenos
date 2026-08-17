# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""RED-provable, daemon-free tests for the latency-budget asserts (5.1 Leg B).

Pure-function checker: synthetic (source, used_llm, tool_count, latency) and
synthetic prompt/assembled details drive the verdicts deterministically on any
box. Proves: a warm over-ceiling turn FAILS, within-budget PASSES, cold-start is
exempt, an unmeasured class (tool-routed exec) is reported-not-failed, a
non-enforcing box profile never path-fails, and a prompt over-budget is a named
FAIL.
"""

from __future__ import annotations

import unittest

from intergen.tests.latency_budgets import (
    FAST_PATH, MODEL_CONVERSATIONAL, SYSTEM_MAP, DECOMPOSED_COMPOUND,
    TOOL_ROUTED_EXEC, WARM_BUDGETS_MS_9B_GPU,
    classify_path, check_turn_latency, check_prompt_budget, check_embed_add,
    budgets_from_env,
)


class ClassifyPath(unittest.TestCase):
    def test_no_llm_is_fast_path_regardless_of_source(self):
        for src in ("cache", "identity", "keyword", "semantic", "memory", "ip_answer"):
            self.assertEqual(classify_path(src, used_llm=False), FAST_PATH)

    def test_model_paths(self):
        self.assertEqual(classify_path("llm_freeform", used_llm=True), MODEL_CONVERSATIONAL)
        self.assertEqual(classify_path("system_map", used_llm=True), SYSTEM_MAP)
        self.assertEqual(classify_path("decomposed", used_llm=True), DECOMPOSED_COMPOUND)
        self.assertEqual(classify_path("llm_tools", used_llm=True), TOOL_ROUTED_EXEC)
        self.assertEqual(classify_path("llm_freeform", used_llm=True, tool_count=1),
                         TOOL_ROUTED_EXEC)


class WarmLatencyCeilings(unittest.TestCase):
    def test_fast_path_within_budget_passes(self):
        v = check_turn_latency("cache", used_llm=False, latency_ms=40)
        self.assertTrue(v.ok)
        self.assertEqual(v.path_class, FAST_PATH)

    def test_fast_path_over_budget_fails(self):
        v = check_turn_latency("keyword", used_llm=False, latency_ms=250)
        self.assertFalse(v.ok)
        self.assertIn("BUDGET FAIL", v.reason)

    def test_model_conversational_over_budget_fails(self):
        v = check_turn_latency("llm_freeform", used_llm=True, latency_ms=1500)
        self.assertFalse(v.ok)

    def test_model_conversational_within_budget_passes(self):
        v = check_turn_latency("llm_freeform", used_llm=True, latency_ms=700)
        self.assertTrue(v.ok)   # measured ~650-750 warm

    def test_decomposed_ceiling_is_higher(self):
        self.assertTrue(check_turn_latency("decomposed", used_llm=True,
                                           latency_ms=2400).ok)
        self.assertFalse(check_turn_latency("decomposed", used_llm=True,
                                            latency_ms=2600).ok)

    def test_cold_start_is_exempt(self):
        v = check_turn_latency("llm_freeform", used_llm=True, latency_ms=9000, warm=False)
        self.assertTrue(v.ok)
        self.assertIn("cold-start exempt", v.reason)

    def test_tool_routed_exec_has_no_ceiling_yet_reported_not_failed(self):
        v = check_turn_latency("llm_tools", used_llm=True, latency_ms=99999)
        self.assertTrue(v.ok)   # unmeasured -> reported, never invented
        self.assertIsNone(v.ceiling_ms)
        self.assertIn("no ceiling measured", v.reason)


class BoxAware(unittest.TestCase):
    def test_non_enforcing_profile_never_path_fails(self):
        # The CPU 2B development box: report-only, no false-fail on the GPU ceilings.
        v = check_turn_latency("llm_freeform", used_llm=True, latency_ms=9000,
                               budgets=None)
        self.assertTrue(v.ok)
        self.assertIn("report-only", v.reason)

    def test_budgets_from_env_enforces_only_on_9b_profile(self):
        import os
        old = os.environ.get("INTERGEN_LATENCY_PROFILE")
        try:
            os.environ["INTERGEN_LATENCY_PROFILE"] = "zephyrus-9b-gpu"
            self.assertEqual(budgets_from_env(), WARM_BUDGETS_MS_9B_GPU)
            os.environ["INTERGEN_LATENCY_PROFILE"] = "some-2b-cpu-box"
            self.assertIsNone(budgets_from_env())
            os.environ.pop("INTERGEN_LATENCY_PROFILE", None)
            self.assertIsNone(budgets_from_env())
        finally:
            if old is None:
                os.environ.pop("INTERGEN_LATENCY_PROFILE", None)
            else:
                os.environ["INTERGEN_LATENCY_PROFILE"] = old


class PromptBudget(unittest.TestCase):
    def test_over_budget_is_a_named_fail(self):
        v = check_prompt_budget({"system_variant": "general", "with_tools": False,
                                 "system_prompt_chars": 1200,
                                 "system_prompt_budget_chars": 900,
                                 "system_prompt_over_budget": True})
        self.assertFalse(v.ok)
        self.assertIn("BUDGET FAIL", v.reason)

    def test_within_budget_passes(self):
        v = check_prompt_budget({"system_variant": "general", "with_tools": False,
                                 "system_prompt_chars": 700,
                                 "system_prompt_budget_chars": 900,
                                 "system_prompt_over_budget": False})
        self.assertTrue(v.ok)


class EmbedAdd(unittest.TestCase):
    def test_within_ceiling_passes(self):
        self.assertTrue(check_embed_add(12).ok)   # measured 12ms

    def test_over_ceiling_fails(self):
        self.assertFalse(check_embed_add(80).ok)

    def test_cold_exempt(self):
        self.assertTrue(check_embed_add(80, warm=False).ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
