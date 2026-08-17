# SPDX-License-Identifier: GPL-3.0-or-later
"""Virtual-GPU vendor labelling — a VM readout should name the GPU (vmware /
virtio / qemu) rather than "unknown (0x15ad)", WITHOUT changing tier/model
selection (a virtual GPU is never discrete-capable). Closes the VM-run cosmetic
LOW (the tier readout showed GPU "unknown" for VMware vendor 0x15ad)."""
from __future__ import annotations

import unittest

from intergen.hardware import GPU_VENDORS, HardwareDetector


class VirtualGpuLabelTests(unittest.TestCase):
    def test_common_virtual_gpus_are_named(self):
        self.assertEqual(GPU_VENDORS.get("0x15ad"), "vmware")   # VMware / VirtualBox
        self.assertEqual(GPU_VENDORS.get("0x1af4"), "virtio")   # virtio-gpu (QEMU/KVM)
        self.assertEqual(GPU_VENDORS.get("0x1234"), "qemu")     # QEMU stdvga
        # The real-hardware vendors are untouched.
        self.assertEqual(GPU_VENDORS.get("0x10de"), "nvidia")
        self.assertEqual(GPU_VENDORS.get("0x1002"), "amd")
        self.assertEqual(GPU_VENDORS.get("0x8086"), "intel")

    def test_virtual_gpus_never_discrete_capable(self):
        # The invariant: labelling a virtual GPU must NOT make it discrete-capable,
        # so tier/model selection is unchanged — even if it somehow reported VRAM.
        d = HardwareDetector()
        for vendor in ("vmware", "virtio", "qemu"):
            self.assertFalse(d._is_discrete_capable(vendor, None), vendor)
            self.assertFalse(d._is_discrete_capable(vendor, 8192), vendor)

    def test_unknown_vendor_still_falls_back_gracefully(self):
        # An id we still don't recognise must not raise and must not be discrete.
        d = HardwareDetector()
        self.assertIsNone(GPU_VENDORS.get("0xbeef"))
        self.assertFalse(d._is_discrete_capable("unknown (0xbeef)", 8192))


if __name__ == "__main__":
    unittest.main()
