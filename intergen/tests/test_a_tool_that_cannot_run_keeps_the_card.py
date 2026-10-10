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
its memory unknown, and the log names the error. So does a tool that runs and
exits with an error, or exits cleanly without printing a size: the log names
its exit status and the first line it wrote, from its error stream or, when
that is empty, from its output (nvidia-smi writes its own failures there). A
machine with no nvidia-smi at all reads exactly as before and logs nothing
about it, and a tool that prints the size is read as before.

Hermetic: the card listing is a built lookalike of /sys/class/drm with one
NVIDIA card that exports no memory figure, the driver's memory files are
absent, and nvidia-smi is a stand-in that raises or returns a finished
process.
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

    def cards_and_memory_when_the_tool_finishes(self, status: int,
                                                output: str, errors: str):
        finished = subprocess.CompletedProcess(
            args=["nvidia-smi", "--query-gpu=memory.total",
                  "--format=csv,noheader,nounits"],
            returncode=status, stdout=output, stderr=errors)
        with mock.patch.object(hardware.subprocess, "run",
                               return_value=finished):
            detector = HardwareDetector()
            return detector._enumerate_gpus(), detector._nvidia_vram_mb()

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

    def test_a_tool_that_exits_with_an_error_keeps_the_card_and_says_why(self):
        with self.assertLogs("intergen.hardware", level="WARNING") as logs:
            cards, memory = self.cards_and_memory_when_the_tool_finishes(
                1, "", "permission denied\n")
        self.assert_the_card_is_kept(cards)
        self.assertIsNone(memory)
        self.assertTrue(any("status 1" in line and "permission denied" in line
                            and "kept" in line for line in logs.output),
                        logs.output)

    def test_a_failure_the_tool_writes_to_its_output_is_named(self):
        said = ("NVIDIA-SMI has failed because it couldn't communicate with "
                "the NVIDIA driver. Make sure that the latest NVIDIA driver "
                "is installed and running.\n")
        with self.assertLogs("intergen.hardware", level="WARNING") as logs:
            cards, memory = self.cards_and_memory_when_the_tool_finishes(
                9, said, "")
        self.assert_the_card_is_kept(cards)
        self.assertIsNone(memory)
        self.assertTrue(any("status 9" in line
                            and "couldn't communicate with the NVIDIA driver"
                            in line for line in logs.output), logs.output)

    def test_a_tool_that_prints_no_size_keeps_the_card_and_says_why(self):
        with self.assertLogs("intergen.hardware", level="WARNING") as logs:
            cards, memory = self.cards_and_memory_when_the_tool_finishes(
                0, "[N/A]\n", "")
        self.assert_the_card_is_kept(cards)
        self.assertIsNone(memory)
        self.assertTrue(any("[N/A]" in line and "kept" in line
                            for line in logs.output), logs.output)

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

    def test_a_tool_that_prints_the_size_is_read_as_before_and_says_nothing(self):
        logger = logging.getLogger("intergen.hardware")
        with mock.patch.object(logger, "warning") as warned:
            cards, memory = self.cards_and_memory_when_the_tool_finishes(
                0, "8192\n", "")
        nvidia = [c for c in cards if c[0] == "nvidia"]
        self.assertEqual(len(nvidia), 1, f"the NVIDIA card was dropped: {cards}")
        self.assertEqual(nvidia[0][2], 8192)
        self.assertEqual(memory, 8192)
        warned.assert_not_called()


if __name__ == "__main__":
    unittest.main()
