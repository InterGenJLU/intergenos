# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Authoritative-denylist coverage for redirect-to-block-device wipes.

WC's safety review of 6c079275 flagged that the destructive-pattern set might
miss redirect-wipes to non-sd block devices. Verified against source: the
authoritative classify_command covered '> /dev/sd' and '> /dev/nvme' but NOT
'> /dev/vd*' (virtio — every VM, including our own test beds), '/dev/mmcblk*'
(SD/eMMC), '/dev/xvd*' (Xen), loop, or device-mapper. A redirect-wipe to a
virtio disk classified CONFIRM instead of BLOCKED — a real hole on the exact
disks our VMs use. These pins close it across the whole block-device family
while keeping char-device redirects (/dev/null, /dev/zero, /dev/tty, ...)
classifiable as normal.

WC's other two flags verified ALREADY-covered and pinned here as regression
guards: mkfs without a dot (\\bmkfs\\b) and long-form rm --recursive --force.
"""

from __future__ import annotations

import unittest

from intergen.safety import (
    SafetyTier,
    classify_command,
    is_destructive_execution,
)


class BlockDeviceRedirectBlocked(unittest.TestCase):
    """A shell redirect to ANY real block device is BLOCKED, not merely CONFIRM."""

    def test_block_device_families_are_blocked(self):
        for cmd in (
            "cat /dev/zero > /dev/sda",        # SCSI/SATA
            "cat /dev/zero > /dev/nvme0n1",    # NVMe
            "cat /dev/zero > /dev/vda",        # virtio — the gap code review surfaced
            "cat /dev/zero > /dev/mmcblk0",    # SD/eMMC
            "cat /dev/zero > /dev/xvda",       # Xen
            "echo x > /dev/dm-0",              # device-mapper
            "cat /dev/zero > /dev/disk/by-id/wwn-0x5000",  # by-id symlink → disk
        ):
            self.assertEqual(classify_command(cmd), SafetyTier.BLOCKED, cmd)

    def test_char_devices_are_not_blocked_by_the_redirect_rule(self):
        # Legitimate redirects to character devices must still classify normally
        # (not BLOCKED by the block-device rule).
        for cmd in (
            "echo hi > /dev/null",
            "cat log > /dev/stdout",
            "echo x > /dev/tty",
        ):
            self.assertNotEqual(classify_command(cmd), SafetyTier.BLOCKED, cmd)


class WCAlreadyCoveredFlags(unittest.TestCase):
    """WC's other two flags — verified already covered; pinned as guards."""

    def test_mkfs_without_a_dot_is_blocked(self):
        self.assertEqual(classify_command("mkfs -t ext4 /dev/sda"), SafetyTier.BLOCKED)
        self.assertEqual(classify_command("mkfs.ext4 /dev/sda1"), SafetyTier.BLOCKED)

    def test_long_form_recursive_force_rm_is_blocked(self):
        self.assertEqual(
            classify_command("rm --recursive --force /home/user/project"),
            SafetyTier.BLOCKED,
        )


class DestructiveExecutionHelperParity(unittest.TestCase):
    """The shared helper the router pre-decline gate uses matches the same family
    (one source of truth — no drift between the dispatch blocker and the gate)."""

    def test_helper_flags_block_device_redirects(self):
        self.assertTrue(is_destructive_execution("cat /dev/zero > /dev/vda"))
        self.assertTrue(is_destructive_execution("Run dd if=/dev/zero of=/dev/sda"))
        self.assertTrue(is_destructive_execution("please mkfs.ext4 /dev/vda"))

    def test_helper_ignores_benign_text(self):
        self.assertFalse(is_destructive_execution("what's my disk usage?"))
        self.assertFalse(is_destructive_execution("echo hi > /dev/null"))
        # Informational mention of a destructive tool must still reach the tools.
        self.assertFalse(is_destructive_execution("what does mkfs do?"))


if __name__ == "__main__":
    unittest.main()
