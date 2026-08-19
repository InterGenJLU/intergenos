# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""What this box can run, and remembering what the user picked.

Three GPU-driver cases are pinned because they are the whole point of the
advisory (decided 2026-07-31): an NVIDIA card on the open-source driver reports
no video memory, so capability cannot be read and the higher tiers must NOT be
offered as if they had been measured; an NVIDIA card on NVIDIA's driver reads
normally and gets no advisory at all; a box with no NVIDIA card is untouched by
any of it.

Every case is driven off a fake sysfs tree, so the real hardware is irrelevant
and the tests run anywhere.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from unittest import mock

from intergen import model_choice
from intergen import hardware
from intergen.interfaces.types import HardwareTierLevel


def _fake_drm(root: Path, cards: list[tuple[str, str | None]]) -> Path:
    """Build a /sys/class/drm lookalike.

    ``cards`` is a list of (pci_vendor_id, bound_kernel_driver); a driver of
    None means no driver symlink at all, which is what an unbound card looks
    like.
    """
    drm = root / "drm"
    drivers = root / "bus" / "pci" / "drivers"
    drm.mkdir(parents=True, exist_ok=True)
    drivers.mkdir(parents=True, exist_ok=True)
    for i, (vendor, driver) in enumerate(cards):
        device = drm / f"card{i}" / "device"
        device.mkdir(parents=True, exist_ok=True)
        (device / "vendor").write_text(vendor + "\n")
        if driver:
            target = drivers / driver
            target.mkdir(parents=True, exist_ok=True)
            (device / "driver").symlink_to(target)
    return drm


class DriverDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_nvidia_on_nouveau_needs_the_advisory(self) -> None:
        drm = _fake_drm(self.root, [("0x10de", "nouveau")])
        state = model_choice.detect_driver_state(drm)
        self.assertTrue(state.nvidia_present)
        self.assertEqual(state.driver, "nouveau")
        self.assertFalse(state.proprietary_nvidia)
        self.assertTrue(state.needs_driver_advisory)

    def test_nvidia_on_the_proprietary_driver_needs_no_advisory(self) -> None:
        drm = _fake_drm(self.root, [("0x10de", "nvidia")])
        state = model_choice.detect_driver_state(drm)
        self.assertTrue(state.nvidia_present)
        self.assertTrue(state.proprietary_nvidia)
        self.assertFalse(state.needs_driver_advisory)

    def test_no_nvidia_card_needs_no_advisory(self) -> None:
        drm = _fake_drm(self.root, [("0x1002", "amdgpu")])
        state = model_choice.detect_driver_state(drm)
        self.assertFalse(state.nvidia_present)
        self.assertEqual(state.driver, "amdgpu")
        self.assertFalse(state.needs_driver_advisory)

    def test_nvidia_beside_an_amd_card_is_still_detected(self) -> None:
        drm = _fake_drm(self.root, [("0x1002", "amdgpu"), ("0x10de", "nouveau")])
        state = model_choice.detect_driver_state(drm)
        self.assertTrue(state.needs_driver_advisory)
        self.assertEqual(state.driver, "nouveau")

    def test_missing_sysfs_is_not_an_error(self) -> None:
        state = model_choice.detect_driver_state(self.root / "nope")
        self.assertFalse(state.nvidia_present)
        self.assertFalse(state.needs_driver_advisory)

    def test_unbound_card_reads_no_driver(self) -> None:
        drm = _fake_drm(self.root, [("0x10de", None)])
        state = model_choice.detect_driver_state(drm)
        self.assertTrue(state.nvidia_present)
        self.assertIsNone(state.driver)
        # No driver at all is not NVIDIA's driver, so the advisory applies.
        self.assertTrue(state.needs_driver_advisory)


class LadderTests(unittest.TestCase):
    """The capability ladder — 35B/9B/2B, 9B/2B, or 2B."""

    def test_big_card_offers_all_three(self) -> None:
        tiers = model_choice.runnable_tiers(is_discrete=True, vram_mb=24000)
        self.assertEqual(tiers, (HardwareTierLevel.TIER_3,
                                 HardwareTierLevel.TIER_2,
                                 HardwareTierLevel.TIER_1))

    def test_mid_card_offers_nine_b_and_two_b(self) -> None:
        tiers = model_choice.runnable_tiers(is_discrete=True, vram_mb=8192)
        self.assertEqual(tiers, (HardwareTierLevel.TIER_2,
                                 HardwareTierLevel.TIER_1))

    def test_small_card_offers_only_the_two_b(self) -> None:
        tiers = model_choice.runnable_tiers(is_discrete=True, vram_mb=4096)
        self.assertEqual(tiers, (HardwareTierLevel.TIER_1,))

    def test_no_discrete_card_offers_only_the_two_b(self) -> None:
        tiers = model_choice.runnable_tiers(is_discrete=False, vram_mb=32000)
        self.assertEqual(tiers, (HardwareTierLevel.TIER_1,))

    def test_unknown_vram_offers_only_the_two_b(self) -> None:
        # Fail DOWN on unknown capability — the same rule tier assignment uses.
        tiers = model_choice.runnable_tiers(is_discrete=True, vram_mb=None)
        self.assertEqual(tiers, (HardwareTierLevel.TIER_1,))

    def test_a_twenty_gb_card_does_not_reach_the_thirty_five_b(self) -> None:
        # The Tier-3 gate is a resident-fit gate, not a "big card" gate.
        tiers = model_choice.runnable_tiers(is_discrete=True, vram_mb=20000)
        self.assertNotIn(HardwareTierLevel.TIER_3, tiers)
        self.assertIn(HardwareTierLevel.TIER_2, tiers)


class OfferTests(unittest.TestCase):
    def test_advisory_offers_the_two_b_only_and_says_why(self) -> None:
        state = model_choice.GpuDriverState(nvidia_present=True,
                                            driver="nouveau",
                                            proprietary_nvidia=False)
        # Even with a VRAM figure in hand, an unreadable card must not have the
        # higher rungs offered as measured capability.
        offer = model_choice.build_offer(is_discrete=True, vram_mb=24000,
                                         driver_state=state)
        self.assertTrue(offer.advisory)
        self.assertEqual(offer.tiers, (HardwareTierLevel.TIER_1,))
        self.assertIn("NVIDIA", offer.advisory_text)
        self.assertIn("2B", offer.advisory_text)

    def test_proprietary_driver_runs_the_ladder_with_no_advisory(self) -> None:
        state = model_choice.GpuDriverState(nvidia_present=True,
                                            driver="nvidia",
                                            proprietary_nvidia=True)
        offer = model_choice.build_offer(is_discrete=True, vram_mb=24000,
                                         driver_state=state)
        self.assertFalse(offer.advisory)
        self.assertEqual(offer.advisory_text, "")
        self.assertEqual(offer.tiers[0], HardwareTierLevel.TIER_3)
        self.assertTrue(offer.is_choice)

    def test_status_payload_is_json_serialisable(self) -> None:
        state = model_choice.GpuDriverState(nvidia_present=False,
                                            driver="amdgpu")
        offer = model_choice.build_offer(is_discrete=True, vram_mb=8192,
                                         driver_state=state)
        payload = offer.to_status()
        json.dumps(payload)  # must not raise — it crosses D-Bus and a pipe
        self.assertEqual(payload["tiers"], [2, 1])
        self.assertFalse(payload["advisory"])
        self.assertEqual(payload["gpu_driver"], "amdgpu")

    def test_single_rung_is_not_a_choice(self) -> None:
        state = model_choice.GpuDriverState()
        offer = model_choice.build_offer(is_discrete=False, vram_mb=None,
                                         driver_state=state)
        self.assertFalse(offer.is_choice)


class ChoicePersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)

    def test_choice_round_trips(self) -> None:
        self.assertIsNone(model_choice.load_choice(self.home))
        model_choice.record_choice(HardwareTierLevel.TIER_1, home=self.home,
                                   chosen_by="tester")
        self.assertEqual(model_choice.load_choice(self.home),
                         HardwareTierLevel.TIER_1)

    def test_choice_is_durable_across_readers(self) -> None:
        # The point of persisting: the user is not asked again on every boot.
        model_choice.record_choice(HardwareTierLevel.TIER_2, home=self.home)
        for _ in range(3):
            self.assertEqual(model_choice.load_choice(self.home),
                             HardwareTierLevel.TIER_2)

    def test_a_later_choice_replaces_an_earlier_one(self) -> None:
        # Re-running setup re-offers the ladder; picking again must stick.
        model_choice.record_choice(HardwareTierLevel.TIER_2, home=self.home)
        model_choice.record_choice(HardwareTierLevel.TIER_1, home=self.home)
        self.assertEqual(model_choice.load_choice(self.home),
                         HardwareTierLevel.TIER_1)

    def test_corrupt_record_reads_as_no_choice(self) -> None:
        path = model_choice.choice_path(self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")
        self.assertIsNone(model_choice.load_choice(self.home))

    def test_record_notes_whether_the_advisory_was_shown(self) -> None:
        model_choice.record_choice(HardwareTierLevel.TIER_1, home=self.home,
                                   advisory_shown=True)
        data = json.loads(model_choice.choice_path(self.home).read_text())
        self.assertTrue(data["advisory_shown"])
        self.assertEqual(data["tier"], 1)

    def test_an_unwritable_home_does_not_raise(self) -> None:
        # Losing the preference re-asks; it must never break setup.
        path = model_choice.record_choice(HardwareTierLevel.TIER_1,
                                          home=Path("/proc/nonexistent-dir"))
        self.assertFalse(path.exists())


# The real output of `llama-server --list-devices` on the HP Victus (.120),
# captured 2026-08-03: a GTX 1650 Mobile on the open-source driver, alongside
# the Intel integrated adapter that reports a LARGER heap than the real card.
VICTUS_LIST_DEVICES = """Available devices:
  Vulkan0: NVIDIA GeForce GTX 1650 (NVK TU117) (4352 MiB, 3916 MiB free)
  Vulkan1: Intel(R) Graphics (ADL GT2) (5704 MiB, 2500 MiB free)
"""


class OpenDriverVramReadTests(unittest.TestCase):
    """The card's SIZE is readable on the open driver, from the serving stack."""

    def test_the_nvidia_card_is_read_not_the_bigger_integrated_heap(self) -> None:
        # The whole hazard in one assertion: the iGPU line reports 5704 MiB,
        # more than the real 4 GB card, because it borrows system memory. A
        # reader that took the largest line would report the wrong card.
        self.assertEqual(
            hardware.open_driver_vram_mb(
                "nvidia", list_output=VICTUS_LIST_DEVICES), 4352)

    def test_no_matching_device_reads_as_unknown(self) -> None:
        self.assertIsNone(hardware.open_driver_vram_mb(
            "nvidia", list_output="Available devices:\n"))

    def test_unparsable_output_reads_as_unknown(self) -> None:
        # Fail-safe: unknown is exactly where every caller stood before.
        self.assertIsNone(hardware.open_driver_vram_mb(
            "nvidia", list_output="llama-server: command not found"))

    def test_patched_engine_output_with_pci_suffix_still_parses(self) -> None:
        # The in-tree list-devices patch appends " [PCI dddd:bb:dd.f]" to each
        # device line; this reader's regex is lockstep with
        # intergen.serving_device's and both accept the suffix as OPTIONAL —
        # patched and unpatched engines must read identically.
        patched = (
            "Available devices:\n"
            "  Vulkan0: NVIDIA GeForce GTX 1650 (NVK TU117) "
            "(4352 MiB, 3916 MiB free) [PCI 0000:01:00.0]\n"
            "  Vulkan1: Intel(R) Graphics (ADL GT2) "
            "(5704 MiB, 2500 MiB free)\n"
        )
        self.assertEqual(
            hardware.open_driver_vram_mb("nvidia", list_output=patched), 4352)


class OpenDriverAdvisoryTests(unittest.TestCase):
    """What the user is told when the card's size IS known."""

    def test_a_card_too_small_says_the_driver_will_not_change_the_model(self) -> None:
        text = model_choice._advisory_text_for(4352)
        self.assertIn("4352 MiB", text)
        self.assertIn("will not make a larger model available", text)
        # It must NOT keep claiming ignorance it no longer has.
        self.assertNotIn("cannot tell", text)

    def test_a_card_big_enough_says_the_driver_is_what_proves_it(self) -> None:
        text = model_choice._advisory_text_for(8192)
        self.assertIn("8192 MiB", text)
        self.assertIn("enough memory for a larger model", text)
        self.assertIn("Install NVIDIA's drivers", text)

    def test_an_unreadable_size_keeps_the_original_text(self) -> None:
        self.assertEqual(model_choice._advisory_text_for(None),
                         model_choice.ADVISORY_TEXT)

    def test_knowing_the_size_never_unlocks_a_rung(self) -> None:
        # The rule this fix must not break: a size is not a proof of offload.
        state = model_choice.GpuDriverState(driver="nouveau", nvidia_present=True,
                                            proprietary_nvidia=False)
        with mock.patch.object(hardware, "open_driver_vram_mb",
                               return_value=24576):
            offer = model_choice.build_offer(is_discrete=True, vram_mb=None,
                                             driver_state=state)
        self.assertEqual(offer.tiers, (HardwareTierLevel.TIER_1,))
        self.assertTrue(offer.advisory)
        self.assertIn("24576 MiB", offer.advisory_text)

    def test_a_broken_reader_never_breaks_setup(self) -> None:
        state = model_choice.GpuDriverState(driver="nouveau", nvidia_present=True,
                                            proprietary_nvidia=False)
        with mock.patch.object(hardware, "open_driver_vram_mb",
                               side_effect=RuntimeError("boom")):
            offer = model_choice.build_offer(is_discrete=True, vram_mb=None,
                                             driver_state=state)
        self.assertEqual(offer.advisory_text, model_choice.ADVISORY_TEXT)


class TestSuiteNeverReadsHostGpu(unittest.TestCase):
    """The test suite's own GPU isolation, checked instead of assumed.

    The project-root conftest.py repoints `detect_driver_state`'s DEFAULT sysfs
    root at an empty throwaway directory, so a test that calls `run_setup` or
    `report_offer` without pinning the driver state cannot read the GPU of the
    machine running the suite. That isolation is invisible at the call site —
    a test inherits it silently and looks identical either way — which means
    removing it would go unnoticed until a suite started failing on one
    development machine and passing on the rest. That is exactly what happened once
    already, so the isolation gets a test rather than trust.
    """

    def test_default_drm_root_is_not_the_real_sysfs(self) -> None:
        default = model_choice.detect_driver_state.__defaults__[0]
        self.assertNotEqual(
            str(default), "/sys/class/drm",
            "conftest.py's driver-probe pin is not in effect: the default "
            "sysfs root is the real one, so any test that drives run_setup "
            "without pinning the driver state is reading this machine's GPU.")
        self.assertFalse(
            Path(default).exists() and any(Path(default).iterdir()),
            f"the pinned drm root {default!r} is not empty; the whole point is "
            "that it yields a deterministic no-NVIDIA state on every box.")

    def test_the_pinned_default_yields_a_no_nvidia_state(self) -> None:
        # The real parser still runs — only the default root moved — so this
        # also proves the empty-directory path returns the clean state rather
        # than raising or reporting a card.
        state = model_choice.detect_driver_state()
        self.assertFalse(state.nvidia_present)
        self.assertFalse(state.needs_driver_advisory)

    def test_an_explicit_root_still_reaches_the_real_parser(self) -> None:
        # The pin must not have turned the function into a stub: a caller that
        # passes its own root still gets genuine parsing. This is what lets the
        # tests above in this file keep testing the real detection logic.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        drm = _fake_drm(Path(tmp.name), [("0x10de", "nouveau")])
        state = model_choice.detect_driver_state(drm)
        self.assertTrue(state.nvidia_present)
        self.assertEqual(state.driver, "nouveau")
        self.assertTrue(state.needs_driver_advisory)


if __name__ == "__main__":
    unittest.main()
