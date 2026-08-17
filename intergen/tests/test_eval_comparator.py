# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Tests for the eval-harness capability inventory + run-over-run comparator.

Deterministic, no daemon, no model — synthetic results.json fixtures exercise
the axes the comparator must catch: per-conversation grade regressions, AND
cell-coverage erosion at (capability, OUTCOME) granularity, so a vanished gate
branch is a regression at the same severity as a pass->fail and a gated tool
covered only by a teaching-negative still flags its untested gate branches as
gaps (WC PR3 coverage-granularity red-team, 2026-06-29). Also pins the
classification against the real tree, the capability derivation precedence, and
the outcome derivation.
"""

from __future__ import annotations

import unittest

from intergen.tests import capability_inventory as ci
from intergen.tests.compare_runs import compare


def _conv(cid, grade="PASS", *, category="", tool_calls=None, tool_used=None,
          capabilities=None, outcome=None, no_tool=False, trace_attrs=None):
    """Build a conversation result the way runner.run_conversation emits one."""
    assertions = [{"type": "tool_used", "value": v} for v in (tool_used or [])]
    if no_tool:
        assertions.append({"type": "no_tool", "value": ""})
    turn = {
        "tool_calls": [{"name": n} for n in (tool_calls or [])],
        "assertions": assertions,
    }
    if trace_attrs is not None:
        # the router.llm_tools span attach_traces joins onto the turn (--observe)
        turn["trace"] = [{"name": "router.llm_tools", "attributes": trace_attrs}]
    conv = {"id": cid, "name": cid, "category": category, "grade": grade,
            "turn_details": [turn]}
    if capabilities is not None:
        conv["capabilities"] = capabilities
    if outcome is not None:
        conv["outcome"] = outcome
    return conv


def _run(run_id, convs):
    return {"run_id": run_id, "conversations": convs}


class CapabilityInventoryTests(unittest.TestCase):
    def test_inventory_matches_tree(self):
        self.assertTrue(ci.GATED_TOOLS and ci.READ_TOOLS)
        self.assertFalse(ci.GATED_TOOLS & ci.READ_TOOLS)
        self.assertEqual(ci.ALL_TOOLS, ci._tool_modules(),
                         "inventory drifted from intergen/tools/")

    def test_classification(self):
        for t in ("manage_packages", "manage_services", "write_file",
                  "run_command", "take_screenshot"):
            self.assertEqual(ci.capability_class(t), "gated")
            self.assertTrue(ci.is_gated(t))
        for t in ("read_file", "analyze_file", "web_search", "open_application"):
            self.assertEqual(ci.capability_class(t), "read")
            self.assertFalse(ci.is_gated(t))
        self.assertEqual(ci.capability_class("nope"), "unknown")

    def test_capability_derivation_precedence(self):
        # 1. explicit tag is authoritative — replaces derivation entirely
        c = _conv("x", tool_calls=["read_file"], capabilities=["write_file"])
        self.assertEqual(ci.conversation_capabilities(c), {"write_file"})
        # 2. union of observed tool_calls + tool_used assertions
        c = _conv("x", category="package_management", tool_calls=["run_command"],
                  tool_used=["read_file"])
        self.assertEqual(ci.conversation_capabilities(c),
                         {"run_command", "read_file"})
        # 3. tool_used assertion when no tool_calls
        c = _conv("x", tool_used=["manage_services"])
        self.assertEqual(ci.conversation_capabilities(c), {"manage_services"})
        # 4. category fallback ONLY when no tool signal at all
        c = _conv("x", category="package_management")
        self.assertEqual(ci.conversation_capabilities(c), {"manage_packages"})
        # none: a pure-knowledge conversation
        self.assertEqual(ci.conversation_capabilities(_conv("x")), set())

    def test_outcome_derivation(self):
        # declared is authoritative (gate branches can ONLY be declared)
        self.assertEqual(ci.conversation_outcome(
            _conv("x", capabilities=["write_file"], outcome="deny")), "deny")
        # teaching: no_tool + a declared capability
        self.assertEqual(ci.conversation_outcome(
            _conv("x", capabilities=["write_file"], no_tool=True)), "teaching")
        # an observed dispatch is executed_success REGARDLESS of grade — coverage is
        # orthogonal to grade, so a failing grade does not flip the derived outcome
        # (that would falsely read a FAIL->PASS improvement as cell erosion).
        self.assertEqual(ci.conversation_outcome(
            _conv("x", tool_calls=["read_file"], grade="PASS")), "executed_success")
        self.assertEqual(ci.conversation_outcome(
            _conv("x", tool_calls=["read_file"], grade="FAIL")), "executed_success")
        # the executed split is DERIVED from the trace's dispatch_any_failed (the
        # per-call success lives on the span, NOT in tool_calls). A read dispatch
        # the trace marks failed is executed_fail; one with no failure is
        # executed_success. (WC read-tool-matrix review, 2026-06-29.)
        self.assertEqual(ci.conversation_outcome(
            _conv("x", tool_calls=["read_file"],
                  trace_attrs={"dispatch_any_failed": True})), "executed_fail")
        self.assertEqual(ci.conversation_outcome(
            _conv("x", tool_calls=["read_file"],
                  trace_attrs={"dispatch_any_failed": False})), "executed_success")
        # executed_fail is grade-orthogonal: a failed dispatch stays executed_fail
        # whether the conversation PASSED or FAILED its assertions (the tool
        # errored or it did not, independent of the assertion grade).
        self.assertEqual(ci.conversation_outcome(
            _conv("x", tool_calls=["read_file"], grade="PASS",
                  trace_attrs={"dispatch_any_failed": True})), "executed_fail")
        self.assertEqual(ci.conversation_outcome(
            _conv("x", tool_calls=["read_file"], grade="FAIL",
                  trace_attrs={"dispatch_any_failed": True})), "executed_fail")
        # an UNTRACED dispatch (no span) cannot observe a failure -> executed_success
        # (you cannot observe an error you did not provoke or trace).
        self.assertEqual(ci.conversation_outcome(
            _conv("x", tool_calls=["read_file"])), "executed_success")
        # executed_fail is RUN-then-error ONLY: a pre-run REJECTION (failed AND
        # denied: executed=False/success=False) must NOT false-claim executed_fail
        # coverage — it derives 'unspecified' (a visible gap), so the missing
        # run-then-error driver stays unmasked. (WC pre-run-reject criterion.)
        self.assertEqual(ci.conversation_outcome(
            _conv("x", tool_calls=["read_file"],
                  trace_attrs={"dispatch_any_failed": True,
                               "dispatch_any_denied": True})), "unspecified")
        # a run-then-error (failed, NOT denied) stays executed_fail
        self.assertEqual(ci.conversation_outcome(
            _conv("x", tool_calls=["read_file"],
                  trace_attrs={"dispatch_any_failed": True,
                               "dispatch_any_denied": False})), "executed_fail")
        # pure knowledge -> unspecified
        self.assertEqual(ci.conversation_outcome(_conv("x")), "unspecified")
        # an invalid declared outcome falls through to derivation, never trusted
        self.assertEqual(ci.conversation_outcome(
            _conv("x", tool_calls=["read_file"], outcome="bogus")),
            "executed_success")

    def test_coverage_gaps_outcome_granular(self):
        # THE WC scenario: a gated tool covered ONLY by a teaching-negative reads
        # teaching_covered=True yet every gate outcome is missing — no longer green.
        run = _run("r", [
            _conv("teach_wf", capabilities=["write_file"], no_tool=True,
                  outcome="teaching"),
            _conv("rf_read", tool_calls=["read_file"], outcome="executed_success"),
        ])
        gaps = ci.coverage_gaps(run)
        self.assertIn("write_file", gaps["gated"])
        self.assertTrue(gaps["gated"]["write_file"]["teaching_covered"])
        self.assertEqual(set(gaps["gated"]["write_file"]["missing_outcomes"]),
                         set(ci.GATE_OUTCOMES))
        # run_command: nothing at all -> all gate outcomes missing, no teaching
        self.assertIn("run_command", gaps["gated"])
        self.assertFalse(gaps["gated"]["run_command"]["teaching_covered"])
        # read_file: one executed outcome covered, the other still missing
        self.assertEqual(gaps["read"]["read_file"]["missing_outcomes"],
                         ["executed_fail"])

    def test_gated_tool_complete_when_every_outcome_present(self):
        convs = [_conv(f"wf_{o}", capabilities=["write_file"], outcome=o)
                 for o in ci.GATE_OUTCOMES]
        convs.append(_conv("wf_teach", capabilities=["write_file"], no_tool=True,
                           outcome="teaching"))
        gaps = ci.coverage_gaps(_run("r", convs))
        self.assertNotIn("write_file", gaps["gated"])

    def test_coverage_note(self):
        # write_file/run_command gate outcomes carry a not-corpus-viable note
        for o in ("deny", "executed_success", "gate_timeout"):
            self.assertIsNotNone(ci.coverage_note("write_file", o))
            self.assertIsNotNone(ci.coverage_note("run_command", o))
        # a corpus-REQUIRED outcome is a real gap, NOT annotated
        self.assertIsNone(ci.coverage_note("manage_services", "deny"))
        # a non-required outcome on a live-gating tool IS annotated
        self.assertIsNotNone(ci.coverage_note("manage_services", "gate_timeout"))
        # an un-annotated tool/outcome: None
        self.assertIsNone(ci.coverage_note("read_file", "executed_success"))

    def test_per_tool_corpus_complete(self):
        # manage_services REQUIRES only deny: a deny cell + a teaching cell = complete,
        # with the non-required outcomes annotated (never blocking completion).
        convs = [_conv("svc_deny", capabilities=["manage_services"], outcome="deny"),
                 _conv("svc_teach", capabilities=["manage_services"], no_tool=True,
                       outcome="teaching")]
        info = ci.coverage_gaps(_run("r", convs))["gated"]["manage_services"]
        self.assertTrue(info["corpus_complete"])
        self.assertEqual(info["required_missing"], [])
        self.assertNotIn("deny", info["missing_outcomes"])
        self.assertIn("gate_timeout", info["notes"])

    def test_required_gap_blocks_corpus_complete(self):
        # teaching only, no deny -> not complete; deny is the chase gap
        info = ci.coverage_gaps(_run("r", [
            _conv("t", capabilities=["manage_services"], no_tool=True,
                  outcome="teaching")]))["gated"]["manage_services"]
        self.assertFalse(info["corpus_complete"])
        self.assertEqual(info["required_missing"], ["deny"])

    def test_gap_report_annotates_without_dropping(self):
        run = _run("r", [_conv("t", capabilities=["write_file"], no_tool=True,
                               outcome="teaching")])
        wf = ci.coverage_gaps(run)["gated"]["write_file"]
        # deny is STILL a gap (never silently dropped) AND annotated
        self.assertIn("deny", wf["missing_outcomes"])
        self.assertIn("deny", wf["notes"])
        self.assertTrue(wf["notes"]["deny"])

    def test_outcome_consistency(self):
        # declared executed_success but no dispatch -> inconsistent
        bad = ci.outcome_consistency(_run("r", [
            _conv("a", outcome="executed_success", capabilities=["read_file"])]))
        self.assertEqual([b["declared"] for b in bad], ["executed_success"])
        # declared teaching but a dispatch -> inconsistent
        bad = ci.outcome_consistency(_run("r", [
            _conv("a", outcome="teaching", tool_calls=["read_file"])]))
        self.assertEqual(len(bad), 1)
        # consistent: executed_success WITH a dispatch; teaching with none
        self.assertEqual(ci.outcome_consistency(_run("r", [
            _conv("a", outcome="executed_success", tool_calls=["read_file"])])), [])
        self.assertEqual(ci.outcome_consistency(_run("r", [
            _conv("a", outcome="teaching", no_tool=True,
                  capabilities=["write_file"])])), [])
        # a declared gate-branch (deny) is harness-asserted, NOT flagged here
        self.assertEqual(ci.outcome_consistency(_run("r", [
            _conv("a", outcome="deny", capabilities=["manage_services"])])), [])


class ComparatorTests(unittest.TestCase):
    def test_clean_run_no_regression(self):
        convs = [_conv("a", "PASS", tool_calls=["read_file"]),
                 _conv("b", "PASS", tool_calls=["manage_packages"])]
        diff = compare(_run("old", convs), _run("new", convs))
        self.assertFalse(diff["regression"])
        self.assertEqual(diff["grade_regressions"], [])
        self.assertEqual(diff["vanished_cells"], [])

    def test_grade_regression_detected(self):
        old = _run("old", [_conv("a", "PASS", tool_calls=["read_file"])])
        new = _run("new", [_conv("a", "FAIL", tool_calls=["read_file"])])
        diff = compare(old, new)
        self.assertTrue(diff["regression"])
        self.assertEqual(diff["grade_regressions"],
                         [{"id": "a", "from": "PASS", "to": "FAIL"}])

    def test_improvement_is_not_a_regression(self):
        old = _run("old", [_conv("a", "FAIL", tool_calls=["read_file"])])
        new = _run("new", [_conv("a", "PASS", tool_calls=["read_file"])])
        diff = compare(old, new)
        self.assertFalse(diff["regression"])
        self.assertEqual(len(diff["grade_improvements"]), 1)

    def test_dropped_conversation_is_a_regression(self):
        old = _run("old", [_conv("a", "PASS", tool_calls=["read_file"]),
                           _conv("b", "PASS", tool_calls=["manage_packages"])])
        new = _run("new", [_conv("a", "PASS", tool_calls=["read_file"])])
        diff = compare(old, new)
        self.assertTrue(diff["regression"])
        self.assertEqual(diff["dropped_conversations"], ["b"])
        self.assertIn(
            {"capability": "manage_packages", "outcome": "executed_success",
             "id": "b"},
            diff["vanished_from_removed_conversations"])

    def test_coverage_erosion_same_conversation(self):
        # The conversation SURVIVES and still PASSes, but stopped exercising a
        # capability. A grade-only diff is blind to this; the coverage-set diff
        # flags it as a regression.
        old = _run("old", [_conv("a", "PASS", tool_calls=["run_command"])])
        new = _run("new", [_conv("a", "PASS", tool_calls=["read_file"])])
        diff = compare(old, new)
        self.assertTrue(diff["regression"])
        self.assertEqual(diff["grade_regressions"], [])
        self.assertIn(
            {"capability": "run_command", "outcome": "executed_success", "id": "a"},
            diff["vanished_capability_only"])

    def test_outcome_cell_erosion_is_regression(self):
        # The new axis: same conversation id, still passing, but it now exercises a
        # DIFFERENT outcome — the deny cell vanished. Outcome-granular keying makes
        # that a regression a capability-only diff would have masked.
        old = _run("old", [_conv("wf", "PASS", capabilities=["write_file"],
                                 outcome="deny")])
        new = _run("new", [_conv("wf", "PASS", capabilities=["write_file"],
                                 outcome="executed_success")])
        diff = compare(old, new)
        self.assertTrue(diff["regression"])
        self.assertIn(
            {"capability": "write_file", "outcome": "deny", "id": "wf"},
            diff["vanished_capability_only"])

    def test_newly_missing_required_outcome(self):
        # Only a REQUIRED outcome counts as newly-missing signal: a manage_services
        # deny cell covered before, gone now. (A non-required outcome's loss is still
        # caught as a vanished cell, just not as a newly-missing-required gap.)
        old = _run("old", [_conv("d", "PASS", capabilities=["manage_services"],
                                 outcome="deny")])
        new = _run("new", [_conv("d", "PASS", tool_calls=["read_file"])])
        diff = compare(old, new)
        self.assertIn({"capability": "manage_services", "outcome": "deny"},
                      diff["newly_missing_outcomes"])

    def test_outcome_inconsistency_is_a_regression(self):
        # Same coverage cell + same grade both runs, so the ONLY signal is the
        # candidate's declared-tag-vs-reality contradiction (executed_success with
        # no dispatch) — it must fail the verdict on its own.
        old = _run("old", [_conv("a", "PASS", tool_calls=["read_file"],
                                 outcome="executed_success")])
        new = _run("new", [_conv("a", "PASS", capabilities=["read_file"],
                                 outcome="executed_success")])
        diff = compare(old, new)
        self.assertTrue(diff["regression"])
        self.assertEqual(diff["grade_regressions"], [])
        self.assertEqual(len(diff["outcome_inconsistencies"]), 1)


if __name__ == "__main__":
    unittest.main()
