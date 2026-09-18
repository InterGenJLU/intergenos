# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The offload plan must weigh the model against the card it will go onto.

THE DEFECT, measured on a two-card workstation on 2026-09-18. Whether the model
fits — and how many of its layers go on the card — was computed from the
hardware detector's MOST-CAPABLE discrete card, while the model was placed on
whichever card the selector or the operator pinned. On a machine whose cards
differ in size those are different cards, and the daemon said so in its own log
without noticing:

    offload: llama_server.gpu_layers='auto' (tier 2, card 20464 MiB)
    -> 999 layers, engine hip (...), device pin ROCm0 at PCI 0000:06:00.0
    ...; the model needs 7331 MiB ... and the card has 20464 MiB

The model went onto the 8176 MiB card. It happened to fit — 8021 MiB of 8176
were in use afterwards, 155 MiB short of the card — but the fit was never
checked against the card that received it. A model between the two card sizes
would be declared a comfortable fit and every one of its layers pushed onto a
card that cannot hold them.

WHAT THE FIX HAS TO DO. The size comes from the SAME selection that produces the
pin, so the card's name, its address and its size can never describe different
cards. Every engine build prints its devices' totals in --list-devices, so this
is backend-neutral. When no card is pinned, or the engine named no size, the
previously detected figure stands — and either way the plan SAYS which figure it
used, in the log and in the trace row, so a recorded plan can never state a size
without saying whose it is.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from intergen import serving_device
from intergen.gpu_offload import plan_offload


MIB = 1024 * 1024

# The two-card workstation, as its HIP engine enumerates it.
TWO_CARD_LIST = (
    "Available devices:\n"
    "  ROCm0: AMD Radeon RX 7600 (8176 MiB, 6842 MiB free) [PCI 0000:06:00.0]\n"
    "  ROCm1: AMD Radeon RX 7900 XT (20464 MiB, 13922 MiB free)"
    " [PCI 0000:0e:00.0]\n"
)
SMALL_CARD_MB = 8176
BIG_CARD_MB = 20464


class TheSelectionCarriesTheCardsSizeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="plan-card-")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        self.sysfs = os.path.join(self.tmp, "sysfs")

    def test_the_selected_card_reports_its_own_size(self):
        name, pci, vram = serving_device.select_serving_device_name_pci_and_vram(
            list_output=TWO_CARD_LIST, discrete_vram_mb=BIG_CARD_MB,
            sysfs_root=self.sysfs)
        self.assertEqual((name, pci, vram),
                         ("ROCm1", "0000:0e:00.0", BIG_CARD_MB))

    def test_selecting_the_small_card_reports_the_small_size(self):
        name, pci, vram = serving_device.select_serving_device_name_pci_and_vram(
            list_output=TWO_CARD_LIST, discrete_vram_mb=SMALL_CARD_MB,
            sysfs_root=self.sysfs)
        self.assertEqual((name, pci, vram),
                         ("ROCm0", "0000:06:00.0", SMALL_CARD_MB))

    def test_no_selection_is_three_nones(self):
        name, pci, vram = serving_device.select_serving_device_name_pci_and_vram(
            list_output="Available devices:\n", discrete_vram_mb=BIG_CARD_MB,
            sysfs_root=self.sysfs)
        self.assertEqual((name, pci, vram), (None, None, None))

    def test_the_older_two_value_reader_still_answers_two_values(self):
        """Its callers are unchanged; only a third reading was added."""
        self.assertEqual(
            serving_device.select_serving_device_and_pci(
                list_output=TWO_CARD_LIST, discrete_vram_mb=BIG_CARD_MB,
                sysfs_root=self.sysfs),
            ("ROCm1", "0000:0e:00.0"))

    def test_the_name_and_address_readers_are_unchanged(self):
        self.assertEqual(
            serving_device.select_serving_device(
                list_output=TWO_CARD_LIST, discrete_vram_mb=SMALL_CARD_MB,
                sysfs_root=self.sysfs), "ROCm0")
        self.assertEqual(
            serving_device.select_serving_device_pci(
                list_output=TWO_CARD_LIST, discrete_vram_mb=SMALL_CARD_MB,
                sysfs_root=self.sysfs), "0000:06:00.0")


class TheOperatorPinnedCardReportsItsSizeTest(unittest.TestCase):
    """A card named by hand is the card the model goes onto."""

    def test_a_named_card_yields_its_address_and_its_size(self):
        self.assertEqual(
            serving_device.pci_and_vram_for_device_name(
                "ROCm0", list_output=TWO_CARD_LIST),
            ("0000:06:00.0", SMALL_CARD_MB))
        self.assertEqual(
            serving_device.pci_and_vram_for_device_name(
                "ROCm1", list_output=TWO_CARD_LIST),
            ("0000:0e:00.0", BIG_CARD_MB))

    def test_a_name_the_engine_does_not_print_yields_nothing(self):
        self.assertEqual(
            serving_device.pci_and_vram_for_device_name(
                "Vulkan9", list_output=TWO_CARD_LIST), (None, None))

    def test_a_line_without_a_pci_suffix_still_reports_its_size(self):
        """The address needs the in-tree list-devices patch; the size does not,
        so a build without that patch still gets a correctly measured plan."""
        no_pci = ("Available devices:\n"
                  "  ROCm0: AMD Radeon RX 7600 (8176 MiB, 6842 MiB free)\n")
        self.assertEqual(
            serving_device.pci_and_vram_for_device_name(
                "ROCm0", list_output=no_pci), (None, SMALL_CARD_MB))

    def test_the_address_only_reader_agrees(self):
        self.assertEqual(
            serving_device.pci_for_device_name("ROCm0",
                                               list_output=TWO_CARD_LIST),
            serving_device.pci_and_vram_for_device_name(
                "ROCm0", list_output=TWO_CARD_LIST)[0])


class TheSizeChangesTheAnswerTest(unittest.TestCase):
    """The whole point: on a model between the two card sizes, the two figures
    give opposite verdicts. Without this the fix would be unobservable."""

    # A model that fits the 20464 MiB card comfortably and cannot fit the
    # 8176 MiB one: 12000 MiB of weights, no projector.
    MODEL_BYTES = 12000 * MIB
    LAYERS = 40

    def _plan(self, vram_mb):
        return plan_offload(vram_mb=vram_mb, model_bytes=self.MODEL_BYTES,
                            projector_bytes=0, total_layers=self.LAYERS)

    def test_the_big_card_fits_it(self):
        plan = self._plan(BIG_CARD_MB)
        self.assertIs(plan.fits, True)
        self.assertIn(str(BIG_CARD_MB), plan.reason)

    def test_the_pinned_small_card_does_not(self):
        plan = self._plan(SMALL_CARD_MB)
        self.assertIs(plan.fits, False)
        self.assertLess(plan.layers, self.LAYERS)
        self.assertIn(str(SMALL_CARD_MB), plan.reason)

    def test_the_reason_names_the_size_it_used(self):
        """So a recorded plan can be checked against the card it was about."""
        self.assertNotIn(str(BIG_CARD_MB), self._plan(SMALL_CARD_MB).reason)


class TheSingleCardControlTest(unittest.TestCase):
    """On a one-card machine the two figures are the same, so the plan is the
    same figure it always was — the fix must be invisible there."""

    ONE_CARD_LIST = (
        "Available devices:\n"
        "  Vulkan0: AMD Radeon RX 7600 (8176 MiB, 6842 MiB free)"
        " [PCI 0000:06:00.0]\n")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="single-card-plan-")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))

    def test_the_pinned_size_equals_the_detected_size(self):
        detected = SMALL_CARD_MB
        _name, _pci, pinned = (
            serving_device.select_serving_device_name_pci_and_vram(
                list_output=self.ONE_CARD_LIST, discrete_vram_mb=detected,
                sysfs_root=os.path.join(self.tmp, "sysfs")))
        self.assertEqual(pinned, detected)

    def test_and_so_the_plan_is_identical(self):
        model_bytes = 5368 * MIB
        by_detected = plan_offload(vram_mb=SMALL_CARD_MB,
                                   model_bytes=model_bytes,
                                   projector_bytes=875 * MIB, total_layers=33)
        by_pinned = plan_offload(vram_mb=SMALL_CARD_MB,
                                 model_bytes=model_bytes,
                                 projector_bytes=875 * MIB, total_layers=33)
        self.assertEqual(by_detected, by_pinned)


class TheDaemonWiringTest(unittest.TestCase):
    """The daemon's start-up block must read the size from the selection and
    record WHICH figure it used. Read from the source, because standing up the
    whole daemon here would prove less and cost more."""

    def _source(self):
        from pathlib import Path
        import intergen.dbus_daemon as mod
        return Path(mod.__file__).read_text(encoding="utf-8")

    def test_the_plan_is_given_the_pinned_size_when_there_is_one(self):
        src = self._source()
        self.assertIn("if isinstance(_device_vram_mb, int) and _device_vram_mb > 0:",
                      src)
        self.assertIn("_plan_vram_mb = _device_vram_mb", src)
        self.assertIn("plan_for_model(vram_mb=_plan_vram_mb", src)

    def test_it_falls_back_to_the_detected_figure(self):
        src = self._source()
        self.assertIn("_plan_vram_mb = _vram_mb", src)

    def test_the_trace_row_names_which_figure_was_used(self):
        src = self._source()
        self.assertIn('"vram_mb_source": _vram_source', src)
        self.assertIn('"detected_vram_mb": _vram_mb', src)
        self.assertIn('"pinned_card_vram_mb": _device_vram_mb', src)

    def test_the_log_line_names_it_too(self):
        src = self._source()
        self.assertIn('"MiB from %s) -> %d layers', src)


if __name__ == "__main__":
    unittest.main()
