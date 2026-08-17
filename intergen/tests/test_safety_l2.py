# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""L2 anti-fabrication gate: wrong-package-manager HARD block.

This system's package manager is pkm. apt/dnf/yum/pacman/zypper/etc. do not
exist on InterGenOS, so emitting one is a wrong-system hallucination. The
dispatch-layer classifier HARD-blocks it and redirects the model to the pkm
equivalent. (The complementary is_installed SOFT re-ground is realized by L1
grounding — reference.py surfaces already-installed tools in query lookups.)
"""

from __future__ import annotations

import unittest

from intergen.safety import classify_command, get_blocked_response
from intergen.interfaces.types import SafetyTier


class TestWrongPkgManagerBlock(unittest.TestCase):
    def test_apt_install_blocked(self):
        self.assertEqual(classify_command("apt install firefox"), SafetyTier.BLOCKED)

    def test_apt_list_blocked_even_readonly(self):
        # Even a read-only apt query is wrong-system → blocked before AUTO table.
        self.assertEqual(classify_command("apt list"), SafetyTier.BLOCKED)

    def test_dnf_yum_pacman_zypper_blocked(self):
        for c in ("dnf install vim", "yum search nginx",
                  "pacman -S htop", "zypper install git"):
            self.assertEqual(classify_command(c), SafetyTier.BLOCKED, c)

    def test_pkm_not_blocked(self):
        # pkm is the correct manager — must NOT be caught by the gate.
        self.assertNotEqual(classify_command("pkm install firefox"), SafetyTier.BLOCKED)
        self.assertNotEqual(classify_command("pkm search audio"), SafetyTier.BLOCKED)

    def test_benign_unaffected(self):
        self.assertEqual(classify_command("ls -la"), SafetyTier.AUTO)


class TestPkmRedirectMessage(unittest.TestCase):
    def test_install_redirect_echoes_package(self):
        msg = get_blocked_response("apt install firefox")
        self.assertIn("pkm install firefox", msg)

    def test_remove_redirect(self):
        self.assertIn("pkm remove vim", get_blocked_response("dnf remove vim"))

    def test_pacman_flag_redirect(self):
        self.assertIn("pkm install htop", get_blocked_response("pacman -S htop"))

    def test_search_redirect(self):
        self.assertIn("pkm search nginx", get_blocked_response("yum search nginx"))

    def test_update_maps_to_sync(self):
        self.assertIn("pkm sync", get_blocked_response("apt update"))

    def test_unparseable_falls_back_to_generic(self):
        msg = get_blocked_response("apt")
        self.assertIn("pkm", msg)


if __name__ == "__main__":
    unittest.main()
