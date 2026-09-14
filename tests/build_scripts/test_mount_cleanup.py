# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Exercise the real mount/cleanup block, optionally with private tmpfs mounts."""

import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MOUNTS = ("proc", "sys", "dev", "run", "dev/pts")


def mount_block():
    ref = os.environ.get("IGOS_TEST_SCRIPT_REF")
    if ref:
        text = subprocess.run(
            ["/usr/bin/git", "-C", str(ROOT), "show", f"{ref}:scripts/build-squashfs.sh"],
            check=True, capture_output=True, text=True,
        ).stdout
    else:
        text = (ROOT / "scripts/build-squashfs.sh").read_text()
    start = text.index('step_begin "[1/6]"')
    end = text.index('step_begin "[2/6]"', start)
    return text[start:end]


class MountCleanup(unittest.TestCase):
    def run_mounts(self, failure):
        real = os.environ.get("IGOS_TEST_REAL_MOUNTS") == "1"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chroot = root / "chroot"
            (chroot / "dev/pts").mkdir(parents=True)
            state = root / "mounted"
            state.mkdir()
            setup = "set -euo pipefail\n"
            setup += f"CHROOT={shlex.quote(str(chroot))}\n"
            setup += f"STATE={shlex.quote(str(state))}\nFAIL={shlex.quote(failure)}\n"
            setup += "REAL=" + ("1" if real else "0") + "\n"
            setup += r'''
log() { printf '%s\n' "$*"; }
detail() { printf '%s\n' "$*"; }
warn() { printf '%s\n' "$*" >&2; }
step_begin() { :; }
status_line() { :; }
mountpoint() {
    local mnt="${@: -1}"
    if [ "$REAL" = 1 ]; then
        /usr/bin/mountpoint "$@"
    else
        [ -e "$STATE/${mnt#"$CHROOT"/}" ]
    fi
}
mount() {
    local arg mnt=""
    for arg in "$@"; do
        case "$arg" in "$CHROOT"/*) mnt="$arg" ;; esac
    done
    local name="${mnt#"$CHROOT"/}"
    if [ "$name" = "$FAIL" ]; then
        printf 'injected mount failure: %s\n' "$name" >&2
        return 47
    fi
    if [ "$REAL" = 1 ]; then
        /usr/bin/mount -t tmpfs tmpfs "$mnt"
        if [ "$name" = dev ]; then mkdir -p "$CHROOT/dev/pts"; fi
    else
        mkdir -p "$STATE/$(dirname "$name")"
        : > "$STATE/$name"
        if [ "$name" = dev ]; then
            rm "$STATE/dev"
            mkdir "$STATE/dev"
        fi
    fi
    printf 'MOUNTED:%s\n' "$name"
}
umount() {
    local mnt="${@: -1}" name
    name="${mnt#"$CHROOT"/}"
    printf 'UNMOUNT:%s\n' "$name"
    if [ "$REAL" = 1 ]; then
        /usr/bin/umount "$@"
    else
        if [ -d "$STATE/$name" ]; then rmdir "$STATE/$name"; else rm "$STATE/$name"; fi
    fi
}
'''
            script = root / "mounts.sh"
            script.write_text(setup + mount_block() + "\nprintf 'CONTINUED\\n'\n")
            if real:
                observer = root / "observer.sh"
                observer.write_text(
                    'set +e\n/usr/bin/bash "$1"\nrc=$?\n'
                    'for name in proc sys dev run dev/pts; do\n'
                    '  if /usr/bin/mountpoint -q "$2/$name"; then printf "LEFT:%s\\n" "$name"; fi\n'
                    'done\nexit "$rc"\n')
                argv = ["/usr/bin/unshare", "--user", "--map-root-user", "--mount",
                        "--propagation", "private", "/usr/bin/bash", str(observer),
                        str(script), str(chroot)]
            else:
                argv = ["/usr/bin/bash", str(script)]
            result = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=15)
            remaining = [str(p.relative_to(state)) for p in state.rglob("*")]
            return result, remaining

    def test_each_mount_failure_cleans_earlier_mounts(self):
        for name in MOUNTS:
            with self.subTest(failed_mount=name):
                result, remaining = self.run_mounts(name)
                self.assertEqual(result.returncode, 47, result.stdout + result.stderr)
                self.assertIn("injected mount failure: " + name, result.stderr)
                self.assertIn("cleanup: unmounting", result.stdout)
                self.assertNotIn("CONTINUED", result.stdout)
                self.assertNotIn("LEFT:", result.stdout)
                self.assertEqual(remaining, [])

    def test_success_unmounts_in_reverse_order(self):
        result, remaining = self.run_mounts("")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("CONTINUED", result.stdout)
        self.assertEqual([line for line in result.stdout.splitlines() if line.startswith("UNMOUNT:")],
                         ["UNMOUNT:" + name for name in reversed(MOUNTS)])
        self.assertNotIn("LEFT:", result.stdout)
        self.assertEqual(remaining, [])


if __name__ == "__main__":
    unittest.main()
