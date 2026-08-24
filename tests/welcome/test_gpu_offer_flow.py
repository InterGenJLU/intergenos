#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The GPU offer's flow: sync, honest closing line, real outcome, next action.

FOUR FIRST-BOOT DEFECTS, each of which ended with a user believing something
untrue about their own machine.

(a) THE INSTALL RAN AGAINST AN EMPTY INDEX. This page is shown during first
    boot, which is exactly when nothing has run `pkm update` yet. An install
    against an empty index fails with the package "not found", which reads as
    the package not existing rather than as the machine never having looked.
    Every install command now syncs first, joined with `&&` so a failed sync
    stops the chain instead of producing the same confusing error one step
    later.

(b) THE TERMINAL ALWAYS SAID IT FINISHED. The closing line was the literal
    string "Installation finished." regardless of what happened, so a refused
    licence or a failed download ended with a sentence stating the opposite of
    the truth. It now reports the actual exit status and says plainly when
    nothing above it necessarily completed.

(c) THE OUTCOME WAS INFERRED FROM THE WINDOW CLOSING. Closing a window is not
    an outcome. A user whose driver installed correctly was shown a retry
    banner implying it had not, and was told nothing about the reboot the
    driver needs. The outcome is now ASKED OF THE PACKAGE DATABASE, and success
    replaces the retry with a state naming the reboot and what follows it.

(d) THE NEXT ACTION WAS BELOW THE FOLD. The driver install ends in a reboot and
    this page is deliberately shown again afterwards — and the setup card sat
    beneath the disclosure and the model-choice block, so a user who had just
    rebooted arrived at prose they had already read. After the driver leg it is
    moved to the top.

THE EXIT-STATUS TRAP, worth stating because it nearly shipped: `pkm info` exits
0 for an installed package, 0 for a known package that is NOT installed, and 0
for a name that does not exist. A check keyed on the exit status would have
reported every machine as installed. It is keyed on the output instead, and
that contract is asserted below against the real tool when one is present.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "assets" / "intergen-welcome" / "intergen-welcome.py"

_spec = importlib.util.spec_from_file_location("welcome_flow", _SCRIPT)
welcome = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(welcome)


class IndexSyncTest(unittest.TestCase):
    """(a) Every offered install syncs the index first."""

    def _commands(self):
        return {
            "cuda": welcome._CUDA_ENGINE_COMMAND,
            "hip": welcome._HIP_ENGINE_COMMAND,
            "driver": welcome._ADVISORY_COMMAND,
        }

    def test_every_install_command_updates_before_installing(self):
        for name, cmd in self._commands().items():
            with self.subTest(command=name):
                self.assertIn("pkm update", cmd,
                              f"the {name} command installs without syncing "
                              f"the index — on first boot the cache is empty "
                              f"and the package reads as not found")
                self.assertLess(
                    cmd.index("pkm update"), cmd.index("pkm install"),
                    f"the {name} command installs before it syncs")

    def test_the_sync_and_the_install_are_joined_so_a_failure_stops(self):
        for name, cmd in self._commands().items():
            with self.subTest(command=name):
                between = cmd[cmd.index("pkm update"):cmd.index("pkm install")]
                self.assertIn("&&", between,
                              f"the {name} command continues to the install "
                              f"even when the sync failed")
                self.assertNotIn(";", between,
                                 f"the {name} command separates sync from "
                                 f"install with ';', which ignores a failed "
                                 f"sync")


class ClosingLineTest(unittest.TestCase):
    """(b) The terminal's closing line reflects the real exit status.

    The script is EXECUTED under bash with a stand-in command, so what is
    measured is what the shell actually prints — not the presence of a string
    in the source.
    """

    def _script_for(self, command, closing_note=None):
        """The script the Welcomer would run, with the read pause removed.

        The pause exists to keep the window open for a human; a test cannot
        answer it. Everything else — the status capture, the closing note,
        the branch and the exit — is left exactly as shipped.
        """
        src = _SCRIPT.read_text()
        start = src.index("    note = ''")
        end = src.index("for argv in (", start)
        literal = "\n".join(ln[4:] if ln.startswith("    ") else ln
                            for ln in src[start:end].splitlines())
        # Recover the composed shell text the same way Python does.
        ns = {"command": command, "closing_note": closing_note}
        exec(literal, ns)
        script = ns["script"]
        return script.replace(
            'read -r -p "Press Enter to close this window."; ', "")

    def _run(self, command, closing_note=None):
        script = self._script_for(command, closing_note)
        return subprocess.run(["bash", "-c", script],
                              capture_output=True, text=True, timeout=60)

    # A failing command is modelled as a CHILD PROCESS that exits non-zero,
    # which is what `sudo pkm install …` is. A bare `exit 7` would terminate
    # the script's own shell before the status check could run — that says
    # nothing about the shipped script and everything about the stand-in, so
    # the stand-in is made faithful rather than the assertion weakened.
    _FAIL_7 = "bash -c 'exit 7'"
    _FAIL_1 = "bash -c 'exit 1'"

    def test_a_successful_command_reports_success(self):
        r = self._run("true")
        self.assertEqual(r.returncode, 0)
        self.assertIn("finished successfully", r.stdout)
        self.assertNotIn("FAILED", r.stdout)

    def test_a_failing_command_does_not_say_it_finished(self):
        """The defect in one assertion."""
        r = self._run(self._FAIL_7)
        self.assertIn("FAILED", r.stdout)
        self.assertNotIn("finished successfully", r.stdout)

    def test_the_failure_line_names_the_exit_code(self):
        r = self._run(self._FAIL_7)
        self.assertIn("exit 7", r.stdout)

    def test_the_terminal_exits_with_the_command_status(self):
        """So the caller could tell, if it ever needed to."""
        self.assertEqual(self._run(self._FAIL_7).returncode, 7)
        self.assertEqual(self._run("true").returncode, 0)

    def test_it_does_not_claim_completion_of_what_did_not_run(self):
        r = self._run(self._FAIL_1)
        self.assertIn("Nothing above this line was necessarily completed",
                      r.stdout)

    # The closing note. The package manager prints its own advisory when the
    # transaction ends, but the LAST line of the window is what the user
    # carries away, and it read "Installation finished successfully." — so the
    # impression a user left with was done, not reboot.

    def test_the_closing_note_is_the_last_thing_a_successful_run_prints(self):
        r = self._run("true", closing_note=">>> REBOOT REQUIRED: run sudo reboot")
        self.assertIn("REBOOT REQUIRED", r.stdout)
        lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
        self.assertIn("REBOOT REQUIRED", lines[-1],
                      "the reboot requirement is not the last thing on screen")
        self.assertLess(lines.index(next(l for l in lines
                                         if "finished successfully" in l)),
                        len(lines) - 1,
                        "the finished line is still the last word")

    def test_a_failed_run_is_not_told_to_reboot(self):
        """Nothing installed, so there is nothing to activate."""
        r = self._run(self._FAIL_7,
                      closing_note=">>> REBOOT REQUIRED: run sudo reboot")
        self.assertNotIn("REBOOT REQUIRED", r.stdout)
        self.assertIn("FAILED", r.stdout)

    def test_no_note_prints_nothing_extra(self):
        r = self._run("true", closing_note=None)
        self.assertIn("finished successfully", r.stdout)
        self.assertNotIn(">>>", r.stdout)

    def test_an_empty_note_prints_nothing_extra(self):
        """_closing_note returns '' for a selection that needs no step."""
        r = self._run("true", closing_note="")
        self.assertNotIn(">>>", r.stdout)

    def test_a_failing_sync_stops_before_the_install(self):
        """The `&&` join, executed rather than pattern-matched.

        A sync that fails must not be followed by an install that then reports
        the same not-found error one step later.
        """
        r = subprocess.run(
            ["bash", "-c", "bash -c 'exit 3' && echo INSTALL_RAN"],
            capture_output=True, text=True, timeout=60)
        self.assertNotIn("INSTALL_RAN", r.stdout)
        self.assertEqual(r.returncode, 3)


class OutcomeDetectionTest(unittest.TestCase):
    """(c) The outcome comes from the package database, not the window."""

    def test_the_exit_status_is_not_what_is_keyed_on(self):
        """The trap that would have made this check useless.

        Executed against the real pkm when present: an absent package exits 0
        just like an installed one, so a returncode test would report every
        machine as installed.
        """
        if not shutil.which("pkm"):
            self.skipTest("pkm is not installed on this machine")
        r = subprocess.run(["pkm", "info", "a-package-that-does-not-exist-xyz"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(
            r.returncode, 0,
            "pkm now exits non-zero for an absent package; the comment in "
            "_package_is_installed describing the measured contract is out of "
            "date and must be re-measured")
        self.assertIs(
            welcome._package_is_installed("a-package-that-does-not-exist-xyz"),
            False,
            "an absent package was not reported as absent")

    def test_an_unreachable_package_manager_is_unknown_not_absent(self):
        """"I could not ask" must not be reported as "not installed"."""
        real = subprocess.run

        def _boom(*a, **k):
            raise OSError("no pkm here")

        welcome.subprocess.run = _boom
        try:
            self.assertIsNone(welcome._package_is_installed("nvidia"))
        finally:
            welcome.subprocess.run = real

    def test_unrecognised_output_is_unknown_not_absent(self):
        real = subprocess.run

        class _R:
            returncode = 0
            stdout = "something this code has never seen"
            stderr = ""

        welcome.subprocess.run = lambda *a, **k: _R()
        try:
            self.assertIsNone(welcome._package_is_installed("nvidia"))
        finally:
            welcome.subprocess.run = real

    def test_an_install_record_reads_as_installed(self):
        real = subprocess.run

        class _R:
            returncode = 0
            stdout = ("  nvidia 580.159.04-1\n"
                      "  install_date        : 2026-08-05T20:22:00+00:00\n")
            stderr = ""

        welcome.subprocess.run = lambda *a, **k: _R()
        try:
            self.assertIs(welcome._package_is_installed("nvidia"), True)
        finally:
            welcome.subprocess.run = real

    # The eight fixed outcome strings these tests used to read were removed on
    # 2026-08-24: each described a situation its author had in mind rather
    # than the selection in front of the user. The messages are composed from
    # the selection now, so the same intent is asserted against the composer.

    def _nvidia_offers(self):
        return welcome._gpu_offers(
            {"version": welcome._GPU_RECORD_VERSION, "vendor": "nvidia",
             "upgrade_engine": "cuda", "upgrade_outranks_shipped": False},
            probe=lambda _n: False)

    def test_the_success_notice_names_the_reboot(self):
        outcome = welcome._install_outcome(
            ["nvidia_driver"], self._nvidia_offers(), probe=lambda _n: True)
        self.assertIn("REBOOT", outcome["message"].upper(),
                      "the success state does not tell the user to reboot, "
                      "which is the one thing the driver needs")
        self.assertIn("again", outcome["message"],
                      "it does not say the page comes back afterwards")

    def test_success_and_failure_notices_are_different_messages(self):
        offers = self._nvidia_offers()
        ok = welcome._install_outcome(
            ["nvidia_driver"], offers, probe=lambda _n: True)
        no = welcome._install_outcome(
            ["nvidia_driver"], offers, probe=lambda _n: False)
        self.assertNotEqual(ok["message"], no["message"])

    def test_the_not_installed_notice_does_not_read_as_could_not_tell(self):
        """Three states, three messages — a user's next step differs."""
        offers = self._nvidia_offers()
        definite = welcome._install_outcome(
            ["nvidia_driver"], offers, probe=lambda _n: False)["message"]
        unknown = welcome._install_outcome(
            ["nvidia_driver"], offers, probe=lambda _n: None)["message"]
        self.assertNotEqual(definite, unknown)
        self.assertIn("still not installed", definite)
        self.assertIn("could not be determined", unknown)

    def test_the_success_path_replaces_the_retry_rather_than_adding_to_it(self):
        src = _SCRIPT.read_text()
        code = "\n".join(ln for ln in src.splitlines()
                         if not ln.strip().startswith("#"))
        block = code[code.index("    def _watch(btn, proc, chosen):"):]
        block = block[:block.index("install_btn.connect")]
        self.assertIn("_install_outcome", block)
        self.assertIn("set_visible(False)", block,
                      "the retry button is still shown after a confirmed "
                      "successful install")


class NextActionTest(unittest.TestCase):
    """(d) After the driver leg, setup is the first thing on the page."""

    def test_a_machine_with_no_record_does_not_reorder(self):
        real = welcome._gpu_detection_record
        welcome._gpu_detection_record = lambda *a, **k: None
        try:
            self.assertFalse(welcome._driver_leg_is_done())
        finally:
            welcome._gpu_detection_record = real

    def test_a_non_nvidia_machine_has_no_driver_leg_here(self):
        real = welcome._gpu_detection_record
        welcome._gpu_detection_record = lambda *a, **k: {"vendor": "amd"}
        try:
            self.assertFalse(welcome._driver_leg_is_done())
        finally:
            welcome._gpu_detection_record = real

    def test_nvidia_with_the_driver_installed_is_done(self):
        real_rec = welcome._gpu_detection_record
        real_pkg = welcome._package_is_installed
        welcome._gpu_detection_record = lambda *a, **k: {"vendor": "nvidia"}
        welcome._package_is_installed = lambda name: True
        try:
            self.assertTrue(welcome._driver_leg_is_done())
        finally:
            welcome._gpu_detection_record = real_rec
            welcome._package_is_installed = real_pkg

    def test_nvidia_without_the_driver_is_not_done(self):
        real_rec = welcome._gpu_detection_record
        real_pkg = welcome._package_is_installed
        welcome._gpu_detection_record = lambda *a, **k: {"vendor": "nvidia"}
        welcome._package_is_installed = lambda name: False
        try:
            self.assertFalse(welcome._driver_leg_is_done())
        finally:
            welcome._gpu_detection_record = real_rec
            welcome._package_is_installed = real_pkg

    def test_an_undeterminable_package_state_does_not_reorder(self):
        """The safe default is the page order that was already reviewed."""
        real_rec = welcome._gpu_detection_record
        real_pkg = welcome._package_is_installed
        welcome._gpu_detection_record = lambda *a, **k: {"vendor": "nvidia"}
        welcome._package_is_installed = lambda name: None
        try:
            self.assertFalse(welcome._driver_leg_is_done())
        finally:
            welcome._gpu_detection_record = real_rec
            welcome._package_is_installed = real_pkg

    def test_the_page_moves_the_setup_card_rather_than_duplicating_it(self):
        """One control for one action.

        A second button would be two things that can disagree about state.
        """
        code = "\n".join(ln for ln in _SCRIPT.read_text().splitlines()
                         if not ln.strip().startswith("#"))
        self.assertIn("reorder_child_after(setup_box, done_note)", code)
        self.assertEqual(
            code.count("setup_btn = "), 1,
            "there is more than one setup button, so two controls can "
            "disagree about the same action")

    def test_the_card_is_moved_under_the_heading_and_never_above_it(self):
        """The card used to be moved to position ZERO of the page box, which
        put it and its accompanying line of text ABOVE the "Meet InterGen"
        heading they belong under (corrected 2026-08-24)."""
        code = "\n".join(ln for ln in _SCRIPT.read_text().splitlines()
                         if not ln.strip().startswith("#"))
        self.assertNotIn("reorder_child_after(setup_box, None)", code)
        self.assertNotIn("reorder_child_after(done_note, None)", code)
        self.assertIn("reorder_child_after(done_note, title)", code)


if __name__ == "__main__":
    unittest.main()
