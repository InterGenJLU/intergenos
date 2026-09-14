# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Run the actual staging functions with rsync over disposable build layouts."""

import os
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIRECTORIES = ("scripts", "packages", "config", "installer", "docs", "assets",
               "igos-build", "pkm", "intergen", "docker/shim-build/sbat")


def function(name):
    ref = os.environ.get("IGOS_TEST_SCRIPT_REF")
    if ref:
        text = subprocess.run(
            ["/usr/bin/git", "-C", str(ROOT), "show", f"{ref}:scripts/build-intergenos.sh"],
            check=True, capture_output=True, text=True,
        ).stdout
    else:
        text = (ROOT / "scripts/build-intergenos.sh").read_text()
    match = re.search(rf"^{name}\(\) \{{\n.*?^\}}$", text, re.M | re.S)
    if not match:
        raise AssertionError(name)
    return match.group()


class RequiredSync(unittest.TestCase):
    def run_sync(self, name, missing=None, broken_destination=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            for directory in (*DIRECTORIES, "build/sources", "build/patches"):
                path = repo / directory
                path.mkdir(parents=True, exist_ok=True)
                (path / "current").write_text(directory)
            for filename in ("igos-build.py", "SOURCES.md"):
                (repo / filename).write_text(filename)
            # The source acquisition step is outside the transfer contract.
            # Its input is already staged; the fixture cannot use the network.
            (repo / "scripts/download-sources.py").write_text("pass\n")
            if missing:
                target = repo / missing
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            target = root / "chroot"
            target.mkdir()
            (target / "mnt/intergenos").mkdir(parents=True)
            if broken_destination:
                bad = target / "mnt/intergenos/config"
                bad.write_text("existing destination is a file\n")
            setup = "set -euo pipefail\n"
            for key, value in {
                "FIXTURE_REPO": repo, "IGOS": target, "SCRIPTS": repo / "scripts",
                "PACKAGES_DIR": repo / "packages", "SOURCES": repo / "build/sources",
                "PATCHES": repo / "build/patches", "BUILD_LOG": root / "build.log",
                "START_AT": "core",
            }.items():
                setup += f"{key}={shlex.quote(str(value))}\n"
            setup += r'''
log() { printf '%s\n' "$*"; }
mountpoint() { return 0; }
tiers_for_start_at() { printf '%s\n' --all; }
rsync() {
    local args=() arg
    for arg in "$@"; do
        case "$arg" in
            /mnt/intergenos/*) arg="$FIXTURE_REPO/${arg#/mnt/intergenos/}" ;;
        esac
        args+=("$arg")
    done
    /usr/bin/rsync "${args[@]}"
}
'''
            script = root / "sync.sh"
            script.write_text(setup + function(name) + f"\n{name}\nprintf 'CONTINUED\\n'\n")
            result = subprocess.run(
                ["/usr/bin/bash", str(script)], cwd=root,
                capture_output=True, text=True, timeout=15,
            )
            copied = sorted(str(p.relative_to(target)) for p in target.rglob("current"))
            return result, copied

    def test_required_tree_transfers_fail_loudly(self):
        for path in (*DIRECTORIES, "igos-build.py", "SOURCES.md"):
            with self.subTest(path=path):
                result, _ = self.run_sync("sync_chroot_scripts", missing=path)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("rsync:", result.stderr)
                self.assertNotIn("CONTINUED", result.stdout)

    def test_present_source_cannot_be_copied_to_invalid_destination(self):
        result, _ = self.run_sync("sync_chroot_scripts", broken_destination=True)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("rsync:", result.stderr)
        self.assertNotIn("CONTINUED", result.stdout)

    def test_source_and_patch_transfer_failures_stop_staging(self):
        for path in ("build/sources", "build/patches"):
            with self.subTest(path=path):
                result, _ = self.run_sync("ensure_sources_staged", missing=path)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("rsync:", result.stderr)
                self.assertNotIn("Sources staged:", result.stdout)
                self.assertNotIn("CONTINUED", result.stdout)

    def test_success_copies_real_files(self):
        for name in ("sync_chroot_scripts", "ensure_sources_staged"):
            with self.subTest(function=name):
                result, copied = self.run_sync(name)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("CONTINUED", result.stdout)
                self.assertTrue(copied)


if __name__ == "__main__":
    unittest.main()
