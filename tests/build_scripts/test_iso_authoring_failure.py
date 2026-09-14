# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Run the ISO author's real command block over small disposable inputs."""

import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def source():
    ref = os.environ.get("IGOS_TEST_SCRIPT_REF")
    if ref:
        return subprocess.run(
            ["/usr/bin/git", "-C", str(ROOT), "show", f"{ref}:scripts/build-iso.sh"],
            check=True, capture_output=True, text=True,
        ).stdout
    return (ROOT / "scripts/build-iso.sh").read_text()


class IsoAuthoringFailure(unittest.TestCase):
    def run_author(self, mode, existing=True):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            iso_root = root / "iso-root"
            iso_root.mkdir()
            (iso_root / "hello").write_bytes(b"ISO payload\n")
            esp = root / "efi.img"
            esp.write_bytes(bytes(1024 * 1024))
            output = root / "image.iso"
            if existing:
                output.write_bytes(b"existing artifact\n")
            text = source()
            staging = text[text.index('STAGING=$(mktemp'):text.index('ESP_TREE=')]
            log_start = text.index('# Tee subsequent stdout+stderr')
            log_end = text.index('# Source the forensic-trace bash companion', log_start)
            logging = text[log_start:log_end]
            begin = text.index('echo "[build-iso] [4/6] running xorriso"')
            end = text.index('# Step 5: self-verify', begin)
            setup = "set -euo pipefail\n"
            for key, value in {"OUTPUT": output, "ISO_ROOT": iso_root, "ESP_IMG": esp,
                               "TMPDIR": root, "VOLID": "TEST_MEDIA", "MODE": mode,
                               "LOG_FILE": root / "build.log"}.items():
                setup += f"export {key}={shlex.quote(str(value))}\n"
            setup += r'''
IGOS_TRACE_LIB_LOADED=1
trace_event() { printf 'TRACE:%s\n' "$*"; }
build_failure_emit() { printf 'FAILURE:%s\n' "$*"; }
xorriso() {
    local previous="" target="" arg rc=0
    for arg in "$@"; do
        if [ "$previous" = -output ]; then target="$arg"; fi
        previous="$arg"
    done
    case "$MODE" in
        fake-failure)
            printf 'partial artifact\n' > "$target"
            printf 'authoring failed\n' >&2
            return 52 ;;
        no-output) return 0 ;;
        real-failure)
            (ulimit -f 128; /usr/bin/xorriso "$@") || rc=$?
            if [ -f "$target" ]; then printf 'PARTIAL_BYTES:%s\n' "$(stat -c%s "$target")"; fi
            return "$rc" ;;
        success) /usr/bin/xorriso "$@" ;;
    esac
}
'''
            script = root / "author.sh"
            script.write_text(setup + logging + staging + text[begin:end] + "printf 'CONTINUED\\n'\n")
            result = subprocess.run(
                ["/usr/bin/bash", str(script)], cwd=root,
                capture_output=True, text=True, timeout=20,
            )
            data = output.read_bytes() if output.is_file() else None
            leftovers = sorted(p.name for p in root.glob("image.iso.partial.*"))
            extracted = None
            if mode == "success" and result.returncode == 0:
                destination = root / "extracted"
                readback = subprocess.run(
                    ["/usr/bin/xorriso", "-osirrox", "on", "-indev", str(output),
                     "-extract", "/hello", str(destination)],
                    capture_output=True, text=True, timeout=20,
                )
                self.assertEqual(readback.returncode, 0, readback.stdout + readback.stderr)
                extracted = destination.read_bytes()
            return result, data, leftovers, extracted

    def test_failure_records_status_and_preserves_existing_artifact(self):
        result, data, leftovers, _ = self.run_author("fake-failure")
        self.assertEqual(result.returncode, 52, result.stdout + result.stderr)
        self.assertIn("TRACE:xorriso_done", result.stdout)
        self.assertIn("rc::=52", result.stdout)
        self.assertIn("authoring failed", result.stdout + result.stderr)
        self.assertNotIn("CONTINUED", result.stdout)
        self.assertEqual(data, b"existing artifact\n")
        self.assertEqual(leftovers, [])

    def test_failure_removes_only_its_new_partial_artifact(self):
        result, data, leftovers, _ = self.run_author("fake-failure", existing=False)
        self.assertEqual(result.returncode, 52, result.stdout + result.stderr)
        self.assertIsNone(data)
        self.assertEqual(leftovers, [])

    def test_real_authoring_write_failure_is_recorded(self):
        result, data, leftovers, _ = self.run_author("real-failure")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PARTIAL_BYTES:", result.stdout)
        self.assertIn("TRACE:xorriso_done", result.stdout)
        self.assertNotIn("CONTINUED", result.stdout)
        self.assertEqual(data, b"existing artifact\n")
        self.assertEqual(leftovers, [])

    def test_missing_new_output_cannot_reuse_existing_artifact(self):
        result, data, leftovers, _ = self.run_author("no-output")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("CONTINUED", result.stdout)
        self.assertEqual(data, b"existing artifact\n")
        self.assertEqual(leftovers, [])

    def test_real_authoring_success_can_be_read_back(self):
        result, data, leftovers, extracted = self.run_author("success")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("TRACE:xorriso_done", result.stdout)
        self.assertIn("CONTINUED", result.stdout)
        self.assertNotEqual(data, b"existing artifact\n")
        self.assertEqual(leftovers, [])
        self.assertEqual(extracted, b"ISO payload\n")


if __name__ == "__main__":
    unittest.main()
