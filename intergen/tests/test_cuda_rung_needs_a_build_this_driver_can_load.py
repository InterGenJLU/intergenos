# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The CUDA rung is offered only when the installed build can load on this
driver.

The architecture gate (test_cuda_architecture_gate.py) asks whether the build
carries code for the card. That is necessary and not sufficient: a build whose
code for the card is PTX must have that PTX translated by the driver at load
time, and a driver translates PTX only from a toolkit no newer than the CUDA
release it advertises. A build that carries no target record at all cannot be
shown to carry compiled code, so its code may be PTX too. Compiled code for the
card's own capability needs no translation and runs on any driver of the same
major CUDA release; a build from a newer major release than the driver does not
run on it at all.

The two versions are read from the machine, never assumed: the driver's from
``nvidia-smi --version``, the build's from the stamp the CUDA compiler writes
into every image it produces ("Cuda compilation tools, release X.Y"). An
unreadable version on either side is not a refusal, and it is never silent
either: the choice writes which reading it could not make and that the
engine's own load decides, then keeps the rung. A build the driver can load
keeps its rung without a word.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from intergen import serving_device

STAMP = b"Cuda compilation tools, release 13.3, V13.3.73"

_ABSENT = object()


def _write(path: str, data: bytes, mode: int = 0o644) -> str:
    with open(path, "wb") as fh:
        fh.write(data)
    os.chmod(path, mode)
    return path


def _restore(name: str, original) -> None:
    """Put a module attribute back as the case found it, removing it when the
    tree under test did not have it, so no case meets another case's
    stand-in in place of the real function."""
    if original is _ABSENT:
        if hasattr(serving_device, name):
            delattr(serving_device, name)
    else:
        setattr(serving_device, name, original)


class _Machine(unittest.TestCase):
    """One NVIDIA card at compute capability 7.5 on the proprietary driver; the
    CUDA engine a stand-in file at the CUDA path, the Vulkan engine present."""

    CARD = {"0000:01:00.0": 75}

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cuda-loadable-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for name in ("cuda_is_usable_here", "detect_nvidia_compute_caps",
                     "cuda_build_gpu_targets", "cuda_driver_release",
                     "hip_is_supported_here"):
            self.addCleanup(_restore, name, getattr(serving_device, name, _ABSENT))
        self._orig_paths = dict(serving_device.ENGINE_SERVER_PATHS)
        self.addCleanup(serving_device.ENGINE_SERVER_PATHS.update, self._orig_paths)
        serving_device.hip_is_supported_here = lambda *_a, **_k: None
        serving_device.cuda_is_usable_here = lambda *_a, **_k: True
        serving_device.detect_nvidia_compute_caps = lambda *_a, **_k: dict(self.CARD)
        serving_device.cuda_driver_release = lambda *_a, **_k: (13, 0)
        self.set_targets(set())
        self.set_engine(b"\x7fELF" + b"\0" * 64 + STAMP + b"\0" * 64)
        vulkan = _write(os.path.join(self.tmp, "llama-server-vulkan"), b"#!/bin/sh\n", 0o755)
        serving_device.ENGINE_SERVER_PATHS["vulkan"] = vulkan

    def set_targets(self, targets: set) -> None:
        serving_device.cuda_build_gpu_targets = lambda *_a, **_k: set(targets)

    def set_engine(self, data: bytes) -> None:
        path = os.path.join(self.tmp, f"llama-server-cuda-{len(data)}-{hash(data) & 0xffff}")
        serving_device.ENGINE_SERVER_PATHS["cuda"] = _write(path, data, 0o755)

    def chosen(self) -> str:
        engine, _path = serving_device.select_serving_engine(vendor="nvidia")
        return engine

    def rungs(self) -> list:
        return [e for e, _p in serving_device.engine_ladder(vendor="nvidia")]


class ABuildTheDriverCannotTranslateIsDeclined(_Machine):

    def test_a_build_with_no_target_record_and_a_newer_toolkit_is_declined(self):
        """The shape this machine shipped: no record, PTX from 13.3, a 13.0 driver."""
        with self.assertLogs(serving_device.log, level="INFO") as caught:
            self.assertEqual(self.chosen(), "vulkan")
        text = "\n".join(caught.output)
        self.assertIn("declining the CUDA engine", text)
        self.assertIn("13.3", text)
        self.assertIn("13.0", text)

    def test_the_ladder_does_not_offer_that_rung(self):
        """A rung that cannot load would spend the restart budget on aborts."""
        rungs = self.rungs()
        self.assertNotIn("cuda", rungs)
        self.assertIn("vulkan", rungs)

    def test_a_failed_vulkan_start_never_climbs_back_to_that_rung(self):
        """After the floor fails, the next rung offered is never the build
        that cannot load: the ladder ends instead."""
        self.assertIsNone(serving_device.next_engine_after("vulkan", vendor="nvidia"))

    def test_a_build_with_only_ptx_for_the_card_and_a_newer_toolkit_is_declined(self):
        self.set_targets({"75-virtual", "89-real"})
        self.assertEqual(self.chosen(), "vulkan")
        self.assertNotIn("cuda", self.rungs())

    def test_a_newer_major_release_is_declined_even_with_compiled_code(self):
        self.set_targets({"75-real"})
        self.set_engine(b"\0" * 32 + b"Cuda compilation tools, release 14.0, V14.0.10" + b"\0" * 32)
        self.assertEqual(self.chosen(), "vulkan")
        self.assertNotIn("cuda", self.rungs())

    def test_no_compiled_code_recorded_and_a_newer_toolkit_is_declined_whatever_the_card(self):
        """No card can have compiled code when the build records none, so an
        unreadable capability does not keep the rung."""
        serving_device.detect_nvidia_compute_caps = lambda *_a, **_k: {}
        self.set_targets({"75-virtual"})
        self.assertEqual(self.chosen(), "vulkan")
        self.assertNotIn("cuda", self.rungs())

    def test_the_reason_names_the_card_the_toolkit_and_the_driver(self):
        reason = serving_device.cuda_driver_refusal_reason(
            caps=dict(self.CARD), server_path=serving_device.ENGINE_SERVER_PATHS["cuda"])
        self.assertIsNotNone(reason)
        for word in ("0000:01:00.0", "7.5", "13.3", "13.0"):
            self.assertIn(word, reason)


class ABuildTheDriverCanLoadKeepsItsRung(_Machine):

    def test_compiled_code_for_the_card_runs_on_an_older_minor_driver(self):
        """The shipped build since r6: 75-real from 13.3 serves on a 13.0 driver."""
        self.set_targets({"75-real", "86-real"})
        with self.assertNoLogs(serving_device.log, level="INFO"):
            self.assertEqual(self.chosen(), "cuda")
        self.assertIn("cuda", self.rungs())

    def test_ptx_from_a_toolkit_no_newer_than_the_driver_is_accepted(self):
        self.set_targets({"75-virtual"})
        self.set_engine(b"\0" * 32 + b"Cuda compilation tools, release 13.0, V13.0.48" + b"\0" * 32)
        with self.assertNoLogs(serving_device.log, level="INFO"):
            self.assertEqual(self.chosen(), "cuda")

    def test_no_target_record_with_a_toolkit_no_newer_than_the_driver_is_accepted(self):
        """The missing record alone is not the refusal; the versions are."""
        self.set_engine(b"\0" * 32 + b"Cuda compilation tools, release 12.8, V12.8.93" + b"\0" * 32)
        with self.assertNoLogs(serving_device.log, level="INFO"):
            self.assertEqual(self.chosen(), "cuda")
        self.assertIn("cuda", self.rungs())


class AnUnreadableReadingKeepsTheRungAndSaysSo(_Machine):

    def kept_with_a_line(self, *words: str) -> None:
        with self.assertLogs(serving_device.log, level="INFO") as caught:
            self.assertEqual(self.chosen(), "cuda")
        text = "\n".join(caught.output)
        self.assertNotIn("declining", text)
        for word in words + ("the engine's own load decides",):
            self.assertIn(word, text)

    def test_an_unreadable_driver_version_keeps_the_rung_and_says_so(self):
        serving_device.cuda_driver_release = lambda *_a, **_k: None
        self.kept_with_a_line("the driver's CUDA version could not be read")
        self.assertIn("cuda", self.rungs())

    def test_a_binary_with_no_toolkit_stamp_keeps_the_rung_and_says_so(self):
        self.set_engine(b"#!/bin/sh\n")
        self.kept_with_a_line("toolkit release could not be read",
                              serving_device.ENGINE_SERVER_PATHS["cuda"])
        self.assertIn("cuda", self.rungs())

    def test_an_unreadable_card_with_compiled_code_recorded_keeps_the_rung_and_says_so(self):
        """Compiled code is recorded, but for which card cannot be told."""
        serving_device.detect_nvidia_compute_caps = lambda *_a, **_k: {}
        self.set_targets({"75-real"})
        self.kept_with_a_line("compute capability could not be read", "13.3", "13.0")
        self.assertIn("cuda", self.rungs())


class TheTwoVersionsAreReadFromTheMachine(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cuda-versions-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_stamp_split_by_a_read_boundary_is_found(self):
        """The image is read in pieces; a stamp that straddles two of them is
        still read, and the newest release stamped in the image is the
        build's toolkit."""
        chunk = serving_device._STAMP_SCAN_CHUNK
        older = b"Cuda compilation tools, release 12.8, V12.8.1"
        start = chunk - 25
        data = older + b"\0" * (start - len(older)) + STAMP + b"\0" * 50
        self.assertLess(start, chunk)
        self.assertGreater(start + len(STAMP), chunk)
        path = _write(os.path.join(self.tmp, "engine"), data)
        self.assertEqual(serving_device.cuda_build_toolkit_release(path), (13, 3))

    def test_no_stamp_and_no_file_are_unknown(self):
        path = _write(os.path.join(self.tmp, "engine"), b"#!/bin/sh\n")
        self.assertIsNone(serving_device.cuda_build_toolkit_release(path))
        self.assertIsNone(serving_device.cuda_build_toolkit_release(
            os.path.join(self.tmp, "absent")))

    def test_the_driver_release_is_read_from_nvidia_smi(self):
        smi = _write(os.path.join(self.tmp, "nvidia-smi"),
                     b"#!/bin/sh\necho 'NVIDIA-SMI version  : 580.159.04'\n"
                     b"echo 'CUDA Version        : 13.0'\n", 0o755)
        self.assertEqual(serving_device.cuda_driver_release(smi_path=smi), (13, 0))

    def test_a_failing_or_silent_nvidia_smi_is_unknown(self):
        for body in (b"#!/bin/sh\nexit 3\n", b"#!/bin/sh\necho nothing\n"):
            with self.subTest(body=body):
                smi = _write(os.path.join(self.tmp, "nvidia-smi"), body, 0o755)
                self.assertIsNone(serving_device.cuda_driver_release(smi_path=smi))


if __name__ == "__main__":
    unittest.main()
