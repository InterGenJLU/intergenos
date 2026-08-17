# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""M8-2 (result-delivery invariant) + M8-4 (script/file lifecycle) — wave 2.

Trace-grounded from the demand-corpus discovery ledger (m8-wave1-ledger):

M8-2 — dispatched_but_discarded (sf_dispatch_run_command-20): a SUCCEEDING read
whose value never reached the delivered answer (an empty delivery, or a deflection
"I don't have current data" rendered ALONGSIDE the result). Was silent; now a
NAMED, LOUD defect (safety.find_unconsumed_dispatches) asserted at every delivery
chokepoint, plus the streaming synthesis-skip is recovered so the value is never
dropped.

M8-4 — fabrication_action x6 (dd-do-0108, dd-do-0127, sf-dispatch-run-command-13,
sf-dispatch-write-file-27, sf-offer-decline-0): a create/save ask the model
NARRATED as done ("I've created the folders") with nothing dispatched. The belt
(detect_file_lifecycle_intent) stages a gated write_file / mkdir OFFER instead —
the action lands ONLY through the consent gate, so a fabricated completion is
structurally impossible. Execution gating is byte-identical: a staged write_file is
a CONFIRM dispatch that fails closed without a review UI and denies on a deny-choice.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from intergen import safety
from intergen.interfaces.types import ToolCall, ToolResult
from intergen.interfaces.provenance import Provenance
from intergen.router import detect_file_lifecycle_intent, ConversationRouter
from intergen.tool_registry import ToolRegistry


class M8ResultDeliveryTests(unittest.TestCase):
    """M8-2 result-delivery invariant — safety.find_unconsumed_dispatches."""

    def _ok(self):
        return ToolResult(call_id="1", name="run_command",
                          content="Disk: 59 GB used of 490 GB",
                          success=True, executed=True)

    def test_empty_delivery_flagged(self):
        # RED: a successful dispatch but a blank answer (the streaming synthesis
        # skip that shipped an empty reply) — a named defect.
        problems = safety.find_unconsumed_dispatches("", [self._ok()])
        self.assertEqual([r for _, r in problems], ["empty_delivery"])

    def test_deflection_despite_result_flagged(self):
        # RED: a successful dispatch but the answer deflects (the compound
        # sf_dispatch_run_command-20 shape).
        problems = safety.find_unconsumed_dispatches(
            "I don't have current data on that.", [self._ok()])
        self.assertEqual([r for _, r in problems], ["deflection_despite_result"])

    def test_honest_paraphrase_clean(self):
        # GREEN: a paraphrased summary that carries the value is NOT flagged.
        self.assertEqual(
            safety.find_unconsumed_dispatches(
                "Your disk has 406 GB free (12% used).", [self._ok()]), [])

    def test_refusal_template_not_flagged(self):
        # Byte-identical gating: a blocked / unsuccessful dispatch legitimately
        # delivers a deterministic refusal template and is NEVER a delivery defect.
        blocked = ToolResult(call_id="2", name="run_command", content="blocked",
                             success=False, executed=False, blocked=True)
        self.assertEqual(
            safety.find_unconsumed_dispatches(
                "I can't verify that without running a tool.", [blocked]), [])


class M8FileLifecycleDetectTests(unittest.TestCase):
    """M8-4 intent detection — the fabrication_action ledger shapes."""

    HOME = "/home/tester"

    def test_dir_create_month_range(self):  # dd-do-0127
        spec = detect_file_lifecycle_intent(
            "make me 12 folders named january through december", home=self.HOME)
        self.assertEqual(spec["tool"], "run_command")
        self.assertTrue(spec["display"].startswith("mkdir -p "))
        self.assertEqual(spec["display"].count(self.HOME + "/"), 12)

    def test_dir_create_single(self):  # sf-dispatch-run-command-13
        spec = detect_file_lifecycle_intent(
            "would you mind make a new directory called projects in my home folder",
            home=self.HOME)
        self.assertEqual(spec["tool"], "run_command")
        self.assertEqual(spec["display"], f"mkdir -p {self.HOME}/projects")

    def test_file_create_at_path(self):  # sf-dispatch-write-file-27
        spec = detect_file_lifecycle_intent(
            "could you please create a config file at ~/.config/myapp/config.ini",
            home=self.HOME)
        self.assertEqual(spec["tool"], "write_file")
        self.assertEqual(spec["args"]["path"], f"{self.HOME}/.config/myapp/config.ini")
        self.assertEqual(spec["args"]["content"], "")

    def test_save_prior_draft(self):  # dd-do-0108
        spec = detect_file_lifecycle_intent(
            "looks good save it as a text file",
            prior_draft="Dear Manager, I resign.", home=self.HOME)
        self.assertEqual(spec["tool"], "write_file")
        self.assertEqual(spec["args"]["content"], "Dear Manager, I resign.")
        self.assertTrue(spec["args"]["path"].endswith(".txt"))

    def test_questions_and_unrelated_decline(self):
        # High-precision: a how-to question or an unrelated turn stages NOTHING
        # (falls through to the model, itself gated under M8-1).
        for q in ("how do I create a file?", "what is the meaning of life",
                  "save my settings please"):
            self.assertIsNone(
                detect_file_lifecycle_intent(q, home=self.HOME), q)


class M8StagedActionGatingTests(unittest.TestCase):
    """M8-4 execution gating is byte-identical — a staged write_file is a CONFIRM
    dispatch through the SAME ToolRegistry.execute gate as any other action."""

    def setUp(self):
        self.registry = ToolRegistry()
        self.registry.discover_tools()

    def _write_call(self, path):
        return ToolCall(name="write_file",
                        arguments={"path": path, "content": "x"},
                        source_of_request=Provenance.USER_DIRECT)

    def test_write_file_fails_closed_without_review_ui(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "new.txt")
            result = self.registry.execute(self._write_call(path),
                                           review_callback=None)
            self.assertFalse(result.success)
            self.assertFalse(result.executed)
            self.assertFalse(os.path.exists(path))  # nothing landed

    def test_write_file_denied_on_deny_choice(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "new.txt")
            result = self.registry.execute(self._write_call(path),
                                           review_callback=lambda c, dc: "deny")
            self.assertFalse(result.executed)
            self.assertFalse(os.path.exists(path))

    @unittest.skipUnless(
        os.environ.get("INTERGEN_TEST_REAL_PKEXEC"),
        "skipped: INTERGEN_TEST_REAL_PKEXEC unset — this test drives the REAL "
        "write_file privileged runner, which does a genuine pkexec round-trip and "
        "pops an INTERACTIVE polkit auth modal. A headless/routine run cannot "
        "authenticate, so the approval is denied and the assertion false-fails. Set "
        "INTERGEN_TEST_REAL_PKEXEC=1 to run it where interactive auth exists (an "
        "instrument box, or CI with a polkit auth agent).")
    def test_write_file_lands_only_on_approve(self):
        """Approve -> the staged write_file LANDS through the gate.

        OPT-IN (INTERGEN_TEST_REAL_PKEXEC): this is the ONE test that drives the real
        pkexec privileged runner end-to-end, so it authenticates to polkit for real
        and pops an interactive modal. It is skipped by default so routine seat test
        runs stay non-interactive — an unattended run can only deny the modal, which
        would false-fail this assertion. Run it deliberately where interactive auth
        exists (an instrument box, or CI with a polkit auth agent) as the named
        real-pkexec row in the instrument battery. The mocked-pkexec coverage of the
        token/gate behavior lives in test_privileged_dispatch_gate and runs always.
        """
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "new.txt")
            result = self.registry.execute(
                self._write_call(path),
                review_callback=lambda c, dc: "allow_once")
            self.assertTrue(result.executed and result.success)
            self.assertTrue(os.path.exists(path))  # lands ONLY through the gate


class M8StagedDispatchRoutingTests(unittest.TestCase):
    """_run_staged_action dispatches the staged TOOL (write_file vs run_command),
    never re-derived — the staged offer is the only path to the action."""

    def _router(self):
        r = ConversationRouter.__new__(ConversationRouter)
        r._ingress_tracker = mock.Mock()
        r._trust_state = mock.Mock()
        r._review_callback = None
        r._conversation_history = []
        captured = {}

        def _exec(call, **kw):
            captured["call"] = call
            return ToolResult(call_id="1", name=call.name, content="done",
                              success=True, executed=True)
        r._tools = mock.Mock()
        r._tools.execute.side_effect = _exec
        r._append_history = lambda *a, **k: None
        r._template_synthesis = lambda *a, **k: "ok"
        r._synthesize_tool_result = lambda *a, **k: "done"
        return r, captured

    def test_staged_write_file_dispatches_write_file(self):
        r, captured = self._router()
        r._run_staged_action("/home/t/config.ini", "write_file",
                             {"path": "/home/t/config.ini", "content": ""})
        self.assertEqual(captured["call"].name, "write_file")
        self.assertEqual(captured["call"].arguments["path"], "/home/t/config.ini")

    def test_staged_run_command_dispatches_run_command(self):
        r, captured = self._router()
        r._run_staged_action("mkdir -p /home/t/projects", "run_command", None)
        self.assertEqual(captured["call"].name, "run_command")
        self.assertEqual(captured["call"].arguments["command"],
                         "mkdir -p /home/t/projects")


if __name__ == "__main__":
    unittest.main()
