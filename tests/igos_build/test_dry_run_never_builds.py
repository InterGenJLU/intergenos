"""--dry-run previews and exits; it never builds.

The dry-run block printed the phases and the build block below it then ran
anyway, so `--build --dry-run` was a real build: a person adding --dry-run to a
build command to make it safe got the opposite of what the flag's name says.

The three legs here are the whole contract:
  * `--build --dry-run` prints the phase list and exits 0 without reaching the
    build block ("==> Executing build" never appears, and no BuildExecutor is
    constructed, so none of its directories are made);
  * `--dry-run` without `--build` is unchanged — it still ends with the
    "run with --build to execute" line;
  * the chroot gate stays ahead of the preview: outside the build chroot a
    tracked `--build --dry-run` still exits 2 with the refusal, because a
    preview must not suggest the command would run there.

The red control for the first leg uses --stage-only. At the base commit a
tracked `--build --dry-run` with the marker set is a REAL tracked build against
the running machine's own package database, which is exactly the hazard the
chroot gate added on 2026-09-17 closed; the --stage-only form reaches the same
build block and proves the same thing without touching those paths.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRY = REPO_ROOT / "igos-build.py"
REAL_RECIPE = "NetworkManager-openconnect"


def run_builder(args, in_chroot=None, sources_dir=None):
    env = dict(os.environ)
    env.pop("IGOS_BUILD_IN_CHROOT", None)
    if in_chroot is not None:
        env["IGOS_BUILD_IN_CHROOT"] = in_chroot
    argv = [sys.executable, str(ENTRY)] + args
    if sources_dir is not None:
        argv += ["--sources-dir", sources_dir]
    return subprocess.run(
        argv, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=900
    )


class DryRunNeverBuilds(unittest.TestCase):
    def test_tracked_build_dry_run_previews_and_exits(self):
        with tempfile.TemporaryDirectory() as empty_sources:
            proc = run_builder(
                ["--build", "--dry-run", "--only", REAL_RECIPE],
                in_chroot="1",
                sources_dir=empty_sources,
            )
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out[-2000:])
        self.assertIn("Build phases (dry run)", out)
        self.assertIn(REAL_RECIPE, out)
        self.assertNotIn("Executing build", out)
        self.assertNotIn("Build summary", out)

    def test_stage_only_build_dry_run_previews_and_exits(self):
        with tempfile.TemporaryDirectory() as empty_sources:
            proc = run_builder(
                ["--build", "--stage-only", "--dry-run", "--only", REAL_RECIPE],
                sources_dir=empty_sources,
            )
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out[-2000:])
        self.assertIn("Build phases (dry run)", out)
        self.assertNotIn("Executing build", out)

    def test_dry_run_without_build_is_unchanged(self):
        with tempfile.TemporaryDirectory() as empty_sources:
            proc = run_builder(
                ["--dry-run", "--only", REAL_RECIPE], sources_dir=empty_sources
            )
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out[-2000:])
        self.assertIn("Build phases (dry run)", out)
        self.assertIn("templates validated", out)
        self.assertNotIn("Executing build", out)

    def test_the_chroot_gate_still_refuses_a_tracked_dry_run_outside_the_chroot(self):
        with tempfile.TemporaryDirectory() as empty_sources:
            proc = run_builder(
                ["--build", "--dry-run", "--only", REAL_RECIPE],
                sources_dir=empty_sources,
            )
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 2, out[-2000:])
        self.assertIn("/var/lib/igos/packages", out)
        self.assertIn("--stage-only", out)
        self.assertNotIn("Build phases (dry run)", out)


if __name__ == "__main__":
    unittest.main()
