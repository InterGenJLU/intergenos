# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Round-trip real manifest output and compare manifest paths literally."""

import os
import re
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def functions():
    ref = os.environ.get("IGOS_TEST_SCRIPT_REF")
    if ref:
        text = subprocess.run(
            ["/usr/bin/git", "-C", str(ROOT), "show", f"{ref}:scripts/pkg-functions.sh"],
            check=True, capture_output=True, text=True,
        ).stdout
    else:
        text = (ROOT / "scripts/pkg-functions.sh").read_text()
    return "\n".join(re.search(rf"^{name}\(\) \{{\n.*?^\}}$", text, re.M | re.S).group()
                     for name in ("pkg_manifest", "pkg_owner"))


class ManifestOwner(unittest.TestCase):
    def run_owner(self, target, rows=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "packages"
            db.mkdir()
            stage = root / "staging/sample-1/usr/share"
            stage.mkdir(parents=True)
            for name in ("plain", "with space", r"back\slash", "[literal]", "a.b"):
                (stage / name).write_text("payload\n")
            setup = "set -euo pipefail\n"
            setup += "IGOS_PKG_DB=" + shlex.quote(str(db)) + "\n"
            setup += "IGOS_PKG_STAGING=" + shlex.quote(str(root / "staging")) + "\n"
            setup += 'pkg_log() { printf "%s\\n" "$*"; }\n'
            setup += 'pkg_error() { printf "%s\\n" "$*" >&2; }\n'
            if rows is not None:
                (db / "sample-1").write_text("PACKAGE NAME: sample-1\nFILE LIST:\n" + rows)
            script = root / "owner.sh"
            script.write_text(setup + functions() + "\n" +
                              ("pkg_manifest sample 1 description\n" if rows is None else "") +
                              "printf 'OWNERS\\n'\npkg_owner " + shlex.quote(target) + "\n")
            result = subprocess.run(
                ["/usr/bin/bash", str(script)], cwd=root,
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout.split("OWNERS\n", 1)[1]

    def test_writer_output_round_trips_files_and_directories(self):
        for target in ("/usr/share/plain", "/usr/share/with space", r"/usr/share/back\slash",
                       "/usr/share/[literal]", "/usr/share/a.b", "/usr/share", "/usr/share/"):
            with self.subTest(target=target):
                self.assertEqual(self.run_owner(target), "sample-1\n")

    def test_legacy_paths_are_literal_not_regular_expressions(self):
        rows = "usr/bin/aXb\nusr/bin/[literal]\nusr/bin/back\\slash\nusr/bin/with space\n"
        for target, expected in (("/usr/bin/a.b", ""), ("/usr/bin/.*", ""),
                                 ("/usr/bin/[literal]", "sample-1\n"),
                                 (r"/usr/bin/back\slash", "sample-1\n"),
                                 ("/usr/bin/with space", "sample-1\n")):
            with self.subTest(target=target):
                self.assertEqual(self.run_owner(target, rows), expected)

    def test_only_a_complete_digest_suffix_is_an_annotation(self):
        rows = "usr/bin/valid sha256:" + "a" * 64 + "\nusr/bin/literal sha256:invalid"
        for target, expected in (("/usr/bin/valid", "sample-1\n"),
                                 ("/usr/bin/literal", ""),
                                 ("/usr/bin/literal sha256:invalid", "sample-1\n")):
            with self.subTest(target=target):
                self.assertEqual(self.run_owner(target, rows), expected)

    def test_directory_separator_does_not_match_a_regular_file(self):
        self.assertEqual(self.run_owner("/usr/bin/plain/", "usr/bin/plain\n"), "")


if __name__ == "__main__":
    unittest.main()
