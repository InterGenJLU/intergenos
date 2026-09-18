"""Tracked deployment refuses to run outside the build chroot.

The builder fixes its tracking paths at absolute locations
(/var/lib/igos/packages, /var/lib/igos/archives, /tmp/igos-staging) and makes
tracked deployment the default for --build. Inside the build chroot those are
the chroot's own directories, which is the design. Typed on a live installed
machine the same command form points at the RUNNING system's package database
while the files deploy into build/system: the completion-marker step unlinks
the installed record the package manager wrote, and a successful tracked build
overwrites that record with a manifest for files that are not installed. The
only thing that stopped it on the machine where it was measured was that the
run was unprivileged.

scripts/chroot-enter.sh is the single entry point into the chroot and now
exports IGOS_BUILD_IN_CHROOT=1 in its env -i list. igos-build refuses a
tracked --build when that variable is not exactly "1".

The "proceeds" legs use a recipe name that does not exist. A --build run is a
real build even with --dry-run (the dry-run block prints the phases and the
build still executes below it), so the test cannot let one start. A name with
no recipe exits 1 at the --only filter, which is past the gate and before any
build work — that exit is the proof the gate let the run through.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRY = REPO_ROOT / "igos-build.py"
NO_SUCH_RECIPE = "zz-no-such-recipe-for-this-test"
STAGING = Path("/tmp/igos-staging")


def run_builder(args, in_chroot):
    env = dict(os.environ)
    env.pop("IGOS_BUILD_IN_CHROOT", None)
    if in_chroot is not None:
        env["IGOS_BUILD_IN_CHROOT"] = in_chroot
    return subprocess.run(
        [sys.executable, str(ENTRY)] + args,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )


class TrackedModeRefusesOutsideChroot(unittest.TestCase):
    def test_build_without_the_variable_exits_2_and_names_the_live_paths(self):
        staging_existed = STAGING.exists()
        proc = run_builder(["--build", "--only", NO_SUCH_RECIPE], in_chroot=None)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 2, out)
        self.assertIn("/var/lib/igos/packages", out)
        self.assertIn("/var/lib/igos/archives", out)
        self.assertIn("/tmp/igos-staging", out)
        self.assertIn("scripts/chroot-enter.sh", out)
        self.assertIn("--stage-only", out)
        # Nothing ran: the gate is ahead of the template scan, so the run does
        # not even print the builder's banner.
        self.assertNotIn("Scanning:", out)
        self.assertNotIn("Executing build", out)
        if not staging_existed:
            self.assertFalse(STAGING.exists(), "the refused run created /tmp/igos-staging")

    def test_build_with_the_variable_set_proceeds_past_the_gate(self):
        proc = run_builder(["--build", "--only", NO_SUCH_RECIPE], in_chroot="1")
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 1, out)
        self.assertIn(f"no package named '{NO_SUCH_RECIPE}'", out)
        self.assertNotIn("tracked deployment", out)

    def test_stage_only_is_unaffected_by_the_gate(self):
        proc = run_builder(
            ["--build", "--stage-only", "--only", NO_SUCH_RECIPE], in_chroot=None
        )
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 1, out)
        self.assertIn(f"no package named '{NO_SUCH_RECIPE}'", out)

    def test_dry_run_without_build_is_unaffected_by_the_gate(self):
        proc = run_builder(["--dry-run", "--only", NO_SUCH_RECIPE], in_chroot=None)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 1, out)
        self.assertIn(f"no package named '{NO_SUCH_RECIPE}'", out)

    def test_a_value_other_than_1_does_not_open_the_gate(self):
        proc = run_builder(["--build", "--only", NO_SUCH_RECIPE], in_chroot="yes")
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 2, out)
        self.assertIn("/var/lib/igos/packages", out)

    def test_chroot_enter_exports_the_variable_in_its_env_list(self):
        text = (REPO_ROOT / "scripts" / "chroot-enter.sh").read_text()
        self.assertIn("IGOS_BUILD_IN_CHROOT=1", text)


if __name__ == "__main__":
    unittest.main()
