# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Run each changed relay with literal diagnostic bytes and producer status."""

import os
import re
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAYLOADS = ("last diagnostic", "normal\n", "", "\n", "  path\\name  ", "first\n\nlast")


def source(name):
    ref = os.environ.get("IGOS_TEST_SCRIPT_REF")
    if ref:
        return subprocess.run(
            ["/usr/bin/git", "-C", str(ROOT), "show", f"{ref}:scripts/{name}"],
            check=True, capture_output=True, text=True,
        ).stdout
    return (ROOT / "scripts" / name).read_text()


class DiagnosticRelay(unittest.TestCase):
    def run_relay(self, code, payload, producer_rc=0, tail=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "diagnostic.log"
            data.write_text(payload)
            script = root / "relay.sh"
            setup = "set +e\nset -o pipefail\npkg_log=" + shlex.quote(str(data)) + "\n"
            setup += 'log() { printf "%s\\n" "$*"; }\n'
            setup += '_sec_render() { :; }\n_ts() { printf stamp; }\n_SEC_CUR=fixture\n'
            if tail:
                script.write_text(setup + code + "\nexit $?\n")
            else:
                setup += 'emit() { /usr/bin/cat "$pkg_log"; return ' + str(producer_rc) + '; }\n'
                script.write_text(setup + code + "\nemit | logpipe\nexit $?\n")
            return subprocess.run(
                ["/usr/bin/bash", str(script)], cwd=root,
                capture_output=True, text=True, timeout=10,
            )

    def test_squashfs_relay_preserves_final_line_and_status(self):
        text = source("build-squashfs.sh")
        code = re.search(r"^logpipe\(\) \{\n.*?^\}$", text, re.M | re.S).group()
        for payload in PAYLOADS:
            for rc in (0, 23):
                with self.subTest(payload=payload, producer_rc=rc):
                    result = self.run_relay(code, payload, rc)
                    self.assertEqual(result.returncode, rc, result.stderr)
                    self.assertEqual(result.stdout,
                                     "".join("[stamp] " + line + "\n" for line in payload.splitlines()))

    def test_every_package_failure_tail_preserves_final_line(self):
        for filename in ("chroot-build-ch8.sh", "chroot-build-core-extra.sh"):
            lines = [(i, line.strip()) for i, line in enumerate(source(filename).splitlines(), 1)
                     if 'tail -20 "$pkg_log" | while IFS= read -r l' in line]
            self.assertEqual(len(lines), 4, filename)
            for line_number, code in lines:
                for payload in PAYLOADS:
                    with self.subTest(source=filename, line=line_number, payload=payload):
                        result = self.run_relay(code, payload, tail=True)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(result.stdout,
                                         "".join("    " + line + "\n" for line in payload.splitlines()))


if __name__ == "__main__":
    unittest.main()
