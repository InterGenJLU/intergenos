# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Run the ISO author's real volume-id default over the names a build is given."""

import importlib.util
import os
import re
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ISO9660_ID = re.compile(r"[A-Z0-9_]{1,32}")


def source():
    ref = os.environ.get("IGOS_TEST_SCRIPT_REF")
    if ref:
        return subprocess.run(
            ["/usr/bin/git", "-C", str(ROOT), "show", f"{ref}:scripts/build-iso.sh"],
            check=True, capture_output=True, text=True,
        ).stdout
    return (ROOT / "scripts/build-iso.sh").read_text()


def default_block():
    text = source()
    begin = text.index('if [ -z "${VOLID:-}" ]; then\n')
    end = text.index("\nfi\n", begin) + len("\nfi\n")
    return text[begin:end]


def release_identity_gate():
    spec = importlib.util.spec_from_file_location(
        "check_release_identity", ROOT / "scripts/check-release-identity.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class IsoVolumeIdDefault(unittest.TestCase):
    def derive(self, output, epoch="1792108800", volid=None):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "volid.sh"
            setup = "set -euo pipefail\n"
            setup += f"OUTPUT={shlex.quote(output)}\nSOURCE_DATE_EPOCH={shlex.quote(epoch)}\n"
            if volid is not None:
                setup += f"VOLID={shlex.quote(volid)}\n"
            script.write_text(setup + default_block() + 'printf "VOLID=%s\\n" "$VOLID"\n')
            env = {k: v for k, v in os.environ.items() if k != "VOLID"}
            result = subprocess.run(
                ["/usr/bin/bash", str(script)], cwd=tmp, env=env,
                capture_output=True, text=True, timeout=10,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1, result.stdout)
        self.assertTrue(lines[0].startswith("VOLID="), result.stdout)
        return lines[0][len("VOLID="):]

    def test_a_re_mint_carries_its_tag_and_no_date(self):
        self.assertEqual(self.derive("/mnt/intergenos/build/intergenos-r001.3-02.iso"),
                         "IGOS_R0013_02")

    def test_a_first_mint_carries_the_release_alone(self):
        self.assertEqual(self.derive("build/intergenos-r001.3.iso"), "IGOS_R0013")

    def test_an_unsigned_test_medium_carries_the_same_tag(self):
        self.assertEqual(self.derive("build/intergenos-r001.3-02.unsigned-test.iso"),
                         "IGOS_R0013_02")

    def test_the_build_date_never_enters_the_id(self):
        ids = {self.derive("build/intergenos-r001.3-02.iso", epoch=epoch)
               for epoch in ("0", "1792108800", "1893456000")}
        self.assertEqual(ids, {"IGOS_R0013_02"})

    def test_the_id_keeps_the_iso9660_bound(self):
        got = self.derive("build/intergenos-r001.3-02-a-long-development-suffix.iso")
        self.assertEqual(got, "IGOS_R0013_02_A_LONG_DEVELOPMENT")
        self.assertEqual(len(got), 32)

    def test_a_volid_the_caller_sets_is_kept(self):
        self.assertEqual(self.derive("build/intergenos-r001.3-02.iso", volid="IGOS_CALLER_SET"),
                         "IGOS_CALLER_SET")

    def test_the_tag_is_the_one_the_release_identity_gate_derives(self):
        gate = release_identity_gate()
        derived, expected = {}, {}
        for name in ("intergenos-r001.3.iso", "intergenos-r001.3-02.iso", "intergenos-r001.3-12.iso"):
            tag = gate.iso_tag(name)
            expected[name] = ("IGOS_" + re.sub(r"[^A-Z0-9_]", "", tag.upper().replace("-", "_")))[:32]
            derived[name] = self.derive("build/" + name)
        self.assertEqual(derived, expected)
        for got in derived.values():
            self.assertIsNotNone(ISO9660_ID.fullmatch(got), got)


if __name__ == "__main__":
    unittest.main()
