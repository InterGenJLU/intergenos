# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A memory tool that exists but cannot run never changes which card the
machine has.

THE DEFECT THIS PINS. The NVIDIA card's memory is read from the driver's own
files and then from nvidia-smi. The reader caught a missing tool, a timeout
and an unreadable number, but not a tool that exists and cannot be run, so a
refused permission (measured: nvidia-smi made inaccessible in the daemon's
unit) raised into the loop over the machine's cards, which dropped the WHOLE
card. The machine then read as having only its integrated card, the CUDA
rung was never asked, and the plan put every layer on a card it could not
read.

THE RULE THESE CELLS HOLD. A tool that cannot be run or read, for any system
error or a failed subprocess, leaves the card in the machine's reading with
its memory unknown, and the log names the error. A machine with no nvidia-smi
at all reads exactly as before and logs nothing about it.

Hermetic: the card listing is a built lookalike of /sys/class/drm with one
NVIDIA card that exports no memory figure, the driver's memory files are
absent, and nvidia-smi is a stand-in that raises.
"""
from __future__ import annotations

import errno
import logging
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from intergen import hardware
from intergen.hardware import HardwareDetector

NVIDIA = "0x10de"
INTEGRATED = "0x8086"


def _card(root: Path, name: str, vendor: str, device: str) -> None:
    device_dir = root / name / "device"
    device_dir.mkdir(parents=True)
    (device_dir / "vendor").write_text(vendor + "\n")
    (device_dir / "device").write_text(device + "\n")


class _Machine(unittest.TestCase):
    """An integrated card and an NVIDIA card whose memory only nvidia-smi
    could say."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.drm = root / "drm"
        _card(self.drm, "card0", INTEGRATED, "0x9a49")
        _card(self.drm, "card1", NVIDIA, "0x1f99")
        self.no_driver_files = root / "nvidia-gpus"
        self.no_driver_files.mkdir()
        real_path = Path

        def path(where, *more):
            if str(where) == "/sys/class/drm" and not more:
                return real_path(self.drm)
            if str(where) == "/proc/driver/nvidia/gpus" and not more:
                return real_path(self.no_driver_files)
            return real_path(where, *more)

        patcher = mock.patch.object(hardware, "Path", path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def cards_when_the_tool(self, raises: BaseException):
        with mock.patch.object(hardware.subprocess, "run",
                               side_effect=raises):
            return HardwareDetector()._enumerate_gpus()

    def assert_the_card_is_kept(self, cards) -> None:
        nvidia = [c for c in cards if c[0] == "nvidia"]
        self.assertEqual(len(nvidia), 1, f"the NVIDIA card was dropped: {cards}")
        self.assertIsNone(nvidia[0][2], "a memory figure appeared from nowhere")


class AToolThatCannotRunKeepsTheCard(_Machine):

    def test_a_tool_present_but_not_executable_keeps_the_card(self):
        refused = PermissionError(errno.EACCES, "Permission denied",
                                  "nvidia-smi")
        with self.assertLogs("intergen.hardware", level="WARNING") as logs:
            cards = self.cards_when_the_tool(refused)
        self.assert_the_card_is_kept(cards)
        self.assertTrue(any("Permission denied" in line and "kept" in line
                            for line in logs.output), logs.output)

    def test_a_tool_raising_another_system_error_keeps_the_card(self):
        broken = OSError(errno.EIO, "Input/output error", "nvidia-smi")
        with self.assertLogs("intergen.hardware", level="WARNING") as logs:
            cards = self.cards_when_the_tool(broken)
        self.assert_the_card_is_kept(cards)
        self.assertTrue(any("Input/output error" in line for line in logs.output),
                        logs.output)

    def test_a_failed_subprocess_keeps_the_card(self):
        cards = self.cards_when_the_tool(
            subprocess.SubprocessError("the tool's process failed"))
        self.assert_the_card_is_kept(cards)

    def test_the_machine_still_selects_the_nvidia_card(self):
        refused = PermissionError(errno.EACCES, "Permission denied",
                                  "nvidia-smi")
        with mock.patch.object(hardware.subprocess, "run",
                               side_effect=refused), \
                mock.patch.object(HardwareDetector, "_try_lspci",
                                  return_value=None):
            vendor, _model, vram = HardwareDetector()._detect_gpu()
        self.assertEqual(vendor, "nvidia")
        self.assertIsNone(vram)


class TheControl(_Machine):

    def test_a_machine_with_no_tool_reads_as_before_and_says_nothing(self):
        absent = FileNotFoundError(errno.ENOENT, "No such file or directory",
                                   "nvidia-smi")
        logger = logging.getLogger("intergen.hardware")
        with mock.patch.object(logger, "warning") as warned:
            cards = self.cards_when_the_tool(absent)
        self.assert_the_card_is_kept(cards)
        warned.assert_not_called()


if __name__ == "__main__":
    unittest.main()
