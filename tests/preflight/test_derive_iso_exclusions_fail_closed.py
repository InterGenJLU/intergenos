# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Fail-closed tests for scripts/derive-iso-exclusions.py.

A package.yml that fails to parse joins neither the ISO list nor the
MIRROR list. The script previously warned and continued, so an
unclassifiable package was never evicted and silently SHIPPED — measured
as a 52-package ship-status delta with an unchanged summary line when a
parser/tree mismatch made a whole tier unparseable. These tests pin:

1. ANY parse failure refuses the whole run — exit 1, the failing file
   named on stderr, and NO output written (a partial exclusion list is
   indistinguishable from a complete one to every consumer).
2. The --packages default resolves to the tree THIS script lives in,
   never a fixed absolute path — the running copy classifies its own
   tree, which is what made the wrong-tree parse possible at all.
"""

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "derive-iso-exclusions.py"

GOOD_YML = """\
name: {name}
version: "1.0"
release: 1
description: test fixture package
license: MIT
source:
- url: https://example.org/{name}-1.0.tar.gz
  sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
dependencies:
  build: []
  host: []
  runtime: []
tier: {tier}
build_style: custom
verify_paths:
  - /usr/bin/{name}
"""


def write_pkg(packages: Path, tier: str, name: str, body: str | None = None):
    d = packages / tier / name
    d.mkdir(parents=True)
    (d / "package.yml").write_text(
        body if body is not None else GOOD_YML.format(name=name, tier=tier))


def run_script(packages: Path, output: Path):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--mode=names",
         "--packages", str(packages), "--output", str(output)],
        capture_output=True, text=True)


class TestFailClosed(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.packages = root / "packages"
        self.output = root / "out.txt"

    def test_broken_yaml_refuses_names_the_file_writes_nothing(self):
        write_pkg(self.packages, "core", "good")
        write_pkg(self.packages, "extra", "broken",
                  body="name: broken\nversion: [this is not\n")
        r = run_script(self.packages, self.output)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("REFUSED", r.stderr)
        self.assertIn("broken", r.stderr)
        self.assertFalse(
            self.output.exists(),
            "no output may be written when any package is unclassifiable")

    def test_clean_tree_still_partitions_and_exits_zero(self):
        write_pkg(self.packages, "core", "ships")
        write_pkg(self.packages, "extra", "mirrored")
        r = run_script(self.packages, self.output)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ISO packages:     1", r.stderr)
        self.assertIn("MIRROR packages:  1", r.stderr)
        names = self.output.read_text()
        self.assertIn("mirrored", names)
        self.assertNotIn("ships", names)


class TestSelfTreeDefault(unittest.TestCase):
    def test_packages_default_is_the_scripts_own_tree(self):
        # Import the script as a module (hyphenated filename) and pin the
        # ingredient the default is built from: the project root derived
        # from the script's OWN location, not any fixed absolute path.
        spec = importlib.util.spec_from_file_location(
            "derive_iso_exclusions_under_test", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertEqual(mod._project_root, REPO_ROOT)


if __name__ == "__main__":
    unittest.main()
