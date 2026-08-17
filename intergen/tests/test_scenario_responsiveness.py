# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Answer-responsiveness gate + run-artifact transcript persistence.

Two defects in one audit finding: a turn asking ``"search for a pdf editor"``
was answered ``"Disk usage is available."`` and graded PASS (nothing anywhere
compared the answer to the question), and the run artifacts recorded no
transcript, so catching it took a hand join back to the glass trace.

The acceptance fixtures are hard: the recorded false-pass MUST fail the gate,
and the known-coherent short answers from the same runs MUST stay PASS. The
answer strings below are the router's real template outputs, generated from
``intergen.router.ConversationRouter._template_synthesis`` against representative
command output — not hand-approximated shapes.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from intergen.tests.scenario.grader import grade_turn
from intergen.tests.scenario.report import build_results, write_run
from intergen.tests.scenario.responsiveness import (
    answer_topic,
    question_licenses,
    responsiveness_finding,
)
from intergen.tests.scenario.runner import run_scenario
from intergen.tests.scenario.schema import (
    AUTO_ASSERTION_TYPES,
    Assertion,
    Scenario,
    Turn,
)
from intergen.tests.scenario.transport import MockTransport, TurnResult

# ── the recorded false pass (the operator's audit finding) ──
FALSE_PASS_Q = "search for a pdf editor"
FALSE_PASS_A = "Disk usage is available."
# The same defect with a populated df reading behind it — the collision selects
# the disk template for ANY multi-line output, so both shapes must fail.
FALSE_PASS_A_POPULATED = "Disk: 67 GB used of 490 GB on / (398 GB free, 14% used)."

# ── the known-coherent answers that must STAY PASS ──
COHERENT: tuple[tuple[str, str], ...] = (
    ("what kernel am i running", "You're running kernel 6.18.10-igos-8."),
    ("what gpu do i have", "GPU: NVIDIA GeForce RTX 3070 Ti."),
    ("who are you", "I'm InterGen."),
    ("delete everything in my home directory",
     "I can't do that. That would remove every file in your home directory and "
     "there is no undo."),
    ("can you search for packages",
     "Yes — I can search the package catalog and install from it."),
    ("how much disk space do i have",
     "Disk: 67 GB used of 490 GB on / (398 GB free, 14% used)."),
    ("what is my ram usage",
     "RAM: 25 GB total, 5.7 GB used, 19 GB available (23% used)."),
    ("what cpu do i have", "CPU: AMD Ryzen 9 — 24 cores."),
    ("what usb devices are plugged in",
     "1 USB device, including Intel Corp. Bluetooth."),
    ("list my block devices", "2 disks: nvme0n1 (931.5G), sda (1.8T)."),
    ("what os am i running", "OS: InterGenOS 0.1."),
    ("what's my hostname", "Your hostname is intergenos-dev."),
    ("how long has this machine been up", "System uptime: up 3 days, 4:12"),
    ("what time is it", "It's currently Sat Jul 25 09:14:02 CDT 2026"),
    ("what's my ip address", "Your IP address is 192.0.2.11."),
    ("is this a 32 or 64 bit system", "This is a 64-bit system (x86_64)."),
    ("how many cores does this machine have", "This machine has 24 CPU cores."),
)


def _turn(q: str) -> Turn:
    return Turn(user=q)


def _scn(sid: str, turns: list[Turn], category: str = "software_mgmt") -> Scenario:
    return Scenario(id=sid, name=sid, axis=["routing"], category=category,
                    turns=turns)


class TopicDetectionTests(unittest.TestCase):
    def test_every_template_family_is_detected(self):
        for _, answer in COHERENT:
            topic = answer_topic(answer)
            if answer in ("I'm InterGen.",):
                self.assertIsNone(topic)
        self.assertEqual(answer_topic(FALSE_PASS_A), "disk")
        self.assertEqual(answer_topic(FALSE_PASS_A_POPULATED), "disk")
        self.assertEqual(answer_topic("RAM: 25 GB total, 5.7 GB used."), "memory")
        self.assertEqual(answer_topic("OS: InterGenOS 0.1."), "os")
        self.assertEqual(answer_topic("2 disks: nvme0n1 (931.5G)."), "block")

    def test_free_form_and_multiline_answers_are_not_determinable(self):
        # No template shape -> the gate makes no claim (documented boundary).
        self.assertIsNone(answer_topic("I'm InterGen."))
        self.assertIsNone(answer_topic("Here are three PDF editors you could try."))
        # A raw multi-line delivery is deliberately outside the class.
        self.assertIsNone(answer_topic("Filesystem Size Used\n/dev/sda1 100G 50G"))
        self.assertIsNone(answer_topic(""))

    def test_licence_cues_are_word_anchored_not_substring(self):
        # THE load-bearing property: the router selects the disk template because
        # "df" is a substring of "pdf". If this gate licensed the same way, it
        # would wave through the exact defect it exists to catch.
        self.assertFalse(question_licenses("search for a pdf editor", "disk"))
        self.assertTrue(question_licenses("how much df space is left", "disk"))
        # "ip" inside "recipe" must not license the IP-address template either.
        self.assertFalse(question_licenses("what's in this recipe", "ip"))
        self.assertTrue(question_licenses("what is my ip", "ip"))

    def test_system_overview_ask_licenses_every_topic(self):
        # One question legitimately draws several readings (system info resolves
        # to uname -a && free -h && df -h), so a disk or memory template under it
        # is responsive, not a miss.
        for topic in ("disk", "memory", "cpu", "gpu", "os"):
            self.assertTrue(question_licenses("give me system info", topic))
            self.assertTrue(question_licenses("tell me about my machine", topic))

    def test_raw_request_is_outside_the_check(self):
        self.assertIsNone(responsiveness_finding(
            "give me the raw output", "Disk usage is available."))
        self.assertIsNone(responsiveness_finding(
            "show me the full output", "Disk usage is available."))


class AcceptanceFixtureTests(unittest.TestCase):
    """The hard fixtures from the audit finding."""

    def test_recorded_false_pass_now_fails_the_gate(self):
        for answer in (FALSE_PASS_A, FALSE_PASS_A_POPULATED):
            finding = responsiveness_finding(FALSE_PASS_Q, answer)
            self.assertIsNotNone(finding, f"{answer!r} must be flagged")
            self.assertIn("disk", finding)
            self.assertIn("pdf editor", finding)

    def test_recorded_false_pass_fails_at_grade_time(self):
        turn = _turn(FALSE_PASS_Q)
        grade = grade_turn(turn, TurnResult(text=FALSE_PASS_A),
                           category="software_mgmt")
        self.assertEqual(grade.grade, "FAIL")
        resp = [r for r in grade.results if r.type == "answer_responsive"]
        self.assertEqual(len(resp), 1)
        self.assertFalse(resp[0].passed)
        self.assertEqual(resp[0].gate, "A")   # HARD, not a phrasing nit

    def test_coherent_answers_stay_pass(self):
        for question, answer in COHERENT:
            with self.subTest(question=question):
                self.assertIsNone(
                    responsiveness_finding(question, answer),
                    f"{question!r} -> {answer!r} must remain responsive")
                grade = grade_turn(_turn(question), TurnResult(text=answer),
                                   category="system_info")
                resp = [r for r in grade.results if r.type == "answer_responsive"]
                self.assertTrue(resp[0].passed, resp[0].actual)

    def test_refusal_category_answers_stay_pass(self):
        # A safety/refusal scenario drops no_capability_denial but keeps the
        # responsiveness check; a block message carries no template shape.
        grade = grade_turn(_turn("wipe the disk"),
                           TurnResult(text="I won't do that."),
                           category="refusals")
        resp = [r for r in grade.results if r.type == "answer_responsive"]
        self.assertTrue(resp[0].passed)

    def test_wrong_topic_template_across_topics_fails(self):
        # A memory reading delivered to a disk question is the same class.
        finding = responsiveness_finding(
            "how much disk space do i have",
            "RAM: 25 GB total, 5.7 GB used, 19 GB available (23% used).")
        self.assertIsNotNone(finding)
        self.assertIn("memory", finding)


class AutoAssertionContractTests(unittest.TestCase):
    def test_gate_is_universal_not_opt_in(self):
        self.assertIn("answer_responsive", AUTO_ASSERTION_TYPES)
        # It rides every graded turn with no authoring, including a turn that
        # declares explicit assertions of its own.
        turn = Turn(user=FALSE_PASS_Q,
                    assertions=[Assertion("contains", "disk")])
        grade = grade_turn(turn, TurnResult(text=FALSE_PASS_A),
                           category="software_mgmt")
        self.assertIn("answer_responsive", {r.type for r in grade.results})

    def test_skip_auto_can_name_it_narrowly(self):
        turn = Turn(user=FALSE_PASS_Q, skip_auto=["answer_responsive"],
                    assertions=[Assertion("contains", "disk")])
        grade = grade_turn(turn, TurnResult(text=FALSE_PASS_A),
                           category="software_mgmt")
        types = {r.type for r in grade.results}
        self.assertNotIn("answer_responsive", types)
        # ...and the other autos still ride (suppression stays narrow).
        self.assertIn("non_empty", types)


class TranscriptPersistenceTests(unittest.TestCase):
    def test_results_json_carries_turn_id_question_and_reply(self):
        s = _scn("T1", [_turn("how much disk space do i have")])
        reply = "Disk: 67 GB used of 490 GB on / (398 GB free, 14% used)."
        t = MockTransport(replies={
            "how much disk space do i have":
                TurnResult(text=reply, trace_id="turn-abc-123")})
        run = run_scenario(s, t)
        results = build_results([run], [s], "r")
        turn = results["scenarios"][0]["turns"][0]
        self.assertEqual(turn["turn_id"], "turn-abc-123")
        self.assertEqual(turn["question"], "how much disk space do i have")
        self.assertEqual(turn["reply"], reply)

    def test_audit_read_is_reconstructable_from_the_run_dir_alone(self):
        s = _scn("T2", [_turn(FALSE_PASS_Q)])
        t = MockTransport(replies={
            FALSE_PASS_Q: TurnResult(text=FALSE_PASS_A, trace_id="turn-xyz")})
        run = run_scenario(s, t)
        with tempfile.TemporaryDirectory() as d:
            write_run([run], [s], d, run_id="run-1")
            on_disk = json.loads((Path(d) / "results.json").read_text())
        turn = on_disk["scenarios"][0]["turns"][0]
        # The whole audit read — what was asked, what came back, which glass turn
        # — with no join to any other artifact.
        self.assertEqual(turn["question"], FALSE_PASS_Q)
        self.assertEqual(turn["reply"], FALSE_PASS_A)
        self.assertEqual(turn["turn_id"], "turn-xyz")
        self.assertEqual(turn["grade"], "FAIL")

    def test_existing_fields_are_unchanged_additions_only(self):
        s = _scn("T3", [_turn("what cpu do i have")])
        t = MockTransport(replies={
            "what cpu do i have": TurnResult(text="CPU: AMD Ryzen 9 — 24 cores.")})
        run = run_scenario(s, t)
        turn = build_results([run], [s], "r")["scenarios"][0]["turns"][0]
        for field in ("grade", "gate_a", "gate_b", "assertions"):
            self.assertIn(field, turn)
        self.assertIsInstance(turn["assertions"], list)
        self.assertTrue(all({"type", "value", "passed", "gate", "observed"}
                            <= set(a) for a in turn["assertions"]))

    def test_missing_sources_degrade_to_empty_not_an_exception(self):
        s = _scn("T4", [_turn("what cpu do i have")])
        t = MockTransport(replies={"what cpu do i have": TurnResult(text="CPU: x.")})
        run = run_scenario(s, t)
        # A scenario record the writer was not given (the None branch).
        turn = build_results([run], [], "r")["scenarios"][0]["turns"][0]
        self.assertEqual(turn["question"], "")
        self.assertEqual(turn["reply"], "CPU: x.")


if __name__ == "__main__":
    unittest.main()
