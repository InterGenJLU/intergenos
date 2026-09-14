# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Execute the manifest writer against real staged files and write failures."""

import hashlib
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def manifest_function():
    ref = os.environ.get("IGOS_TEST_SCRIPT_REF")
    if ref:
        text = subprocess.run(
            ["/usr/bin/git", "-C", str(ROOT), "show", f"{ref}:scripts/pkg-functions.sh"],
            check=True, capture_output=True, text=True,
        ).stdout
    else:
        text = (ROOT / "scripts/pkg-functions.sh").read_text()
    match = re.search(r"^pkg_manifest\(\) \{\n.*?^\}$", text, re.M | re.S)
    if not match:
        raise AssertionError("manifest function missing")
    return match.group()


class ManifestWrite(unittest.TestCase):
    def run_writer(self, failure=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stage = root / "staging" / "sample-1"
            stage.mkdir(parents=True)
            (stage / "hello").write_bytes(b"payload\n")
            db = root / "packages"
            db.mkdir()
            manifest = db / "sample-1"
            if failure == "directory":
                manifest.mkdir()
            elif failure == "full":
                manifest.symlink_to("/dev/full")
            setup = "set +e\nset -o pipefail\n"
            setup += 'IGOS_PKG_STAGING="$1/staging"\nIGOS_PKG_DB="$1/packages"\n'
            setup += 'pkg_log() { printf "%s\\n" "$*"; }\n'
            setup += 'pkg_error() { printf "%s\\n" "$*" >&2; }\n'
            if failure == "status":
                setup += 'cat() { /usr/bin/cat > /dev/null; return 47; }\n'
            script = root / "writer.sh"
            script.write_text(setup + manifest_function() + '\n' +
                              'pkg_manifest sample 1 description 4\nrc=$?\n'
                              'if [ "$rc" -eq 0 ]; then printf "DOWNSTREAM\\n"; fi\n'
                              'exit "$rc"\n')
            result = subprocess.run(
                ["/usr/bin/bash", str(script), str(root)], cwd=root,
                capture_output=True, text=True, timeout=10,
            )
            content = manifest.read_text() if failure is None else None
            return result, content

    def test_real_write_failures_prevent_success(self):
        for failure in ("directory", "full", "status"):
            with self.subTest(failure=failure):
                result, _ = self.run_writer(failure)
                self.assertEqual(result.returncode, 47 if failure == "status" else 1,
                                 result.stdout + result.stderr)
                self.assertIn("Manifest write failed", result.stderr)
                self.assertNotIn("Manifest written", result.stdout)
                self.assertNotIn("DOWNSTREAM", result.stdout)

    def test_success_records_payload_and_release(self):
        result, content = self.run_writer()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Manifest written", result.stdout)
        self.assertIn("DOWNSTREAM", result.stdout)
        self.assertIn("PACKAGE RELEASE: 4\n", content)
        self.assertIn("hello sha256:" + hashlib.sha256(b"payload\n").hexdigest(), content)


if __name__ == "__main__":
    unittest.main()
