#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""scripts/preflight-kernel-release-lockstep.py — release-coupled path derivation.

KERNELRELEASE is <version>-igos-<release>, computed by stamping linux-kernel's
`release:` field into CONFIG_LOCALVERSION. It names the kernel image, the module
tree, the UKI and the boot-menu entry. Recipes ALSO state those paths by hand in
verify_paths, with nothing deriving them — so a release bump silently invalidates
every literal until someone remembers.

Three misses on record (4 -> 6, 6 -> 7, then 7 -> 8), the first two caught hours
into a build by squashfs Step 4.5 and the third caught by this gate. The gate
derives the expected value from the same recipe build.sh reads and refuses a
build that would look for a kernel it will not produce.

What is asserted: the drift is caught, a correct tree passes, the derivation
follows BOTH the release and the version, the changelog comment on the release:
line does not false-positive on the historical names it deliberately cites, and
an unparseable source field is an error rather than a guessed default — a gate
that invents the value it checks against certifies nothing.
"""
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = REPO_ROOT / "scripts" / "preflight-kernel-release-lockstep.py"

_spec = importlib.util.spec_from_file_location("preflight_kernel_release_lockstep",
                                               GATE)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


class KernelReleaseLockstepTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self._write_kernel(version="6.18.10", release=8)

    def tearDown(self):
        self._td.cleanup()

    def _write_kernel(self, version, release, extra=""):
        d = self.root / "packages" / "core" / "linux-kernel"
        d.mkdir(parents=True, exist_ok=True)
        (d / "package.yml").write_text(
            f'name: linux-kernel\nversion: "{version}"\nrelease: {release}\n{extra}')

    def _write_recipe(self, tier, name, body):
        d = self.root / "packages" / tier / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "package.yml").write_text(body)

    # ---- the defect --------------------------------------------------------

    def test_stale_verify_path_is_caught(self):
        self._write_recipe("core", "linux-kernel-pass2",
                           'name: linux-kernel-pass2\nversion: "6.18.10"\n'
                           'release: 5\nverify_paths:\n'
                           '  - /boot/vmlinuz-6.18.10-igos-7\n'
                           '  - /usr/lib/modules/6.18.10-igos-7\n')

        expected, violations = _mod.scan(self.root)

        self.assertEqual(expected, "6.18.10-igos-8")
        self.assertEqual(len(violations), 2)
        self.assertEqual({v["found"] for v in violations}, {"6.18.10-igos-7"})

    def test_a_stale_version_is_caught_too(self):
        """The derivation is the whole name, not just the release suffix."""
        self._write_recipe("core", "linux-kernel-pass2",
                           'name: linux-kernel-pass2\nversion: "6.18.10"\n'
                           'release: 5\nverify_paths:\n'
                           '  - /boot/vmlinuz-6.17.4-igos-8\n')

        _expected, violations = _mod.scan(self.root)

        self.assertEqual([v["found"] for v in violations], ["6.17.4-igos-8"])

    def test_any_recipe_is_scanned_not_just_the_kernel(self):
        """A release-coupled path can be stated by any package that checks one."""
        self._write_recipe("desktop", "some-tool",
                           'name: some-tool\nversion: "1.0"\nrelease: 1\n'
                           'verify_paths:\n  - /usr/lib/modules/6.18.10-igos-3/x.ko\n')

        _expected, violations = _mod.scan(self.root)

        self.assertEqual([v["recipe"] for v in violations],
                         ["packages/desktop/some-tool/package.yml"])

    # ---- what must NOT fire ------------------------------------------------

    def test_matching_paths_pass(self):
        self._write_recipe("core", "linux-kernel-pass2",
                           'name: linux-kernel-pass2\nversion: "6.18.10"\n'
                           'release: 5\nverify_paths:\n'
                           '  - /usr/include/linux/limits.h\n'
                           '  - /boot/vmlinuz-6.18.10-igos-8\n'
                           '  - /usr/lib/modules/6.18.10-igos-8\n')
        self.assertEqual(_mod.scan(self.root)[1], [])

    def test_release_line_changelog_is_not_a_claim(self):
        """The release: comment cites historical kernel names on purpose."""
        self._write_recipe("core", "linux-kernel-pass2",
                           'name: linux-kernel-pass2\nversion: "6.18.10"\n'
                           'release: 5  # r4: lockstep bump, was 6.18.10-igos-6\n'
                           'verify_paths:\n  - /boot/vmlinuz-6.18.10-igos-8\n')
        self.assertEqual(_mod.scan(self.root)[1], [],
                         "a changelog records what WAS; it is not a claim about "
                         "what this build produces")

    def test_comment_lines_are_not_claims(self):
        self._write_recipe("core", "demo",
                           'name: demo\nversion: "1.0"\nrelease: 1\n'
                           '# historical: shipped as 6.18.10-igos-4\n')
        self.assertEqual(_mod.scan(self.root)[1], [])

    # ---- the gate's own failure modes -------------------------------------

    def test_release_bump_moves_the_expected_value(self):
        self._write_kernel(version="6.18.10", release=9)
        self._write_recipe("core", "linux-kernel-pass2",
                           'name: linux-kernel-pass2\nversion: "6.18.10"\n'
                           'release: 5\nverify_paths:\n'
                           '  - /boot/vmlinuz-6.18.10-igos-8\n')
        expected, violations = _mod.scan(self.root)
        self.assertEqual(expected, "6.18.10-igos-9")
        self.assertEqual(len(violations), 1,
                         "yesterday's correct value is today's drift")

    def test_unparseable_release_is_an_error_not_a_guess(self):
        self._write_kernel(version="6.18.10", release="")
        with self.assertRaises(ValueError):
            _mod.scan(self.root)

    def test_missing_kernel_recipe_is_an_error(self):
        (self.root / "packages" / "core" / "linux-kernel"
         / "package.yml").unlink()
        with self.assertRaises(FileNotFoundError):
            _mod.scan(self.root)

    def test_setup_error_exits_2_not_0(self):
        (self.root / "packages" / "core" / "linux-kernel"
         / "package.yml").unlink()
        self.assertEqual(_mod.main(["--repo-root", str(self.root)]), 2,
                         "a gate that cannot derive its reference must never "
                         "report clean")

    def test_exit_codes(self):
        self._write_recipe("core", "bad",
                           'name: bad\nversion: "1.0"\nrelease: 1\n'
                           'verify_paths:\n  - /boot/vmlinuz-6.18.10-igos-2\n')
        self.assertEqual(_mod.main(["--repo-root", str(self.root)]), 1)

        (self.root / "packages" / "core" / "bad" / "package.yml").write_text(
            'name: bad\nversion: "1.0"\nrelease: 1\n')
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
