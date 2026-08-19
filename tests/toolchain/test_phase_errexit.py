# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The toolchain driver must SEE a failure that happens inside a recipe phase.

THE DEFECT THIS PINS. scripts/toolchain-build.sh ran each recipe phase as the
left operand of `||`:

    configure >> "$LOG" 2>&1 || { log "FAILED in ..."; exit 1; }

A command in that position runs with errexit SUSPENDED, and the suspension
reaches every function the command calls — the `set -e` a recipe writes at the
top of its own configure()/build() does not restore it, and neither does a
subshell wrapper. The phase therefore ran to its end after a failed command and
reported the status of whatever happened to run last. Measured on the shipped
bash (5.3.0): a phase whose `cd` failed reported success, and the driver logged
"configure completed successfully".

That is not hypothetical for this tree: binutils-pass1's build() and install()
each begin with `cd build` while configure() had already left the shell inside
build/, so nine `cd` failures per toolchain run were absorbed silently.

THE THREE FORMS, measured (this test re-measures all three rather than citing
them, so the claim cannot drift from the shell's actual behaviour):

    phase >> log 2>&1 || { ...; }              failure NOT seen
    ( set -e; phase ) >> log 2>&1 || { ...; }  failure NOT seen
    set +e; ( set -e; phase ) >> log; rc=$?    failure seen

The subshell is load-bearing for a second reason: `set -e` inside a recipe
phase changes the option for the whole shell (bash has no function-local
option scope), so a phase called directly leaves errexit ON in a driver whose
own header states it must not run under errexit.
"""

import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER = REPO_ROOT / "scripts" / "lib" / "phase-run.sh"
DRIVER = REPO_ROOT / "scripts" / "toolchain-build.sh"


def run_bash(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


FAILING_PHASE = 'configure() { set -e; cd /nonexistent-igos-probe; echo CONTINUED; true; }'


class TestPhaseCallForms(unittest.TestCase):
    """The measurement the fix rests on — re-run, not quoted."""

    def test_bare_call_under_or_masks_the_failure(self):
        """The retired form: failure invisible, and the phase kept running."""
        r = run_bash(
            f'set +e; {FAILING_PHASE}\n'
            'out=$(configure 2>&1) || echo "CAUGHT"\n'
            'echo "STATUS=$?"; echo "OUT=$out"\n'
        )
        self.assertNotIn("CAUGHT", r.stdout, "the || form must not be treated as catching")
        self.assertIn("CONTINUED", r.stdout,
                      "measurement changed: the phase no longer continues past the failure")

    def test_subshell_under_or_also_masks_the_failure(self):
        """The form a reader would reach for second — equally blind."""
        r = run_bash(
            f'set +e; {FAILING_PHASE}\n'
            'out=$( ( set -e; configure ) 2>&1 ) || echo "CAUGHT"\n'
            'echo "OUT=$out"\n'
        )
        self.assertNotIn("CAUGHT", r.stdout)


class TestIgosRunPhase(unittest.TestCase):
    """The shipped helper."""

    def _helper(self, body: str) -> subprocess.CompletedProcess:
        return run_bash(f'set +e; source {HELPER}\n{body}')

    def test_helper_exists(self):
        self.assertTrue(HELPER.is_file(), f"{HELPER} missing")

    def test_failure_inside_the_phase_is_reported(self):
        r = self._helper(
            f'{FAILING_PHASE}\n'
            'igos_run_phase configure /dev/null\n'
            'echo "RC=$IGOS_PHASE_RC"\n'
        )
        self.assertIn("RC=1", r.stdout, r.stdout + r.stderr)

    def test_the_phase_stops_at_the_failing_command(self):
        r = self._helper(
            f'{FAILING_PHASE}\n'
            'log=$(mktemp)\n'
            'igos_run_phase configure "$log"\n'
            'cat "$log"; rm -f "$log"\n'
        )
        self.assertNotIn("CONTINUED", r.stdout,
                         "the phase ran past its failed command")

    def test_success_is_reported_as_success(self):
        r = self._helper(
            'configure() { set -e; cd /tmp; true; }\n'
            'igos_run_phase configure /dev/null\n'
            'echo "RC=$IGOS_PHASE_RC"\n'
        )
        self.assertIn("RC=0", r.stdout)

    def test_phase_set_e_does_not_leak_into_the_driver(self):
        r = self._helper(
            'configure() { set -e; true; }\n'
            'igos_run_phase configure /dev/null\n'
            'case $- in *e*) echo "LEAKED";; *) echo "CONTAINED";; esac\n'
        )
        self.assertIn("CONTAINED", r.stdout)

    def test_phase_cwd_does_not_leak_into_the_next_phase(self):
        """Every toolchain recipe that uses a build directory cd's into it in
        EVERY phase, so each phase must start where the previous one did."""
        r = self._helper(
            'here=$PWD\n'
            'configure() { set -e; cd /tmp; }\n'
            'igos_run_phase configure /dev/null\n'
            '[ "$PWD" = "$here" ] && echo "CWD-STABLE" || echo "CWD-MOVED"\n'
        )
        self.assertIn("CWD-STABLE", r.stdout)

    def test_caller_errexit_state_is_restored(self):
        r = run_bash(
            f'source {HELPER}\nset -e\n'
            'configure() { true; }\n'
            'igos_run_phase configure /dev/null\n'
            'case $- in *e*) echo "RESTORED";; *) echo "CLEARED";; esac\n'
        )
        self.assertIn("RESTORED", r.stdout)


class TestDriverUsesTheHelper(unittest.TestCase):
    """Structural: no phase call in the driver may use the retired form."""

    def setUp(self):
        self.text = DRIVER.read_text()

    def test_driver_sources_the_helper(self):
        self.assertIn("source /mnt/intergenos/scripts/lib/phase-run.sh", self.text)

    def test_no_raw_phase_call_remains(self):
        offenders = [
            line.strip() for line in self.text.splitlines()
            if any(line.lstrip().startswith(p + " >>") for p in
                   ("configure", "build", "install", "check"))
        ]
        self.assertEqual([], offenders,
                         "recipe phases must run through igos_run_phase")

    def test_driver_parses(self):
        r = subprocess.run(["bash", "-n", str(DRIVER)], capture_output=True, text=True)
        self.assertEqual(0, r.returncode, r.stderr)


if __name__ == "__main__":
    unittest.main()
