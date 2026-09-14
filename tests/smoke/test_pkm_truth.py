# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Truth controls for the installed-system package-manager smoke checks."""

from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SMOKE_DIR = REPO_ROOT / "installer" / "smoke"
LIB_SH = SMOKE_DIR / "lib.sh"
PKM_SH = SMOKE_DIR / "checks" / "pkm.sh"


class PkmSmokeTruthTests(unittest.TestCase):
    def run_check(
        self,
        function: str,
        stub_body: str | None,
        *,
        strict: bool = False,
    ) -> tuple[list[tuple[str, str, str]], list[str]]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            calls = root / "calls"
            if stub_body is not None:
                stub = bin_dir / "pkm"
                stub.write_text("#!/usr/bin/bash\n" + textwrap.dedent(stub_body))
                stub.chmod(0o755)

            script = textwrap.dedent(
                f"""
                set -uo pipefail
                SMOKE_JSON=1
                SMOKE_STRICT={1 if strict else 0}
                . "{LIB_SH}"
                . "{PKM_SH}"
                {function}
                for sol013_row in "${{SMOKE_RESULTS[@]}}"; do
                    printf '%s\\n' "$sol013_row"
                done
                """
            )
            env = dict(os.environ)
            env["PATH"] = (
                f"{bin_dir}:/usr/bin:/bin" if stub_body is not None else str(bin_dir)
            )
            env["SMOKE_PKM_CALLS"] = str(calls)
            result = subprocess.run(
                ["/usr/bin/bash", "-c", script],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            rows = []
            for line in result.stdout.splitlines():
                if line.count("|") >= 2:
                    status, check_id, message = line.split("|", 2)
                    rows.append((status, check_id, message))
            recorded_calls = calls.read_text().splitlines() if calls.exists() else []
            return rows, recorded_calls

    def one(self, rows: list[tuple[str, str, str]], check_id: str):
        matches = [row for row in rows if row[1] == check_id]
        self.assertEqual(len(matches), 1, rows)
        return matches[0]

    def test_list_uses_declared_package_count_not_presentation_lines(self):
        rows, _ = self.run_check(
            "check_pkm_list",
            r'''
            printf '%s\n' \
                '  Installed packages (2):' \
                '    alpha  1.0  [core]' \
                '        a wrapped description' \
                '        with a second line' \
                '    beta  2.0  [base]'
            ''',
        )
        status, _, message = self.one(rows, "pkm/list")
        self.assertEqual(status, "PASS")
        self.assertEqual(message, "2 packages")

    def test_list_empty_or_malformed_output_fails(self):
        rows, _ = self.run_check(
            "check_pkm_list",
            "printf '%s\\n' '  No packages installed'\n",
        )
        self.assertEqual(self.one(rows, "pkm/list")[0], "FAIL")

    def test_list_nonzero_exit_fails(self):
        rows, _ = self.run_check(
            "check_pkm_list",
            "printf '%s\\n' 'database unavailable' >&2\nexit 9\n",
        )
        self.assertEqual(self.one(rows, "pkm/list")[0], "FAIL")

    def test_info_uses_installed_name_and_requires_positive_installed_evidence(self):
        rows, calls = self.run_check(
            "check_pkm_info_marker",
            r'''
            printf '%s\n' "$*" >> "$SMOKE_PKM_CALLS"
            if [ "${1:-}" = info ] && [ "${2:-}" = glibc ]; then
                printf '%s\n' '  glibc 2.43-4' '  Files: 3285'
            else
                printf "  Package '%s' is not installed\n" "${2:-}"
            fi
            ''',
        )
        status, _, _ = self.one(rows, "pkm/info")
        self.assertEqual(status, "PASS")
        self.assertEqual(calls, ["info glibc"])

    def test_info_not_installed_with_exit_zero_fails(self):
        rows, _ = self.run_check(
            "check_pkm_info_marker glibc",
            "printf \"  Package '%s' is not installed\\n\" \"${2:-}\"\nexit 0\n",
        )
        self.assertEqual(self.one(rows, "pkm/info")[0], "FAIL")

    def test_files_parses_header_and_rejects_not_found_exit_zero(self):
        rows, _ = self.run_check(
            "check_pkm_files_marker glibc",
            r'''
            printf '%s\n' '  Files in glibc (7):' '    /usr/bin/example'
            ''',
        )
        status, _, message = self.one(rows, "pkm/files")
        self.assertEqual(status, "PASS")
        self.assertEqual(message, "glibc owns 7 tracked paths")

        rows, _ = self.run_check(
            "check_pkm_files_marker glibc",
            "printf \"  Package '%s' not found or has no tracked files\\n\" \"${2:-}\"\n",
        )
        self.assertEqual(self.one(rows, "pkm/files")[0], "FAIL")

    def test_verify_exit_three_is_warning_with_exact_root_rerun(self):
        rows, _ = self.run_check(
            "check_pkm_verify",
            "printf '%s\\n' 'checks could not run'\nexit 3\n",
        )
        status, _, message = self.one(rows, "pkm/verify")
        self.assertEqual(status, "WARN")
        self.assertIn(
            "re-run as root: /usr/bin/sudo /usr/bin/intergenos-smoke-test",
            message,
        )

    def test_verify_fast_success_does_not_claim_content_match(self):
        rows, _ = self.run_check("check_pkm_verify", "exit 0\n")
        status, _, message = self.one(rows, "pkm/verify")
        self.assertEqual(status, "PASS")
        self.assertIn("existence-only", message)
        self.assertNotIn("files match", message)

    def test_missing_package_manager_is_fail_not_green_skip(self):
        rows, _ = self.run_check("run_pkm_checks", None)
        status, _, _ = self.one(rows, "pkm/category")
        self.assertEqual(status, "FAIL")


if __name__ == "__main__":
    unittest.main()
