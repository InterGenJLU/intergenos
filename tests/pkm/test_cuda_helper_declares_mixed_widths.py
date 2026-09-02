#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The CUDA toolkit helper declares that the toolkit carries both ELF widths.

Measured on the reference laptop on 2026-09-02, the first time the helper ran
to completion on real hardware: NVIDIA's 13.3.1 toolkit, laid into /opt/cuda,
holds 506 ELF objects of which 11 are 32-bit (six under compute-sanitizer/x86,
five under target/linux-desktop-glibc_2_11_3-x86). The footprint recorder's
default contract is "every deposited binary is 64-bit"; it refused the first
32-bit object and the helper aborted with 8,973 files on disk, none tracked —
`pkm files`, `pkm verify` and `pkm remove` blind to a seven-gigabyte install.

The helper now declares the mixed width before recording, which the shared
helper library accepts and writes into the package's manifest. The
declaration is asserted here in the source; the recorder's handling of a
"mixed" declaration is proven in tests/pkm/test_helper_manifest_elf.py and
by running the real library below with a 32-bit and a 64-bit object.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HELPER = REPO / "packages" / "compute" / "cuda-toolkit" / "helper" / "igos-install-cuda-toolkit"
HELPER_LIB = REPO / "packages" / "core" / "intergenos-helper-lib" / "helper-lib.sh"


class TheDeclarationIsInTheHelper(unittest.TestCase):

    def test_mixed_is_declared_before_the_files_are_recorded(self):
        src = HELPER.read_text()
        self.assertIn("export IGOS_HELPER_ELF_CLASS=mixed", src)
        self.assertLess(src.index("export IGOS_HELPER_ELF_CLASS=mixed"),
                        src.index("igos_helper_record_file"),
                        "the width is declared after recording has begun; "
                        "the library refuses a declaration that changes "
                        "mid-run")


class TheLibraryRecordsBothWidthsUnderMixed(unittest.TestCase):
    """The real helper library, fed a 32-bit and a 64-bit ELF header, under
    the default contract and under the declaration the helper now makes."""

    def _record(self, declare_mixed):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            elf32 = td / "lib32.so"
            elf64 = td / "lib64.so"
            elf32.write_bytes(b"\x7fELF\x01" + b"\x00" * 60)
            elf64.write_bytes(b"\x7fELF\x02" + b"\x00" * 60)
            script = (
                f"source {HELPER_LIB}\n"
                f"export IGOS_HELPER_MANIFEST_DIR={td}/m\n"
                f"mkdir -p {td}/m\n"
                + ("export IGOS_HELPER_ELF_CLASS=mixed\n" if declare_mixed else "")
                + "igos_helper_init cudatest || exit 90\n"
                f"igos_helper_record_file {elf64} || exit 91\n"
                f"igos_helper_record_file {elf32} || exit 92\n"
                "echo RECORDED_BOTH\n"
                "IGOS_HELPER_COMMITTED=1\n"
            )
            r = subprocess.run(["bash", "-c", script], capture_output=True,
                               text=True, timeout=60)
            return r

    def test_the_default_contract_refuses_the_32_bit_object(self):
        """The laptop's failure, reproduced against the real library."""
        r = self._record(declare_mixed=False)
        self.assertEqual(r.returncode, 92, r.stdout + r.stderr)
        self.assertIn("32-bit ELF", r.stdout + r.stderr)

    def test_the_mixed_declaration_records_both(self):
        r = self._record(declare_mixed=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("RECORDED_BOTH", r.stdout)


if __name__ == "__main__":
    unittest.main()
