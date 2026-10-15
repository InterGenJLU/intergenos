# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A refused hook recording is reported whatever shell options the caller set."""

import os
import re
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WARNING = "WARNING: pkm record-hook-changes failed for sample"
REFUSAL = "error: the hook baseline could not be compared"
RECORDED = "sample: 0 own payload file(s) recorded as hook-managed"

# The two option sets the build drivers call the recorder under: errexit
# alone, and errexit with pipefail.
ERREXIT = "set -e"
ERREXIT_PIPEFAIL = "set -e\nset -o pipefail"

# A stand-in pkm on PATH: it records its arguments, then either refuses the
# way pkm does (its own error on stderr, exit 1) or reports a recording.
STAND_IN = f"""#!/usr/bin/bash
printf '%s\\n' "$*" >> "$PKM_CALLS"
if [ "$PKM_REFUSES" = yes ]; then
    printf '%s\\n' {shlex.quote(REFUSAL)} >&2
    exit 1
fi
printf '%s\\n' {shlex.quote(RECORDED)}
"""

AFTER = r'''
printf 'RETURNED %s\n' "$?"
printf 'BASELINE [%s]\n' "$PKG_HOOK_BASELINE_FILE"
for option in errexit pipefail; do
    if shopt -qo "$option"; then printf 'AFTER %s on\n' "$option"; else printf 'AFTER %s off\n' "$option"; fi
done
'''


def source():
    ref = os.environ.get("IGOS_TEST_SCRIPT_REF")
    if ref:
        return subprocess.run(
            ["/usr/bin/git", "-C", str(ROOT), "show", f"{ref}:scripts/pkg-functions.sh"],
            check=True, capture_output=True, text=True,
        ).stdout
    return (ROOT / "scripts/pkg-functions.sh").read_text()


def function(text, name):
    match = re.search(rf"^{name}\(\) \{{\n.*?^\}}$", text, re.M | re.S)
    if not match:
        raise AssertionError(name)
    return match.group()


class HookRecordingFailureIsReported(unittest.TestCase):
    def record(self, options, refuses):
        text = source()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bin").mkdir()
            (root / "logs").mkdir()
            pkm = root / "bin/pkm"
            pkm.write_text(STAND_IN)
            pkm.chmod(0o755)
            baseline = root / "baseline"
            baseline.write_text("baseline\n")
            script = root / "record.sh"
            script.write_text(
                options + "\n" +
                "IGOS_LOGS=" + shlex.quote(str(root / "logs")) + "\n" +
                "PKG_HOOK_BASELINE_FILE=" + shlex.quote(str(baseline)) + "\n" +
                function(text, "pkg_log") + "\n" +
                function(text, "pkg_record_hook_changes") + "\n" +
                "pkg_record_hook_changes sample\n" + AFTER)
            result = subprocess.run(
                ["/usr/bin/bash", str(script)], cwd=root,
                env={"PATH": f"{root / 'bin'}:/usr/bin:/bin",
                     "PKM_CALLS": str(root / "calls"),
                     "PKM_REFUSES": "yes" if refuses else "no"},
                capture_output=True, text=True, timeout=10,
            )
            log = root / "logs/pkg-install.log"
            return {
                "result": result,
                "log": log.read_text() if log.exists() else "",
                "calls": (root / "calls").read_text(),
                "baseline": str(baseline),
                "baseline_left": baseline.exists(),
            }

    def assert_reported(self, options):
        run = self.record(options, refuses=True)
        out = run["result"].stdout
        self.assertEqual(run["result"].returncode, 0, out + run["result"].stderr)
        self.assertIn("RETURNED 0\n", out)
        self.assertIn("  " + REFUSAL + "\n", out)
        self.assertIn(WARNING, out)
        self.assertIn(WARNING, run["log"])

    def assert_quiet(self, options):
        run = self.record(options, refuses=False)
        out = run["result"].stdout
        self.assertEqual(run["result"].returncode, 0, out + run["result"].stderr)
        self.assertIn("RETURNED 0\n", out)
        self.assertIn("  " + RECORDED + "\n", out)
        self.assertNotIn("WARNING", out)
        self.assertNotIn("WARNING", run["log"])

    def test_a_refusal_is_reported_under_errexit_alone(self):
        self.assert_reported(ERREXIT)

    def test_a_refusal_is_reported_under_errexit_and_pipefail(self):
        self.assert_reported(ERREXIT_PIPEFAIL)

    def test_a_recording_prints_no_warning_under_errexit_alone(self):
        self.assert_quiet(ERREXIT)

    def test_a_recording_prints_no_warning_under_errexit_and_pipefail(self):
        self.assert_quiet(ERREXIT_PIPEFAIL)

    def test_the_callers_options_are_unchanged_by_the_call(self):
        for options, pipefail in ((ERREXIT, "off"), (ERREXIT_PIPEFAIL, "on")):
            for refuses in (True, False):
                with self.subTest(options=options, refuses=refuses):
                    out = self.record(options, refuses)["result"].stdout
                    self.assertIn("AFTER errexit on\n", out)
                    self.assertIn(f"AFTER pipefail {pipefail}\n", out)

    def test_pkm_is_given_the_baseline_which_is_then_removed(self):
        for options in (ERREXIT, ERREXIT_PIPEFAIL):
            for refuses in (True, False):
                with self.subTest(options=options, refuses=refuses):
                    run = self.record(options, refuses)
                    self.assertEqual(
                        run["calls"],
                        f"record-hook-changes sample --baseline {run['baseline']}\n")
                    self.assertFalse(run["baseline_left"])
                    self.assertIn("BASELINE []\n", run["result"].stdout)


if __name__ == "__main__":
    unittest.main()
