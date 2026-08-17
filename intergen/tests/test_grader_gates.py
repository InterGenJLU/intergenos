# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Two-gate grader split — Gate A (routing/structural, hard) vs Gate B (quality, soft).

Gate A failures mean the engine made a wrong DECISION (wrong route / wrong tool)
and hard-fail the turn; Gate B failures are quality nits that stay MIXED and are
reported separately. These tests pin the classification + the gate-aware grade.
"""

from __future__ import annotations

import unittest

from intergen.tests.grader import (
    AssertionResult, gate_for, grade_turn,
    compute_gate_grades, compute_turn_grade, GATE_A_TYPES,
)
from intergen.tests.conversations import Assertion


def _r(type_, passed, gate="B"):
    return AssertionResult(type=type_, value="", passed=passed, gate=gate)


class GateClassificationTests(unittest.TestCase):
    def test_structural_types_are_gate_a(self):
        for t in ("source", "tool_used", "no_tool", "routed_via",
                  "no_fabricated_success", "eligibility"):
            self.assertEqual(gate_for(t), "A", t)

    def test_quality_types_are_gate_b(self):
        for t in ("contains", "not_contains", "contains_any", "safety_tier",
                  "auto:no_filler_opening", "auto:no_generic_filler_phrases", "unknown_type"):
            self.assertEqual(gate_for(t), "B", t)

    def test_gate_a_set_membership(self):
        self.assertIn("tool_used", GATE_A_TYPES)
        self.assertNotIn("contains", GATE_A_TYPES)


class GateGradeTests(unittest.TestCase):
    def test_gate_a_failure_is_hard_fail(self):
        # A wrong route (Gate A fail) hard-fails the turn even if quality passes.
        results = [_r("source", False, "A"), _r("contains", True, "B")]
        self.assertEqual(compute_gate_grades(results),
                         {"gate_a": "FAIL", "gate_b": "PASS"})
        self.assertEqual(compute_turn_grade(results), "FAIL")

    def test_gate_b_only_failure_is_soft_mixed(self):
        # A quality miss with routing correct -> MIXED, never FAIL.
        results = [_r("tool_used", True, "A"), _r("not_contains", False, "B")]
        self.assertEqual(compute_gate_grades(results),
                         {"gate_a": "PASS", "gate_b": "MIXED"})
        self.assertEqual(compute_turn_grade(results), "MIXED")

    def test_clean_is_pass(self):
        results = [_r("source", True, "A"), _r("contains", True, "B")]
        self.assertEqual(compute_turn_grade(results), "PASS")

    def test_no_gate_a_assertions_passes_gate_a(self):
        # Knowledge turns have only Gate-B assertions -> Gate A trivially clean.
        results = [_r("contains", True, "B"), _r("auto:no_generic_filler_phrases", True, "B")]
        self.assertEqual(compute_gate_grades(results)["gate_a"], "PASS")

    def test_both_gates_fail_is_fail(self):
        results = [_r("no_tool", False, "A"), _r("contains", False, "B")]
        self.assertEqual(compute_turn_grade(results), "FAIL")


class GradeTurnTagsGateTests(unittest.TestCase):
    def test_grade_turn_tags_each_result_with_its_gate(self):
        # An integration check: grade_turn must stamp .gate on every result so
        # the structural assertions land in Gate A and the auto:* in Gate B.
        response = {"text": "Your hostname is box.", "source": "cache",
                    "tool_calls": [], "category": "system_info"}
        assertions = [Assertion("source", "cache", "routed via cache"),
                      Assertion("contains", "hostname", "names the host")]
        results = grade_turn(response, assertions)
        by_type = {r.type: r.gate for r in results}
        self.assertEqual(by_type["source"], "A")
        self.assertEqual(by_type["contains"], "B")
        # every auto:* check is Gate B
        self.assertTrue(all(r.gate == "B" for r in results
                            if r.type.startswith("auto:")))


class OutputReadabilityTests(unittest.TestCase):
    """auto:long_data_output_has_line_breaks uses a numeric-token signal: a long
    narrative that merely mentions a few numbers (IP/port/version) reads fine as
    prose and must NOT be penalized for lacking newlines, while a long REAL data
    dump rendered as one blob still must break lines. (Closes the digit-residual.)"""

    def _readable(self, text: str) -> bool:
        results = grade_turn({"text": text, "source": "llm_freeform"}, [])
        hits = [r for r in results if r.type == "auto:long_data_output_has_line_breaks"]
        self.assertTrue(hits, "no auto:long_data_output_has_line_breaks result produced")
        return hits[0].passed

    def test_narrative_with_incidental_numbers_passes(self):
        # >450 chars of prose mentioning a couple of IPs and a port — 3 numeric
        # tokens, readable; previously failed because raw digit_count >= 3.
        text = ("The nginx.conf file is the default secure configuration. It binds "
                "only to 127.0.0.1, disables version banners, and uses TLS-only on "
                "port 443 with self-signed certificates. It restricts the status "
                "endpoint to 127.0.0.1, logs errors and access under /var/log/nginx, "
                "includes the standard MIME types, enables gzip compression, and "
                "redirects HTTP to HTTPS. The configuration is intentionally minimal "
                "and locked to loopback for security, with no autoindex enabled.")
        self.assertGreater(len(text), 450)
        self.assertNotIn("\n", text)
        self.assertTrue(self._readable(text),
                        "narrative prose with a few numbers was wrongly flagged")

    def test_unformatted_data_dump_still_fails(self):
        # >450 chars of real df-style data as one blob — many numeric tokens, must
        # still be flagged for lacking line formatting.
        text = ("The disk usage for the mounted filesystems is as follows. The root "
                "filesystem is 457GB in size, with 29GB used and 405GB available, "
                "occupying 7 percent. The tmpfs filesystems are 12GB with 73MB used "
                "and 12GB available at 1 percent, another is 16GB with 240MB used and "
                "15GB free at 2 percent, and boot is 512MB with 98MB used and 414MB "
                "free at 20 percent. The home partition is 200GB with 88GB used and "
                "112GB available at 44 percent, and var is 50GB with 31GB used and "
                "19GB free at 62 percent of the total partition capacity on the disk.")
        self.assertGreater(len(text), 450)
        self.assertNotIn("\n", text)
        self.assertFalse(self._readable(text),
                         "real unformatted data dump should be flagged")


class FillerOpeningTests(unittest.TestCase):
    """auto:no_filler_opening targets filler that DELAYS substance ("Of course!
    <long answer>"); a standalone short courtesy reply ("Of course!" to a thanks)
    is the complete appropriate response and must not be penalized. (edge_thanks.)"""

    def _filler_ok(self, text: str) -> bool:
        results = grade_turn({"text": text, "source": "llm_freeform"}, [])
        hits = [r for r in results if r.type == "auto:no_filler_opening"]
        self.assertTrue(hits, "no auto:no_filler_opening result produced")
        return hits[0].passed

    def test_standalone_courtesy_passes(self):
        for text in ("Of course!", "Certainly!", "Of course, happy to help!"):
            self.assertTrue(self._filler_ok(text), text)

    def test_filler_before_substance_still_flagged(self):
        text = ("Of course! Here is the disk usage you asked about, with the root "
                "filesystem and several mounted directories and their sizes.")
        self.assertGreater(len(text), 40)
        self.assertFalse(self._filler_ok(text))


if __name__ == "__main__":
    unittest.main()
