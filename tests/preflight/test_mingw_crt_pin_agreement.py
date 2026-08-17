# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The mingw-w64 CRT-default pin agreement is a CHECKED gate, not an
authoring guarantee (the mingw wave-boundary adversarial verify's low
observation, closed same cycle): upstream flipped the default CRT to
ucrt at v12, so mingw-w64-headers and mingw-w64-crt each pin
--with-default-msvcrt explicitly — and the two MUST agree. A one-sided
edit would drift them silently, and the RT-15 hello-world gate would
likely still link across a CRT-default mismatch. This battery test
fails the landing instead.
"""

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

PIN_RE = re.compile(r"--with-default-msvcrt=([A-Za-z0-9]+)")


def crt_default_pins(build_sh_text):
    """Extract the explicit CRT-default pin(s) from recipe text. Returns
    the sorted unique pinned values — a recipe pinning two different
    values in different stanzas is its own defect, and an EMPTY result
    means the recipe rides the upstream default (the unset trap)."""
    return sorted(set(PIN_RE.findall(build_sh_text)))


class TestMingwCrtPinAgreement(unittest.TestCase):
    HEADERS = REPO / "packages/extra/mingw-w64-headers/build.sh"
    CRT = REPO / "packages/extra/mingw-w64-crt/build.sh"

    def test_real_tree_pins_present_and_agree(self):
        h = crt_default_pins(self.HEADERS.read_text())
        c = crt_default_pins(self.CRT.read_text())
        # Exactly-one on each side: an empty set means the recipe rides
        # the (silently ucrt-flipped) upstream default and must FAIL
        # here — it can never read as trivially-equal.
        self.assertEqual(len(h), 1,
                         f"headers must carry exactly one explicit CRT pin, got {h}")
        self.assertEqual(len(c), 1,
                         f"crt must carry exactly one explicit CRT pin, got {c}")
        self.assertEqual(h, c,
                         f"CRT-default pin DRIFT: headers={h} crt={c} — the two "
                         f"recipes must always agree; a mismatch builds a "
                         f"headers/crt pair targeting different default CRTs "
                         f"and the RT-15 hello-world would still link")

    def test_missing_pin_yields_empty_never_equal(self):
        self.assertEqual(crt_default_pins("../configure --prefix=/usr\n"), [])

    def test_mismatch_detected(self):
        a = crt_default_pins("--with-default-msvcrt=msvcrt \\\n")
        b = crt_default_pins("--with-default-msvcrt=ucrt \\\n")
        self.assertEqual(a, ["msvcrt"])
        self.assertEqual(b, ["ucrt"])
        self.assertNotEqual(a, b)

    def test_double_pin_in_one_recipe_detected(self):
        two = crt_default_pins(
            "--with-default-msvcrt=msvcrt\n--with-default-msvcrt=ucrt\n")
        self.assertEqual(len(two), 2)


if __name__ == "__main__":
    unittest.main()
