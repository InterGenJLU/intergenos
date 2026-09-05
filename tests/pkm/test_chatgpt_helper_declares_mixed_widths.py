#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The ChatGPT desktop app helper declares that its payload carries more than
one ELF width.

Measured on 2026-09-05, the first time the helper ran to completion on real
hardware: the app's bundled node tree holds prebuilt native modules for other
architectures beside the x86-64 ones (an Android ARM build of a database
module was the first the recorder met). The footprint recorder's default
contract is "every deposited binary is 64-bit"; it refused that file and the
helper aborted with 3,167 files on disk, none tracked.

The helper now declares the mixed width before recording. The declaration is
asserted here in the source; the recorder's handling of a "mixed" declaration
is proven in tests/pkm/test_helper_manifest_elf.py and in
tests/pkm/test_cuda_helper_declares_mixed_widths.py against the real library.
"""

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RECIPE = REPO / "packages" / "extra" / "chatgpt" / "build.sh"


class TheDeclarationIsInTheHelper(unittest.TestCase):

    def test_mixed_is_declared_before_the_files_are_recorded(self):
        src = RECIPE.read_text()
        self.assertIn("export IGOS_HELPER_ELF_CLASS=mixed", src)
        self.assertLess(src.index("export IGOS_HELPER_ELF_CLASS=mixed"),
                        src.index("igos_helper_record_file"),
                        "the width is declared after recording has begun; "
                        "the library refuses a declaration that changes "
                        "mid-run")


if __name__ == "__main__":
    unittest.main()
