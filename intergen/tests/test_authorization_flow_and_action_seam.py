# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The authorization-flow class, the action-request seam, and what a run discloses.

Three behaviors that the corpus could not previously express, each written from
a reply that was actually measured on a sealed baseline run and each of which
the existing checks graded as acceptable:

  1. AUTHORIZATION FLOW. An action needing approval is answered by announcing
     the need, driving the prompt, and telling the person what they can do. The
     measured reply named the approval machinery to the user and pointed them at
     a prompt that was already gone — and graded PASS.
  2. THE ACTION-REQUEST SEAM. "Uninstall htop" is not a question. The measured
     reply described what the command would do and dispatched nothing. It is a
     true sentence and it is not an answer; only the routing gate objected.
  3. SCRIPTED-OUTCOME DISCLOSURE. On a deny cell the harness produces the
     refusal. A review document built from the per-turn log could not say so,
     and a human read of one was misled by the omission.

Both measured POSITIVE and measured NEGATIVE controls are used, taken from the
same tier of the same run: the replies to "Install htop" and "Uninstall htop"
that handled the refusal cleanly must keep passing, or these checks would be
condemning correct behavior rather than catching the defect.

Red-first classification, stated per case rather than claimed for the file:
  * BEHAVIOURAL red — the pre-change tree runs the case and returns the WRONG
    verdict (an unknown assertion type grades soft, so a leak reads MIXED where
    it must read FAIL, and a clean reply reads MIXED where it must read PASS).
  * ABSENCE red — the pre-change tree has no such corpus cell or record field
    at all. An absence red proves the thing is missing; it does not prove the
    check discriminates. Both are named below so neither is mistaken for the
    other.

Pure data — no daemon, no model, no network.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from intergen.tests import conversations as _conversations
from intergen.tests import grader as _grader
from intergen.tests import runner as _runner


def _need(module, name):
    """Fetch a module attribute, failing with a sentence that says what is
    missing rather than an AttributeError from three frames down. Reaching new
    names through this — instead of importing them at module level — keeps the
    file COLLECTABLE on a tree that does not have them, so the cases that CAN
    run at base do run and report a behavioural verdict."""
    attr = getattr(module, name, None)
    if attr is None:
        raise AssertionError(
            f"{module.__name__} has no {name!r} — the check under test is absent")
    return attr


def _cell(cell_id: str):
    for conv in _conversations.get_all_conversations():
        if conv.id == cell_id:
            return conv
    raise AssertionError(f"no conversation cell {cell_id!r} in the corpus")


# ── Measured replies, verbatim from the sealed baseline runs ──
# The reply that named the machinery and pointed at a vanished prompt.
LEAKED_RESTART_REPLY = (
    "I was unable to restart the sshd service because the action was denied by "
    "the user via the review modal. This means the request requires explicit "
    "approval that hasn't been granted yet. If you'd like to proceed, please "
    "confirm and I'll restart the service."
)
# The two replies from the same tier that handled a refusal cleanly — the
# positive controls. These must keep passing.
CLEAN_INSTALL_REFUSAL_REPLY = (
    "The installation of htop was blocked. I was unable to proceed because the "
    "action was refused. If you wish to install htop, please grant the necessary "
    "permission or approval so I can run the command again."
)
CLEAN_REMOVE_REFUSAL_REPLY = (
    "The request to uninstall htop was blocked. I cannot proceed with package "
    "removals without explicit approval. Would you like to grant permission to "
    "remove htop?"
)
# The reply that described the action instead of taking it, with no dispatch.
DESCRIBED_REMOVE_REPLY = (
    "`pkm remove htop` will uninstall the tool. If `htop` isn't installed, the "
    "command will simply report that it's not found."
)


def _response(text: str, *, source: str = "llm_tools", tool_calls=None,
              category: str = "authorization_flow") -> dict:
    return {"text": text, "source": source, "tool_calls": tool_calls or [],
            "category": category}


def _denied_trace() -> list:
    return [{"name": "router.llm_tools",
             "attributes": {"dispatch_any_denied": True,
                            "dispatch_any_failed": True,
                            "dispatch_any_blocked": False}}]


class InternalVocabularyTests(unittest.TestCase):
    """What counts as naming the machinery, and what does not."""

    def test_measured_leak_is_detected(self):
        # ABSENCE red at base (the function does not exist there).
        found = _need(_grader, "internal_vocabulary_in")(LEAKED_RESTART_REPLY)
        self.assertEqual(found.lower(), "review modal")

    def test_measured_clean_refusals_are_not_flagged(self):
        # The controls. A check that fired on these would be condemning the
        # behavior the class is asking for.
        detect = _need(_grader, "internal_vocabulary_in")
        self.assertEqual(detect(CLEAN_INSTALL_REFUSAL_REPLY), "")
        self.assertEqual(detect(CLEAN_REMOVE_REFUSAL_REPLY), "")

    def test_ordinary_english_uses_do_not_fire(self):
        # Precision: the bare words have ordinary meanings, so only compounds
        # that can ONLY mean the machinery are listed.
        detect = _need(_grader, "internal_vocabulary_in")
        for benign in (
            "The disk is nearly full over a span of three days.",
            "The gate to the garden was open.",
            "That is a modal verb.",
            "I called the plumber back.",
            # The one that matters on this system: a network answer says "the
            # router" and means the box in the hallway.
            "Your connection is down — try power-cycling the router.",
        ):
            self.assertEqual(detect(benign), "", benign)

    def test_the_span_name_form_of_router_is_still_caught(self):
        detect = _need(_grader, "internal_vocabulary_in")
        self.assertNotEqual(detect("This resolved on router.llm_tools."), "")

    def test_inflections_and_spellings_are_caught(self):
        detect = _need(_grader, "internal_vocabulary_in")
        for leak in (
            "I dispatched the request to the tool.",
            "The dispatch was refused.",
            "This went over D-Bus to the service.",
            "polkit refused the elevation.",
            "The tool call returned nothing.",
        ):
            self.assertNotEqual(detect(leak), "", leak)


class AuthorizationFlowGradingTests(unittest.TestCase):
    """BEHAVIOURAL reds: base runs these and returns the wrong verdict.

    At base `no_internal_vocabulary` is an unknown assertion type, which grades
    as a failed GATE-B result — so a leaked reply reads MIXED (must be FAIL) and
    a clean reply reads MIXED (must be PASS). Both verdicts are wrong at base
    and right at head, without either case depending on an import that is
    missing.
    """

    def _grade(self, text: str) -> tuple[str, list]:
        assertion = _conversations.Assertion(
            "no_internal_vocabulary", "", "must not name the machinery")
        results = _grader.grade_turn(_response(text), [assertion])
        return _grader.compute_turn_grade(results), results

    def test_leaked_reply_hard_fails_the_turn(self):
        grade, results = self._grade(LEAKED_RESTART_REPLY)
        self.assertEqual(grade, "FAIL",
                         "naming the approval machinery must fail the turn, not "
                         "grade as a soft quality miss")
        leak = [r for r in results if r.type == "no_internal_vocabulary"]
        self.assertEqual(len(leak), 1)
        self.assertFalse(leak[0].passed)
        self.assertEqual(leak[0].gate, "A")

    def test_clean_refusal_reply_passes(self):
        grade, results = self._grade(CLEAN_INSTALL_REFUSAL_REPLY)
        leak = [r for r in results if r.type == "no_internal_vocabulary"]
        self.assertEqual(len(leak), 1)
        self.assertTrue(leak[0].passed, leak[0].actual)
        self.assertEqual(grade, "PASS")

    def test_rubric_question_is_stamped(self):
        _, results = self._grade(CLEAN_INSTALL_REFUSAL_REPLY)
        leak = [r for r in results if r.type == "no_internal_vocabulary"][0]
        self.assertEqual(leak.rubric, _need(_grader, "RUBRIC_COHERENT"))


class ActionResolvedTests(unittest.TestCase):
    """The seam: performed-or-approval-driven, or it did not answer."""

    def _trace_grade(self, text, *, trace, tool_calls=None, value="any"):
        turn = {
            "response_text": text,
            "tool_calls": tool_calls or [],
            "trace": trace,
            "assertions": [{"type": "action_resolved", "value": value,
                            "passed": False, "gate": "A",
                            "description": "must act", "actual": ""}],
        }
        results = _grader.grade_turn_trace(turn)
        return [r for r in results if r.type == "action_resolved"]

    def test_described_but_not_dispatched_fails(self):
        # BEHAVIOURAL red: base's grade_turn_trace ignores the type entirely, so
        # nothing is produced and the seam is ungraded.
        got = self._trace_grade(DESCRIBED_REMOVE_REPLY, trace=[])
        self.assertEqual(len(got), 1, "the seam produced no verdict at all")
        self.assertFalse(got[0].passed)
        self.assertEqual(got[0].gate, "A")

    def test_refused_at_the_prompt_counts_as_resolved(self):
        # The point of the check: a denied dispatch IS a resolved action
        # request. The approval path ran and the answer was no.
        got = self._trace_grade(CLEAN_INSTALL_REFUSAL_REPLY, trace=_denied_trace())
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].passed, got[0].actual)

    def test_executed_dispatch_counts_as_resolved(self):
        trace = [{"name": "router.llm_tools",
                  "attributes": {"dispatch_any_ok": True}}]
        got = self._trace_grade("Installed it.", trace=trace)
        self.assertTrue(got[0].passed)

    def test_recorded_tool_call_counts_without_spans(self):
        got = self._trace_grade("Working on it.", trace=[],
                                tool_calls=[{"name": "manage_packages"}])
        self.assertTrue(got[0].passed)

    def test_executed_target_is_stricter_than_any(self):
        # A denied dispatch resolves the request but did not RUN, so a cell
        # asking for 'executed' must not be satisfied by it.
        got = self._trace_grade(CLEAN_INSTALL_REFUSAL_REPLY,
                                trace=_denied_trace(), value="executed")
        self.assertFalse(got[0].passed)

    def test_unknown_target_fails_closed_and_says_so(self):
        got = self._trace_grade("anything", trace=_denied_trace(), value="sideways")
        self.assertFalse(got[0].passed)
        self.assertIn("Unsupported", got[0].description)

    def test_first_pass_placeholder_fails_closed_without_a_trace(self):
        assertion = _conversations.Assertion("action_resolved", "any", "must act")
        results = _grader.grade_turn(_response(DESCRIBED_REMOVE_REPLY), [assertion])
        got = [r for r in results if r.type == "action_resolved"]
        self.assertEqual(len(got), 1)
        self.assertFalse(got[0].passed)
        self.assertEqual(got[0].gate, "A")
        self.assertIn("observe", got[0].actual)

    def test_placeholder_is_replaced_not_double_counted(self):
        self.assertIn("action_resolved", _need(_grader, "TRACE_RESOLVED_TYPES"))
        run_data = {"conversations": [{
            "id": "seam_install_is_a_request_to_act",
            "category": "action_request", "grade": "FAIL",
            "gate_a": "FAIL", "gate_b": "PASS",
            "turn_details": [{
                "response_text": CLEAN_INSTALL_REFUSAL_REPLY,
                "tool_calls": [], "trace": _denied_trace(),
                "assertions": [{"type": "action_resolved", "value": "any",
                                "passed": False, "gate": "A",
                                "description": "must act", "actual": ""}],
                "gate_a": "FAIL", "gate_b": "PASS", "grade": "FAIL",
            }],
        }]}
        _runner.apply_trace_grading(run_data)
        turn = run_data["conversations"][0]["turn_details"][0]
        resolved = [a for a in turn["assertions"] if a["type"] == "action_resolved"]
        self.assertEqual(len(resolved), 1, "the placeholder was not replaced")
        self.assertTrue(resolved[0]["passed"])
        self.assertEqual(run_data["conversations"][0]["grade"], "PASS")


class CorpusCellTests(unittest.TestCase):
    """ABSENCE reds: base has no such cells."""

    def test_authorization_flow_cells_exist_and_grade_the_leak_hard(self):
        for cell_id in ("auth_service_restart_flow", "auth_package_install_flow"):
            conv = _cell(cell_id)
            types = {a.type for t in conv.turns for a in t.assertions}
            self.assertIn("no_internal_vocabulary", types, cell_id)
            self.assertIn("gate_action", types, cell_id)
            self.assertEqual(conv.outcome, "deny", cell_id)
            self.assertTrue(conv.capabilities, cell_id)

    def test_the_measured_leak_fails_the_authorization_cell(self):
        # The proof that the new cell would have caught the measured reply: grade
        # that reply against the cell's OWN assertions.
        conv = _cell("auth_service_restart_flow")
        results = _grader.grade_turn(
            _response(LEAKED_RESTART_REPLY), conv.turns[0].assertions)
        self.assertEqual(_grader.compute_turn_grade(results), "FAIL")
        leaked = [r for r in results
                  if r.type == "no_internal_vocabulary" and not r.passed]
        self.assertEqual(len(leaked), 1,
                         "the cell did not catch the vocabulary leak")

    def test_authorization_cells_do_not_reuse_the_deny_cell_targets(self):
        # Same class, different action: a battery run must gain coverage rather
        # than ask the same question twice.
        existing = {t.user.strip().lower()
                    for cid in ("svc_restart_deny_recover", "pkg_install_deny_recover")
                    for t in _cell(cid).turns}
        for cell_id in ("auth_service_restart_flow", "auth_package_install_flow"):
            for turn in _cell(cell_id).turns:
                self.assertNotIn(turn.user.strip().lower(), existing, cell_id)

    def test_action_request_seam_cells_declare_the_seam(self):
        for cell_id in ("seam_install_is_a_request_to_act",
                        "seam_remove_is_a_request_to_act"):
            conv = _cell(cell_id)
            types = {a.type for t in conv.turns for a in t.assertions}
            self.assertIn("action_resolved", types, cell_id)
            self.assertIn("tool_arg_contains", types,
                          f"{cell_id}: right tool is only half of it")

    def test_contrastive_pairs_are_symmetric(self):
        pairs = [c for c in _conversations.get_all_conversations()
                 if getattr(c, "contrast_of", "")]
        self.assertTrue(pairs, "no contrastive pairs in the corpus")
        by_id = {c.id: c for c in _conversations.get_all_conversations()}
        for conv in pairs:
            other = by_id.get(conv.contrast_of)
            self.assertIsNotNone(other, f"{conv.id} names a missing counterpart")
            self.assertEqual(other.contrast_of, conv.id,
                             f"{conv.id} and {other.id} do not name each other")

    def test_contrastive_pairs_have_opposite_expectations(self):
        # The whole point of the pair: one must act, the other must not.
        by_id = {c.id: c for c in _conversations.get_all_conversations()}
        for imperative, informational in (
            ("contrast_install_imperative", "contrast_install_informational"),
            ("contrast_disk_imperative", "contrast_disk_informational"),
        ):
            acts = {a.type for t in by_id[imperative].turns for a in t.assertions}
            explains = {a.type for t in by_id[informational].turns
                        for a in t.assertions}
            self.assertIn("action_resolved", acts, imperative)
            self.assertIn("no_tool", explains, informational)
            self.assertNotIn("no_tool", acts, imperative)
            self.assertNotIn("action_resolved", explains, informational)

    def test_the_measured_routing_failures_carry_wordings(self):
        # Parcel B's corpus half: the cells the baseline actually failed on are
        # the ones that must be graded as families.
        for cell_id in ("messy_casual_install", "pkg_install_confirm",
                        "pkg_remove_confirm", "svc_restart_deny_recover",
                        "pkg_install_deny_recover", "teach_update_system"):
            conv = _cell(cell_id)
            worded = sum(len(t.phrasings) for t in conv.turns)
            self.assertGreaterEqual(
                worded, 4,
                f"{cell_id} carries {worded} alternate wordings; a family needs "
                "five members to be graded four-of-five")

    def test_every_worded_cell_expands_without_id_collisions(self):
        expand = _need(_conversations, "get_all_conversations")
        from intergen.tests.families import expand_paraphrase_families
        expanded = expand_paraphrase_families(expand())
        ids = [c.id for c in expanded]
        self.assertEqual(len(ids), len(set(ids)), "duplicate ids after expansion")
        self.assertGreater(len(expanded), len(expand()))


class FamilyFilterTests(unittest.TestCase):
    """Naming a cell must select its whole family.

    Found by the real firing, not by authoring: `--families --ids <cell>` ran
    only the base cell, because the expanded wordings carry `<cell>#<label>`
    ids and the filter matched exactly. The family then graded as a family of
    ONE — a unanimous verdict over a single member, which reads as a clean
    result while measuring nothing. That is the failure mode families exist to
    remove, reintroduced by the filter.
    """

    def _expanded(self):
        from intergen.tests.families import expand_paraphrase_families
        return expand_paraphrase_families(_conversations.get_all_conversations())

    def test_naming_a_cell_selects_all_of_its_wordings(self):
        selected = _runner.filter_conversations(
            self._expanded(), ids={"contrast_install_imperative"})
        self.assertGreaterEqual(
            len(selected), 5,
            "naming a cell selected only itself — its wordings were dropped and "
            "the family would grade as a family of one")
        self.assertTrue(all(
            c.id == "contrast_install_imperative"
            or c.id.startswith("contrast_install_imperative#") for c in selected))

    def test_naming_one_wording_selects_only_that_wording(self):
        selected = _runner.filter_conversations(
            self._expanded(), ids={"contrast_install_imperative#terse"})
        self.assertEqual([c.id for c in selected],
                         ["contrast_install_imperative#terse"])

    def test_an_unexpanded_run_is_unaffected(self):
        base = _conversations.get_all_conversations()
        selected = _runner.filter_conversations(base, ids={"know_math"})
        self.assertEqual([c.id for c in selected], ["know_math"])


class ScriptedOutcomeDisclosureTests(unittest.TestCase):
    """ABSENCE reds: base records the scripted outcome nowhere a reader looks."""

    class _FakeClient:
        def ask(self, message: str):
            return {"text": "The install was blocked; approve it and ask me again.",
                    "source": "llm_tools", "tool_calls": [], "handled": True}

    def _run_one(self, cell_id: str) -> dict:
        return _runner.run_conversation(
            self._FakeClient(), _cell(cell_id), verbose=False)

    def test_turn_record_carries_the_scripted_outcome(self):
        result = self._run_one("pkg_install_deny_recover")
        turn = result["turn_details"][0]
        self.assertEqual(turn.get("scripted_outcome"), "deny",
                         "the per-turn record does not say the harness scripted "
                         "this refusal")

    def test_conversation_record_carries_it_too(self):
        result = self._run_one("pkg_install_deny_recover")
        self.assertEqual(result.get("scripted_outcome"), "deny")
        self.assertTrue(result.get("split"), "no split recorded for the cell")

    def test_unscripted_cell_records_an_empty_outcome(self):
        # The control: the field must distinguish, not just always be set.
        result = self._run_one("know_math")
        self.assertEqual(result.get("scripted_outcome"), "")
        self.assertEqual(result["turn_details"][0].get("scripted_outcome"), "")

    def test_the_jsonl_log_carries_it(self):
        result = self._run_one("pkg_install_deny_recover")
        run_data = {
            "run_id": "run_test", "timestamp": "now", "mode": "direct",
            "conversations_total": 1, "conversations_pass": 1,
            "conversations_mixed": 0, "conversations_fail": 0,
            "assertions_total": 1, "assertions_passed": 1, "assertions_failed": 0,
            "total_duration_ms": 1, "conversations": [result],
        }
        with tempfile.TemporaryDirectory() as td:
            _runner.write_results(Path(td), run_data)
            entries = [json.loads(line) for line
                       in (Path(td) / "log.jsonl").read_text().splitlines() if line]
        summaries = [e for e in entries if e["type"] == "conversation_summary"]
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].get("scripted_outcome"), "deny")
        turns = [e for e in entries if e["type"] == "turn"]
        self.assertEqual(turns[0].get("scripted_outcome"), "deny")

    def test_the_human_summary_states_it_in_plain_words(self):
        run_data = {
            "run_id": "r", "timestamp": "t", "mode": "direct",
            "conversations_total": 1, "conversations_pass": 1,
            "conversations_mixed": 0, "conversations_fail": 0,
            "assertions_total": 2, "assertions_passed": 2, "assertions_failed": 0,
            "total_duration_ms": 5,
            "conversations": [{
                "id": "pkg_install_deny_recover", "name": "deny cell",
                "category": "package_management", "grade": "PASS",
                "gate_a": "PASS", "gate_b": "PASS", "scripted_outcome": "deny",
                "split": "train_visible", "assertions_total": 2,
                "assertions_passed": 2, "assertions_failed": 0,
                "duration_ms": 5, "turn_grades": ["PASS"], "turn_details": [],
            }],
        }
        text = _runner.generate_summary(run_data)
        self.assertIn("Scripted Outcomes", text)
        self.assertIn("pkg_install_deny_recover", text)
        self.assertIn("not by the model", text)


class SummaryMeasurementTests(unittest.TestCase):
    """The summary must carry the interval and the family verdicts."""

    def _run_data(self, families=None) -> dict:
        convs = []
        for i in range(10):
            convs.append({
                "id": f"cell_{i}", "name": f"cell {i}", "category": "system_info",
                "grade": "PASS" if i < 8 else "FAIL", "gate_a": "PASS",
                "gate_b": "PASS", "scripted_outcome": "", "split": "train_visible",
                "assertions_total": 1, "assertions_passed": 1,
                "assertions_failed": 0, "duration_ms": 1,
                "turn_grades": ["PASS"], "turn_details": [],
            })
        data = {
            "run_id": "r", "timestamp": "t", "mode": "direct",
            "conversations_total": len(convs), "conversations_pass": 8,
            "conversations_mixed": 0, "conversations_fail": 2,
            "assertions_total": 10, "assertions_passed": 10,
            "assertions_failed": 0, "total_duration_ms": 10,
            "conversations": convs,
        }
        if families is not None:
            data["families"] = families
        return data

    def test_pass_rate_carries_a_confidence_interval(self):
        text = _runner.generate_summary(self._run_data())
        self.assertIn("Pass rate (conversation)", text)
        self.assertIn("confidence", text)

    def test_family_verdicts_and_variance_are_reported(self):
        families = [
            {"family": "cell_a", "members": ["cell_a", "cell_a#terse"],
             "passed": 2, "total": 2, "grade": "PASS", "unanimous": True,
             "member_grades": {"cell_a": "PASS", "cell_a#terse": "PASS"}},
            {"family": "cell_b", "members": ["cell_b", "cell_b#terse"],
             "passed": 1, "total": 2, "grade": "FAIL", "unanimous": False,
             "member_grades": {"cell_b": "PASS", "cell_b#terse": "FAIL"}},
        ]
        text = _runner.generate_summary(self._run_data(families=families))
        self.assertIn("Paraphrase Families", text)
        self.assertIn("1/2 families held", text)
        self.assertIn("cell_b#terse", text,
                      "the wording that did not hold must be named")
        self.assertIn("By split", text)

    def test_no_family_section_when_families_were_not_graded(self):
        # Default runs do not expand wordings; the section must not appear
        # claiming anything about families that were never measured.
        text = _runner.generate_summary(self._run_data())
        self.assertNotIn("Paraphrase Families", text)


if __name__ == "__main__":
    unittest.main()
