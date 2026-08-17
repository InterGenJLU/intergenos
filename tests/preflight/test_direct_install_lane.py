#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""scripts/preflight-direct-install-lane.py — the archive-payload contract gate.

`direct_install: true` tells a builder that do_install writes to absolute paths
and that the package's file set must be derived by diffing filesystem snapshots.
Only igos-build implements that. The bash builder tars its DESTDIR staging tree
into the archive and never reads the flag, so a recipe declaring it on that lane
builds green, deploys its payload to the live filesystem, and ships an archive
containing none of it — correct on the image, missing on every install.

Two properties are asserted here, and the second matters as much as the first:
the gate must catch a recipe on the bash lane, and it must NOT fire on a recipe
igos-build builds, where the flag is honored and legitimate. A gate that cannot
tell those apart would push recipes off a lane that serves them correctly.

The live-tree case is asserted last: the repository as committed must pass, so
this file fails if the tree regresses rather than only if the checker does.
"""
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = REPO_ROOT / "scripts" / "preflight-direct-install-lane.py"

_spec = importlib.util.spec_from_file_location("preflight_direct_install_lane",
                                               GATE)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


class DirectInstallLaneGateTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        (self.root / "scripts").mkdir()
        for driver in _mod.BASH_DRIVERS:
            (self.root / driver).write_text("#!/bin/bash\n")

    def tearDown(self):
        self._td.cleanup()

    def _recipe(self, tier_root, name, body):
        d = self.root / tier_root / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "package.yml").write_text(body)

    def _driver(self, driver, lines):
        (self.root / driver).write_text("#!/bin/bash\n" + "\n".join(lines) + "\n")

    # ---- the defect --------------------------------------------------------

    def test_flags_a_bash_built_recipe(self):
        self._recipe("packages/core", "demo-pass2",
                     'name: demo-pass2\nversion: "1.0"\nrelease: 1\n'
                     'direct_install: true\n')
        self._driver("scripts/chroot-build-core-extra.sh",
                     ['run_package "demo-pass2" "demo-pass2" "1.0" \\',
                      '    "demo-1.0.tar.xz" "demo"'])

        violations = _mod.scan(self.root)

        self.assertEqual([v["package"] for v in violations], ["demo-pass2"])
        self.assertEqual(violations[0]["driver"],
                         "scripts/chroot-build-core-extra.sh")
        self.assertEqual(violations[0]["line"], 2,
                         "the report must point at the invocation line so the "
                         "reader can see which lane builds it")

    def test_flags_the_base_tier_driver_too(self):
        """Coverage is per-driver; base builds through the same pipeline."""
        self._recipe("packages/base", "widget",
                     'name: widget\nversion: "2.0"\nrelease: 1\n'
                     'direct_install: true\n')
        self._driver("scripts/chroot-build-base.sh",
                     ['build_base_package "widget" "widget" "2.0" \\',
                      '    "widget-2.0.tar.gz" "widget"'])

        self.assertEqual([v["package"] for v in _mod.scan(self.root)],
                         ["widget"])

    # ---- what must NOT fire ------------------------------------------------

    def test_ignores_a_recipe_no_bash_driver_builds(self):
        """igos-build implements the diff — the flag is correct there."""
        self._recipe("packages/desktop", "systemd-pass2",
                     'name: systemd-pass2\nversion: "1.0"\nrelease: 1\n'
                     'direct_install: true\n')
        self.assertEqual(_mod.scan(self.root), [],
                         "a package on the lane that honors the flag must not "
                         "be pushed off it by this gate")

    def test_ignores_a_staged_recipe_on_the_bash_lane(self):
        self._recipe("packages/core", "plain",
                     'name: plain\nversion: "1.0"\nrelease: 1\n')
        self._driver("scripts/chroot-build-ch8.sh",
                     ['run_package "plain" "plain" "1.0" "plain-1.0.tar.xz" "x"'])
        self.assertEqual(_mod.scan(self.root), [])

    def test_ignores_a_commented_out_invocation(self):
        self._recipe("packages/core", "retired",
                     'name: retired\nversion: "1.0"\nrelease: 1\n'
                     'direct_install: true\n')
        self._driver("scripts/chroot-build-core-extra.sh",
                     ['# run_package "retired" "retired" "1.0" "r.tar.xz" "x"'])
        self.assertEqual(_mod.scan(self.root), [],
                         "a commented-out invocation builds nothing")

    def test_ignores_an_indented_direct_install_key(self):
        """A nested mapping's key belongs to that mapping, not the package."""
        self._recipe("packages/core", "nested",
                     'name: nested\nversion: "1.0"\nrelease: 1\n'
                     'validation:\n  - direct_install: true\n')
        self._driver("scripts/chroot-build-ch8.sh",
                     ['run_package "nested" "nested" "1.0" "n.tar.xz" "x"'])
        self.assertEqual(_mod.scan(self.root), [])

    def test_ignores_direct_install_false(self):
        self._recipe("packages/core", "explicit",
                     'name: explicit\nversion: "1.0"\nrelease: 1\n'
                     'direct_install: false\n')
        self._driver("scripts/chroot-build-ch8.sh",
                     ['run_package "explicit" "explicit" "1.0" "e.tar.xz" "x"'])
        self.assertEqual(_mod.scan(self.root), [])

    # ---- the gate's own failure modes -------------------------------------

    def test_a_missing_driver_is_an_error_not_a_pass(self):
        """Silent coverage loss is the failure this gate exists to prevent."""
        (self.root / "scripts" / "chroot-build-ch8.sh").unlink()
        with self.assertRaises(FileNotFoundError):
            _mod.scan(self.root)

    def test_setup_error_exits_2_not_0(self):
        (self.root / "scripts" / "chroot-build-base.sh").unlink()
        rc = _mod.main(["--repo-root", str(self.root)])
        self.assertEqual(rc, 2, "a broken scan must never report clean")

    def test_exit_codes(self):
        self._recipe("packages/core", "bad",
                     'name: bad\nversion: "1.0"\nrelease: 1\n'
                     'direct_install: true\n')
        self._driver("scripts/chroot-build-ch8.sh",
                     ['run_package "bad" "bad" "1.0" "b.tar.xz" "x"'])
        self.assertEqual(_mod.main(["--repo-root", str(self.root)]), 1)

        self._driver("scripts/chroot-build-ch8.sh", ['# nothing built here'])
        self.assertEqual(_mod.main(["--repo-root", str(self.root)]), 0)

    # ---- the tree as committed --------------------------------------------

    def test_the_repository_passes(self):
        r = subprocess.run([sys.executable, str(GATE)],
                           capture_output=True, text=True, cwd=str(REPO_ROOT))
        self.assertEqual(r.returncode, 0,
                         f"the committed tree must satisfy its own gate:\n"
                         f"{r.stdout}\n{r.stderr}")


if __name__ == "__main__":
    unittest.main()
