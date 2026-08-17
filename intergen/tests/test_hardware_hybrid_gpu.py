# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""PI-Z13 regression — hybrid-graphics GPU naming coherence.

On a hybrid iGPU+dGPU laptop, lspci lists the integrated card first.
_try_lspci() used to return that first display line unconditionally, so the
detector reported the SELECTED discrete card's vendor with the integrated
card's model string (Zephyrus: gpu_vendor "nvidia" + gpu_model
"Intel … Iris Xe"). The lspci name must now match the selected vendor, or be
discarded in favor of the sysfs name.
"""
import subprocess
import unittest
from unittest.mock import patch

from intergen.hardware import HardwareDetector

_HYBRID_LSPCI = (
    "0000:00:02.0 VGA compatible controller: Intel Corporation Alder Lake-P "
    "GT2 [Iris Xe Graphics] (rev 0c)\n"
    "0000:01:00.0 3D controller: NVIDIA Corporation GA104M [GeForce RTX "
    "3070 Mobile / Max-Q] (rev a1)\n"
)


def _mock_lspci(*args, **kwargs):
    return subprocess.CompletedProcess(args=["lspci"], returncode=0,
                                       stdout=_HYBRID_LSPCI, stderr="")


_DUAL_RADEON_LSPCI_NN = (
    "03:00.0 VGA compatible controller [0300]: Advanced Micro Devices, Inc. "
    "[AMD/ATI] Navi 33 [Radeon RX 7600/7600 XT] [1002:7480] (rev cf)\n"
    "0a:00.0 VGA compatible controller [0300]: Advanced Micro Devices, Inc. "
    "[AMD/ATI] Navi 31 [Radeon RX 7900 XT/7900 XTX] [1002:744c] (rev c8)\n"
)


def _mock_lspci_dual_radeon(*args, **kwargs):
    return subprocess.CompletedProcess(args=["lspci", "-nn"], returncode=0,
                                       stdout=_DUAL_RADEON_LSPCI_NN, stderr="")


class DualSameVendorLspciNaming(unittest.TestCase):
    """Two cards, ONE vendor: only a device-id match labels the selected card
    correctly (a vendor match returns whichever AMD line lspci prints first —
    the mislabel found on the dual-Radeon dev PC, where the selected 20 GB
    card was reported with the 8 GB card's name)."""

    def setUp(self):
        self.det = HardwareDetector()

    def test_device_id_selects_the_exact_card(self):
        with patch("intergen.hardware.subprocess.run",
                   _mock_lspci_dual_radeon):
            name = self.det._try_lspci("amd", "0x744c")
        self.assertIsNotNone(name)
        self.assertIn("7900", name)
        self.assertNotIn("7600", name)
        # The PCI id tag is stripped from the human-readable label.
        self.assertNotIn("[1002:744c]", name)

    def test_other_card_selects_its_own_line(self):
        with patch("intergen.hardware.subprocess.run",
                   _mock_lspci_dual_radeon):
            name = self.det._try_lspci("amd", "0x7480")
        self.assertIn("7600", name)
        self.assertNotIn("7900", name)

    def test_vendor_fallback_without_device_id_still_works(self):
        with patch("intergen.hardware.subprocess.run",
                   _mock_lspci_dual_radeon):
            name = self.det._try_lspci("amd")
        self.assertIsNotNone(name)
        self.assertIn("Radeon", name)


class HybridLspciNaming(unittest.TestCase):
    def setUp(self):
        self.det = HardwareDetector()

    def test_selected_nvidia_gets_nvidia_name(self):
        with patch("intergen.hardware.subprocess.run", _mock_lspci):
            name = self.det._try_lspci("nvidia")
        self.assertIsNotNone(name)
        self.assertIn("NVIDIA", name)
        self.assertNotIn("Intel", name)

    def test_selected_intel_gets_intel_name(self):
        with patch("intergen.hardware.subprocess.run", _mock_lspci):
            name = self.det._try_lspci("intel")
        self.assertIsNotNone(name)
        self.assertIn("Intel", name)

    def test_unmatched_vendor_returns_none_not_wrong_card(self):
        with patch("intergen.hardware.subprocess.run", _mock_lspci):
            name = self.det._try_lspci("amd")
        self.assertIsNone(name, "a wrong-vendor lspci name must be discarded")

    def test_no_vendor_keeps_legacy_first_line(self):
        with patch("intergen.hardware.subprocess.run", _mock_lspci):
            name = self.det._try_lspci()
        self.assertIn("Intel", name)


if __name__ == "__main__":
    unittest.main()
