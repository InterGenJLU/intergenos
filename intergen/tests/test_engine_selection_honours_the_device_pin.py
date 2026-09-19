# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Engine selection must honour the operator's device pin at EVERY step.

TWO DEFECTS, both measured on a two-card AMD workstation on 2026-09-19 with the
shipped daemon, the six-target HIP build installed, and one architecture removed
from the build's own architecture record.

DEFECT ONE — THE LADDER ASKED THE GATE ABOUT A DIFFERENT CARD THAN THE PIN NAMES.
``select_serving_engine`` passes the operator's device pin to the per-card
architecture gate, so with the pinned card uncovered it correctly declined the
HIP engine and chose Vulkan. ``engine_ladder`` — the list a caller walks AFTER an
engine has failed — asked the same gate with NO pin, so the gate answered about
the card the AUTOMATIC selector would choose instead. On a machine whose two
cards differ, that is a different card: the pinned card was uncovered and the
auto-selected one was covered, so the ladder offered the HIP engine again and the
daemon relaunched the very engine the gate had just refused, this time with no
device pin at all and the model spread across both cards. A gate that one call
site honours and another ignores is not a gate.

DEFECT TWO — THE FALLBACK ENGINE WAS LAUNCHED WITH THE OTHER ENGINE'S DEVICE NAME.
Device names are backend-local: "ROCm0" means a card to the HIP build and means
nothing to the Vulkan build. When the architecture gate declined HIP and Vulkan
was chosen, the operator's configured name was handed to the Vulkan binary
unchanged, which exited 1 with `invalid device: ROCm0` on every attempt. The
information needed to catch it was already in hand — the pinned name did not
resolve to an address in the chosen binary's enumeration — and nothing acted on
it. On the display-free card the consequence was total: Vulkan could not start,
HIP was correctly refused, the ladder reported itself exhausted, and a machine
with a working Vulkan engine served nothing.

WHAT THE FIX HAS TO DO.
  1. Every place that consults the architecture gate asks about the card that
     will actually serve. The pin travels through ``engine_ladder`` and
     ``next_engine_after`` exactly as it travels through
     ``select_serving_engine``; with no pin, the automatic selection is asked,
     which is today's behaviour unchanged.
  2. A device name reaches an engine only after THAT engine's own enumeration
     resolved it. A name the chosen engine does not know is re-resolved by PCI
     address into the chosen engine's namespace when both engines print one,
     and otherwise dropped with one plain line saying so. Dropping the pin
     leaves the engine to make its own selection, which is a working machine;
     passing an unresolvable name is a machine that cannot start.

Everything below runs with no GPU present: fake engine binaries, a fake KFD
topology and the engines' ``--list-devices`` text handed in directly.
"""
from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from intergen import serving_device


# The real machine both defects were measured on, as each engine reports it.
# The HIP build names the cards ROCm0/ROCm1; the Vulkan build names the same
# two cards Vulkan0/Vulkan1 and puts them in the opposite order, which is the
# whole reason a name cannot be carried from one engine to another.
_HIP_LIST = (
    "Available devices:\n"
    "  ROCm0: AMD Radeon RX 7600 (8176 MiB, 6842 MiB free) [PCI 0000:06:00.0]\n"
    "  ROCm1: AMD Radeon RX 7900 XT (20464 MiB, 13922 MiB free)"
    " [PCI 0000:0e:00.0]\n"
)
_VULKAN_LIST = (
    "Available devices:\n"
    "  Vulkan0: AMD Radeon RX 7900 XT (20464 MiB, 13922 MiB free)"
    " [PCI 0000:0e:00.0]\n"
    "  Vulkan1: AMD Radeon RX 7600 (8176 MiB, 6842 MiB free)"
    " [PCI 0000:06:00.0]\n"
)
# The same Vulkan enumeration from a build without the in-tree PCI-id patch:
# every line is there and no line carries an address, so nothing can be matched
# by address and the only honest answer is to drop the pin.
_VULKAN_LIST_NO_PCI = (
    "Available devices:\n"
    "  Vulkan0: AMD Radeon RX 7900 XT (20464 MiB, 13922 MiB free)\n"
    "  Vulkan1: AMD Radeon RX 7600 (8176 MiB, 6842 MiB free)\n"
)


def _topology(tmp, nodes):
    """A KFD topology tree; node 0 is the CPU node the driver always writes."""
    root = Path(tmp) / "nodes"
    for i, (version, location_id) in enumerate([(0, 0)] + list(nodes)):
        node = root / str(i)
        node.mkdir(parents=True)
        (node / "properties").write_text(
            f"cpu_cores_count {0 if i else 16}\n"
            f"simd_count {64 if i else 0}\n"
            f"gfx_target_version {version}\n"
            f"location_id {location_id}\n"
            "domain 0\n")
    return str(root)


def _targets_file(tmp, text):
    p = Path(tmp) / "gpu-targets"
    p.write_text(text)
    return str(p)


def _fake_binary(directory, name):
    p = Path(directory) / name
    p.write_text("#!/bin/sh\nexit 0\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return str(p)


class LadderAsksAboutThePinnedCardTest(unittest.TestCase):
    """DEFECT ONE. The machine measured on 2026-09-19: the HIP build covers the
    RX 7900 XT (gfx1100) and NOT the RX 7600 (gfx1102); the operator pinned the
    RX 7600, which is the card driving the display; the automatic selector
    prefers the display-free RX 7900 XT. The pin and the automatic answer are
    therefore different cards, and the gate must be asked about the pinned one.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ladder-pin-")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        self._orig_paths = dict(serving_device.ENGINE_SERVER_PATHS)
        self.addCleanup(
            lambda: serving_device.ENGINE_SERVER_PATHS.update(self._orig_paths))
        for attr in ("KFD_TOPOLOGY_NODES", "HIP_GPU_TARGETS_PATH",
                     "select_serving_device_and_pci", "pci_for_device_name"):
            orig = getattr(serving_device, attr)
            self.addCleanup(
                lambda a=attr, o=orig: setattr(serving_device, a, o))
        for engine in ("hip", "vulkan"):
            serving_device.ENGINE_SERVER_PATHS[engine] = _fake_binary(
                self.tmp, f"{engine}-server")
        serving_device.ENGINE_SERVER_PATHS["cuda"] = os.path.join(
            self.tmp, "absent-cuda")
        # node 1 = RX 7600 gfx1102 at 0000:06:00.0; node 2 = RX 7900 XT
        # gfx1100 at 0000:0e:00.0.
        serving_device.KFD_TOPOLOGY_NODES = _topology(
            self.tmp, [(110002, 1536), (110000, 3584)])
        # The build covers the RX 7900 XT only.
        serving_device.HIP_GPU_TARGETS_PATH = _targets_file(
            self.tmp, "gfx1030;gfx1100;gfx1101;gfx1200;gfx1201\n")
        # The automatic selector prefers the display-free RX 7900 XT.
        serving_device.select_serving_device_and_pci = (
            lambda *a, **k: ("ROCm1", "0000:0e:00.0"))
        serving_device.pci_for_device_name = (
            lambda name, *a, **k: {"ROCm0": "0000:06:00.0",
                                   "ROCm1": "0000:0e:00.0"}.get(name))

    def test_the_chooser_declines_hip_for_the_pinned_card(self):
        """The behaviour that was already right, kept as the control."""
        engine, _path = serving_device.select_serving_engine(
            vendor="amd", device_pin="ROCm0")
        self.assertEqual(engine, "vulkan")

    def test_the_ladder_does_not_offer_hip_for_the_pinned_card(self):
        """THE RED. The ladder asked with no pin, got the auto-selected card's
        answer, and offered HIP again after the chooser had declined it."""
        rungs = [e for e, _p in serving_device.engine_ladder(
            "amd", device_pin="ROCm0")]
        self.assertEqual(rungs, ["vulkan"])

    def test_the_ladder_still_offers_hip_when_the_pin_names_a_covered_card(self):
        rungs = [e for e, _p in serving_device.engine_ladder(
            "amd", device_pin="ROCm1")]
        self.assertEqual(rungs, ["hip", "vulkan"])

    def test_with_no_pin_the_ladder_asks_the_automatic_selection(self):
        """Today's behaviour, unchanged: no pin means the auto-selected card,
        which here is covered, so HIP is a rung."""
        rungs = [e for e, _p in serving_device.engine_ladder("amd")]
        self.assertEqual(rungs, ["hip", "vulkan"])

    def test_the_next_rung_after_a_failure_carries_the_pin_too(self):
        """next_engine_after is what the daemon actually calls when an engine
        has died, so the pin has to reach the gate through it as well."""
        self.assertIsNone(serving_device.next_engine_after(
            "vulkan", "amd", device_pin="ROCm0"))
        nxt = serving_device.next_engine_after(
            "vulkan", "amd", device_pin="ROCm1")
        self.assertIsNotNone(nxt)
        self.assertEqual(nxt[0], "hip")

    def test_an_auto_pin_is_the_same_as_no_pin(self):
        """'auto' and the empty string are the configuration's way of saying
        'no pin', and must not be looked up as a device name."""
        for value in ("auto", "", None):
            rungs = [e for e, _p in serving_device.engine_ladder(
                "amd", device_pin=value)]
            self.assertEqual(rungs, ["hip", "vulkan"], value)


class AdvanceEngineCarriesThePinTest(unittest.TestCase):
    """The daemon's own step: when a running engine fails and the manager moves
    to the next rung, the pin currently in force is what the gate must be asked
    about. Proving the wiring, not the decision (proved above)."""

    def test_the_manager_passes_its_device_pin_to_the_ladder(self):
        from intergen import llama_manager
        seen = {}

        def fake_next_engine_after(failed_engine, vendor=None, tried=None,
                                   device_pin=None):
            seen["failed"] = failed_engine
            seen["device_pin"] = device_pin
            return None

        orig = serving_device.next_engine_after
        serving_device.next_engine_after = fake_next_engine_after
        self.addCleanup(
            lambda: setattr(serving_device, "next_engine_after", orig))

        mgr = llama_manager.LlamaManager.__new__(llama_manager.LlamaManager)
        mgr._config = llama_manager.ServerConfig(
            model_path="/does/not/exist.gguf", port=8080, context_size=4096,
            gpu_layers=0, parallel=1, jinja=False, reasoning="off",
            server_path=serving_device.ENGINE_SERVER_PATHS["hip"],
            device="ROCm0")
        mgr._engines_tried = set()
        mgr._advance_engine()
        self.assertEqual(seen.get("device_pin"), "ROCm0")


if __name__ == "__main__":
    unittest.main()
