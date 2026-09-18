# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The offload plan must weigh the figure the card can actually give it.

WHAT WAS MEASURED. The engine's own ``--list-devices`` line carries TWO numbers
for every card — the total and the free:

    Vulkan0: AMD Radeon RX 7600 (8176 MiB, 6842 MiB free) [PCI 0000:06:00.0]

The plan read the first and ignored the second. On a card that is painting the
desktop the difference is the desktop: measured on a two-card workstation
2026-09-18, the shipped model needed 7331 MiB (5368 MiB of weights, 875 MiB of
vision projector, 1088 MiB reserved for the key/value cache and compute
buffers), the card reported 8176 MiB total and 6842 MiB free, and the plan
declared a comfortable fit against the total. The load then finished with
155 MiB to spare — a fit that was never measured, only guessed at.

THE RULE THIS FIXES IT WITH. Both figures come off the SAME device line, in the
SAME selection that produces the pin, so they can never describe different
cards. Which one the plan is weighed against is decided by one fact about that
card, and the decision is stated in words in the plan's own record:

  * the card is PROVABLY driving a display, and a free figure was reported ->
    the FREE figure, because the desktop already holds the difference;
  * anything else -- display-free, or the display state unreadable, or no free
    figure on the line -> the TOTAL, exactly as before.

The second clause is deliberate and is what keeps a single-card machine's plan
identical: an engine build without the in-tree list-devices patch prints no PCI
address, the display state is then unknowable, and a plan that silently shrank
on that unknown would change behaviour on machines this defect never touched.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from intergen import serving_device
from intergen.gpu_offload import plan_offload

MIB = 1024 * 1024

# The two cards of the workstation the defect was measured on.
DISPLAY_CARD_TOTAL_MB = 8176
DISPLAY_CARD_FREE_MB = 6842
SERVING_CARD_TOTAL_MB = 20464
SERVING_CARD_FREE_MB = 20281

# The shipped 9B model with its vision projector, at the shipped context.
MODEL_BYTES = 5368 * MIB
PROJECTOR_BYTES = 875 * MIB
TOTAL_LAYERS = 33
REQUIRED_MB = 7331          # 5368 + 875 + 1088, as the plan computes it

TWO_CARD_LIST = (
    "Available devices:\n"
    f"  Vulkan0: AMD Radeon RX 7600 ({DISPLAY_CARD_TOTAL_MB} MiB, "
    f"{DISPLAY_CARD_FREE_MB} MiB free) [PCI 0000:06:00.0]\n"
    f"  Vulkan1: AMD Radeon PRO W7800 ({SERVING_CARD_TOTAL_MB} MiB, "
    f"{SERVING_CARD_FREE_MB} MiB free) [PCI 0000:0e:00.0]\n")

# The same line an unpatched engine build prints: no PCI address at all.
ONE_CARD_LIST_NO_PCI = (
    "Available devices:\n"
    f"  Vulkan0: AMD Radeon RX 7600 ({DISPLAY_CARD_TOTAL_MB} MiB, "
    f"{DISPLAY_CARD_FREE_MB} MiB free)\n")


def _sysfs_with(tmp: str, cards: dict) -> str:
    """Build a kernel-records tree the display check can read.

    ``cards`` maps a PCI address to True (a connected display) or False (none).
    The shape is the one :func:`serving_device._pci_drives_display` reads: the
    card's DRM node is named under ``<sysfs>/bus/pci/devices/<pci>/drm/cardN``
    and each connector's state is read from
    ``<sysfs>/class/drm/cardN-<TYPE>-<n>/status`` — two different places, which
    is why a test tree that puts the status file under the PCI path alone
    yields "unknown" rather than an answer.
    """
    root = os.path.join(tmp, "sysfs")
    for i, (pci, connected) in enumerate(sorted(cards.items())):
        card = f"card{i}"
        os.makedirs(os.path.join(root, "bus", "pci", "devices", pci, "drm",
                                 card), exist_ok=True)
        conn = os.path.join(root, "class", "drm", f"{card}-DP-1")
        os.makedirs(conn, exist_ok=True)
        with open(os.path.join(conn, "status"), "w", encoding="utf-8") as fh:
            fh.write("connected\n" if connected else "disconnected\n")
    return root


class TheSelectionCarriesBothFiguresTest(unittest.TestCase):
    """One selection, one device line, both numbers off it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="plan-free-memory-")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))

    def test_the_auto_selection_reports_the_free_figure_too(self):
        sysfs = _sysfs_with(self.tmp, {"0000:06:00.0": True,
                                       "0000:0e:00.0": False})
        name, pci, total, free = (
            serving_device.select_serving_device_name_pci_vram_and_free(
                list_output=TWO_CARD_LIST,
                discrete_vram_mb=SERVING_CARD_TOTAL_MB, sysfs_root=sysfs))
        self.assertEqual(name, "Vulkan1")
        self.assertEqual(pci, "0000:0e:00.0")
        self.assertEqual(total, SERVING_CARD_TOTAL_MB)
        self.assertEqual(free, SERVING_CARD_FREE_MB)

    def test_an_operator_pin_reports_it_too(self):
        pci, total, free = serving_device.pci_vram_and_free_for_device_name(
            "Vulkan0", list_output=TWO_CARD_LIST)
        self.assertEqual(pci, "0000:06:00.0")
        self.assertEqual(total, DISPLAY_CARD_TOTAL_MB)
        self.assertEqual(free, DISPLAY_CARD_FREE_MB)

    def test_the_existing_three_value_readings_are_unchanged(self):
        """The three-value wrappers added on 2026-09-18, when the plan first
        took its size from the pinned card, keep their shape — so nothing that
        already reads them has to change."""
        sysfs = _sysfs_with(self.tmp, {"0000:06:00.0": True,
                                       "0000:0e:00.0": False})
        triple = serving_device.select_serving_device_name_pci_and_vram(
            list_output=TWO_CARD_LIST,
            discrete_vram_mb=SERVING_CARD_TOTAL_MB, sysfs_root=sysfs)
        self.assertEqual(triple, ("Vulkan1", "0000:0e:00.0",
                                  SERVING_CARD_TOTAL_MB))
        self.assertEqual(
            serving_device.pci_and_vram_for_device_name(
                "Vulkan0", list_output=TWO_CARD_LIST),
            ("0000:06:00.0", DISPLAY_CARD_TOTAL_MB))

    def test_a_line_with_no_free_figure_is_not_invented(self):
        """An engine whose line does not carry a free figure yields None for
        it, and the caller falls back rather than guessing."""
        pci, total, free = serving_device.pci_vram_and_free_for_device_name(
            "Vulkan9", list_output=TWO_CARD_LIST)
        self.assertIsNone(pci)
        self.assertIsNone(total)
        self.assertIsNone(free)


class WhichFigureThePlanIsWeighedAgainstTest(unittest.TestCase):
    """The one decision, as a function that can be asked directly."""

    def test_a_display_card_is_weighed_on_its_free_memory(self):
        mb, why = serving_device.memory_to_plan_against(
            total_mb=DISPLAY_CARD_TOTAL_MB, free_mb=DISPLAY_CARD_FREE_MB,
            drives_display=True)
        self.assertEqual(mb, DISPLAY_CARD_FREE_MB)
        self.assertIn("free", why)
        self.assertIn("display", why)

    def test_a_display_free_card_is_weighed_on_its_total(self):
        mb, why = serving_device.memory_to_plan_against(
            total_mb=SERVING_CARD_TOTAL_MB, free_mb=SERVING_CARD_FREE_MB,
            drives_display=False)
        self.assertEqual(mb, SERVING_CARD_TOTAL_MB)
        self.assertIn("total", why)
        self.assertIn("display-free", why)

    def test_an_unreadable_display_state_keeps_the_total(self):
        mb, why = serving_device.memory_to_plan_against(
            total_mb=DISPLAY_CARD_TOTAL_MB, free_mb=DISPLAY_CARD_FREE_MB,
            drives_display=None)
        self.assertEqual(mb, DISPLAY_CARD_TOTAL_MB)
        self.assertIn("total", why)
        self.assertIn("could not be read", why)

    def test_a_display_card_with_no_free_figure_keeps_the_total(self):
        mb, why = serving_device.memory_to_plan_against(
            total_mb=DISPLAY_CARD_TOTAL_MB, free_mb=None, drives_display=True)
        self.assertEqual(mb, DISPLAY_CARD_TOTAL_MB)
        self.assertIn("total", why)
        self.assertIn("no free figure", why)

    def test_the_words_never_state_a_figure_without_saying_whose_it_is(self):
        for kwargs in ({"drives_display": True},
                       {"drives_display": False},
                       {"drives_display": None}):
            _mb, why = serving_device.memory_to_plan_against(
                total_mb=DISPLAY_CARD_TOTAL_MB,
                free_mb=DISPLAY_CARD_FREE_MB, **kwargs)
            self.assertTrue(why.strip(), f"empty reason for {kwargs}")
            self.assertIn("card", why)


class TheDefectItselfTest(unittest.TestCase):
    """The measured case, end to end through the plan.

    This is the RED: at the base the plan is handed the total and declares a
    fit that the card cannot honour.
    """

    def _plan(self, vram_mb):
        return plan_offload(vram_mb=vram_mb, model_bytes=MODEL_BYTES,
                            projector_bytes=PROJECTOR_BYTES,
                            total_layers=TOTAL_LAYERS)

    def test_the_arithmetic_of_the_measured_case(self):
        self.assertEqual(self._plan(DISPLAY_CARD_TOTAL_MB).required_mb,
                         REQUIRED_MB)
        self.assertGreater(REQUIRED_MB, DISPLAY_CARD_FREE_MB)
        self.assertLess(REQUIRED_MB, DISPLAY_CARD_TOTAL_MB)

    def test_weighed_on_the_total_it_declares_a_fit(self):
        """Today's answer, kept as the thing being replaced."""
        self.assertIs(self._plan(DISPLAY_CARD_TOTAL_MB).fits, True)

    def test_weighed_on_the_free_figure_it_does_not(self):
        plan = self._plan(DISPLAY_CARD_FREE_MB)
        self.assertIs(plan.fits, False)
        self.assertLess(plan.layers, TOTAL_LAYERS)
        self.assertIn(str(DISPLAY_CARD_FREE_MB), plan.reason)

    def test_the_selection_and_the_decision_together(self):
        """The whole path: read the line, ask which figure, plan on it."""
        _pci, total, free = serving_device.pci_vram_and_free_for_device_name(
            "Vulkan0", list_output=TWO_CARD_LIST)
        mb, why = serving_device.memory_to_plan_against(
            total_mb=total, free_mb=free, drives_display=True)
        plan = self._plan(mb)
        self.assertIs(plan.fits, False)
        self.assertIn("free", why)


class TheSingleCardControlTest(unittest.TestCase):
    """A one-card machine must plan exactly as it did before."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="plan-free-single-")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))

    def test_no_pci_address_means_the_total_still_stands(self):
        sysfs = os.path.join(self.tmp, "sysfs")
        name, pci, total, free = (
            serving_device.select_serving_device_name_pci_vram_and_free(
                list_output=ONE_CARD_LIST_NO_PCI,
                discrete_vram_mb=DISPLAY_CARD_TOTAL_MB, sysfs_root=sysfs))
        self.assertEqual(name, "Vulkan0")
        self.assertIsNone(pci)
        self.assertEqual(total, DISPLAY_CARD_TOTAL_MB)
        self.assertEqual(free, DISPLAY_CARD_FREE_MB)
        mb, why = serving_device.memory_to_plan_against(
            total_mb=total, free_mb=free, drives_display=None)
        self.assertEqual(mb, DISPLAY_CARD_TOTAL_MB)
        self.assertIn("could not be read", why)


class TheDisplayFreeControlTest(unittest.TestCase):
    """The card this machine actually serves on is display-free, so its plan
    must not move at all."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="plan-free-displayfree-")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))

    def test_the_serving_card_is_still_weighed_on_its_total(self):
        sysfs = _sysfs_with(self.tmp, {"0000:06:00.0": True,
                                       "0000:0e:00.0": False})
        name, pci, total, free = (
            serving_device.select_serving_device_name_pci_vram_and_free(
                list_output=TWO_CARD_LIST,
                discrete_vram_mb=SERVING_CARD_TOTAL_MB, sysfs_root=sysfs))
        self.assertEqual(name, "Vulkan1")
        self.assertIs(serving_device._pci_drives_display(pci, sysfs), False)
        mb, why = serving_device.memory_to_plan_against(
            total_mb=total, free_mb=free,
            drives_display=serving_device._pci_drives_display(pci, sysfs))
        self.assertEqual(mb, SERVING_CARD_TOTAL_MB)
        self.assertIn("display-free", why)
        plan = plan_offload(vram_mb=mb, model_bytes=MODEL_BYTES,
                            projector_bytes=PROJECTOR_BYTES,
                            total_layers=TOTAL_LAYERS)
        self.assertIs(plan.fits, True)


class TheDaemonWiringTest(unittest.TestCase):
    """The daemon's start-up block must ask the function, not re-derive the
    choice inline, and must record which figure it used.

    This reads the source because standing the whole daemon up here would prove
    less and cost more; the RUNTIME proof is the installed daemon's own offload
    line, taken on a real machine and delivered with this lane.
    """

    def _source(self):
        from pathlib import Path
        import intergen.dbus_daemon as mod
        return Path(mod.__file__).read_text(encoding="utf-8")

    def test_it_asks_the_one_function(self):
        self.assertIn("memory_to_plan_against(", self._source())

    def test_the_trace_row_names_the_free_figure_and_the_display_state(self):
        src = self._source()
        self.assertIn('"pinned_card_free_mb": _device_free_mb', src)
        self.assertIn('"device_display": _device_display', src)

    def test_the_display_state_is_read_before_the_plan_is_made(self):
        """Ordering matters: the choice of figure depends on it."""
        src = self._source()
        self.assertLess(src.index("_device_display = "),
                        src.index("plan_for_model(vram_mb=_plan_vram_mb"))


if __name__ == "__main__":
    unittest.main()
