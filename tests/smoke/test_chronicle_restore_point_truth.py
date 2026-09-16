# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Truth controls for the installed-system restore-point health check.

R001.3 gating row 32. The check answers one question — can this machine be
returned to the system it was installed as? — and it must answer it
honestly in every direction: a timeline it cannot read is not an empty
timeline, and an empty timeline is not a healthy one.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SMOKE_DIR = REPO_ROOT / "installer" / "smoke"
LIB_SH = SMOKE_DIR / "lib.sh"
CHRONICLE_SH = SMOKE_DIR / "checks" / "chronicle.sh"

INSTALL_TIME = 1_757_000_000          # a fixed installation moment
BEFORE = INSTALL_TIME - 3600
AFTER = INSTALL_TIME + 3600


class RestorePointTruthTests(unittest.TestCase):

    def run_check(self, stub_body: str | None, *, anchor: bool = True):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            if stub_body is not None:
                stub = bin_dir / "chronicle"
                stub.write_text("#!/usr/bin/bash\n" + textwrap.dedent(stub_body))
                stub.chmod(0o755)

            machine_id = root / "machine-id"
            if anchor:
                machine_id.write_text("0123456789abcdef0123456789abcdef\n")
                os.utime(machine_id, (INSTALL_TIME, INSTALL_TIME))

            script = textwrap.dedent(
                f"""
                set -uo pipefail
                SMOKE_JSON=1
                SMOKE_STRICT=0
                . "{LIB_SH}"
                . "{CHRONICLE_SH}"
                check_chronicle_restore_point_covers_this_install
                for row in "${{SMOKE_RESULTS[@]}}"; do
                    printf '%s\\n' "$row"
                done
                """
            )
            env = dict(os.environ)
            env["PATH"] = f"{bin_dir}:/usr/bin:/bin"
            env["SMOKE_MACHINE_ID"] = str(machine_id)
            result = subprocess.run(
                ["/usr/bin/bash", "-c", script],
                capture_output=True, text=True, env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return [line for line in result.stdout.splitlines() if line.strip()]

    @staticmethod
    def _timeline(*stamps):
        entries = ", ".join(
            '{"version_id": "%010d-aaaaaaaaaaaa", "wall_clock": %d, "files": 1}'
            % (i + 1, s) for i, s in enumerate(stamps))
        return f"""
            printf '%s\\n' '[{entries}]'
            exit 0
            """

    def test_a_restore_point_taken_after_the_install_passes(self):
        rows = self.run_check(self._timeline(BEFORE, AFTER))
        self.assertEqual(len(rows), 1)
        self.assertIn("PASS", rows[0])
        self.assertIn("chronicle/restore-point", rows[0])

    def test_a_timeline_that_all_predates_the_install_warns(self):
        rows = self.run_check(self._timeline(BEFORE, BEFORE - 60))
        self.assertIn("WARN", rows[0])
        self.assertIn("predates this installation", rows[0])

    def test_an_empty_timeline_warns_rather_than_passing(self):
        rows = self.run_check("printf '%s\\n' '[]'\nexit 0\n")
        self.assertIn("WARN", rows[0])
        self.assertIn("no restore point exists", rows[0])

    def test_a_timeline_it_cannot_read_is_never_reported_as_empty(self):
        """An unprivileged run is refused by the engine; that is not a zero."""
        rows = self.run_check(
            "echo 'ERROR: not permitted to reach the Chronicle engine' >&2\n"
            "exit 4\n")
        self.assertIn("WARN", rows[0])
        self.assertIn("unreadable", rows[0])
        self.assertIn("as root", rows[0])
        self.assertNotIn("no restore point exists", rows[0])

    def test_output_that_is_not_a_timeline_warns_and_does_not_crash(self):
        rows = self.run_check("printf '%s\\n' 'not json at all'\nexit 0\n")
        self.assertIn("WARN", rows[0])
        self.assertIn("could not be parsed", rows[0])

    def test_no_installation_anchor_is_named_rather_than_assumed(self):
        rows = self.run_check(self._timeline(AFTER), anchor=False)
        self.assertIn("WARN", rows[0])
        self.assertIn("when this system was installed", rows[0])

    def test_the_check_runs_in_the_category(self):
        src = CHRONICLE_SH.read_text()
        self.assertIn("check_chronicle_restore_point_covers_this_install\n}", src)

    def test_the_boundary_second_counts_as_covering(self):
        """A capture in the same second as the anchor is not older than it."""
        rows = self.run_check(self._timeline(INSTALL_TIME))
        self.assertIn("PASS", rows[0])


if __name__ == "__main__":
    unittest.main()
