# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""When nothing else read the card, ask the engine that is about to launch.

WHAT WAS MEASURED, three times — on an installed single-GPU laptop under
intergen r269, r285 and r287 (2026-09-19), the launch path logged, verbatim:

    offload: llama_server.gpu_layers='auto' (tier 1, card unreadable MiB from
    no card pinned) -> 999 layers, engine vulkan; video memory could not be
    read, so whether the model fits is unknown; offloading rather than serving
    on the processor without having measured anything

Four seconds later, in the same start, the engine printed its own reading of
the very same card:

    using device Vulkan0 (Intel(R) UHD Graphics (ICL GT1)) - 10584 MiB free

and ``llama-server --list-devices`` on that machine prints one line carrying
both figures. The number the plan said could not be read was available to the
process that was making the plan.

WHY THE PLAN HAD NOTHING. The automatic serving-device selection exists to pick
ONE card on a multi-card box; on a machine with a single integrated GPU it
correctly pins nothing, and the card's two memory figures come out of that same
selection — so no pin meant no figures. The hardware detector reports no
dedicated video memory for an integrated GPU either. Both honest; between them
the plan was left deciding blind.

THE RULE THIS ADDS, and nothing more:

  * a pinned card whose size the engine reported is weighed exactly as before;
  * a detected figure is used exactly as before;
  * only when NEITHER is available does the plan ask the chosen engine for its
    own device list, and then only when that list names EXACTLY ONE device —
    with no pin and several cards llama.cpp spreads the model across all of
    them, so no single card's figure describes the memory the model goes into;
  * which of that one card's two figures is used is decided by the same
    function that decides it for a pinned card, so there is still ONE place
    that chooses between a total and a free figure;
  * an unreadable list, or one naming no device, keeps today's honest
    "could not be read" plan. The fallback adds a measurement; it never
    invents one.
"""

from __future__ import annotations

import ast
import inspect
import os
import tempfile
import unittest

from intergen import dbus_daemon, serving_device
from intergen.gpu_offload import plan_offload

MIB = 1024 * 1024

# The machine the finding was measured on: one integrated GPU, painting the
# desktop, reported by the engine's own device list.
IGPU_PCI = "0000:00:02.0"
IGPU_TOTAL_MB = 11760
IGPU_FREE_MB = 6053
ONE_DEVICE_LIST = (
    "Available devices:\n"
    f"  Vulkan0: Intel(R) UHD Graphics (ICL GT1) ({IGPU_TOTAL_MB} MiB, "
    f"{IGPU_FREE_MB} MiB free) [PCI {IGPU_PCI}]\n")

# The same machine's engine build without the in-tree list-devices patch: a
# device line with no PCI address, so the display state is unknowable.
ONE_DEVICE_LIST_NO_PCI = (
    "Available devices:\n"
    f"  Vulkan0: Intel(R) UHD Graphics (ICL GT1) ({IGPU_TOTAL_MB} MiB, "
    f"{IGPU_FREE_MB} MiB free)\n")

TWO_DEVICE_LIST = (
    "Available devices:\n"
    "  Vulkan0: AMD Radeon RX 7600 (8176 MiB, 6842 MiB free) "
    "[PCI 0000:06:00.0]\n"
    "  Vulkan1: AMD Radeon PRO W7800 (20464 MiB, 20281 MiB free) "
    "[PCI 0000:0e:00.0]\n")

NO_DEVICE_LIST = "Available devices:\n"

# The shipped model on that machine, with its vision projector.
MODEL_BYTES = 1282436192
PROJECTOR_BYTES = 636106144
TOTAL_LAYERS = 29
REQUIRED_MB = 2917          # 1223 weights + 606 projector + 1088 reserve


def _sysfs_with(tmp: str, cards: dict) -> str:
    """A kernel-records tree the display check can read; see
    :func:`serving_device._pci_drives_display` for the two places it reads."""
    root = os.path.join(tmp, "sysfs")
    for i, (pci, connected) in enumerate(sorted(cards.items())):
        card = f"card{i}"
        os.makedirs(os.path.join(root, "bus", "pci", "devices", pci, "drm",
                                 card), exist_ok=True)
        conn = os.path.join(root, "class", "drm", f"{card}-eDP-1")
        os.makedirs(conn, exist_ok=True)
        with open(os.path.join(conn, "status"), "w", encoding="utf-8") as fh:
            fh.write("connected\n" if connected else "disconnected\n")
    return root


class TheEngineListCanBeAskedForItsOneDeviceTest(unittest.TestCase):

    def test_one_device_is_returned_whole(self):
        sole = serving_device.sole_reported_device(
            list_output=ONE_DEVICE_LIST)
        self.assertEqual(sole, ("Vulkan0", IGPU_PCI, IGPU_TOTAL_MB,
                                IGPU_FREE_MB))

    def test_a_line_without_a_pci_address_still_yields_its_figures(self):
        sole = serving_device.sole_reported_device(
            list_output=ONE_DEVICE_LIST_NO_PCI)
        self.assertEqual(sole, ("Vulkan0", None, IGPU_TOTAL_MB, IGPU_FREE_MB))

    def test_two_devices_yield_nothing(self):
        self.assertIsNone(serving_device.sole_reported_device(
            list_output=TWO_DEVICE_LIST))

    def test_no_device_yields_nothing(self):
        self.assertIsNone(serving_device.sole_reported_device(
            list_output=NO_DEVICE_LIST))

    def test_an_unreadable_enumeration_yields_nothing(self):
        self.assertIsNone(serving_device.sole_reported_device(
            server="/nonexistent/llama-server-that-is-not-here"))


class WhichFigureTheLaunchPathTakesTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="plan-engine-list-")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))

    def test_a_pinned_card_with_a_size_is_weighed_exactly_as_before(self):
        mb, why = serving_device.memory_for_the_offload_plan(
            device_name="Vulkan1", device_total_mb=20464,
            device_free_mb=20281, device_drives_display=False,
            detected_vram_mb=8176, server="/nonexistent/llama-server")
        self.assertEqual(mb, 20464)
        self.assertEqual(why, serving_device.memory_to_plan_against(
            total_mb=20464, free_mb=20281, drives_display=False)[1])

    def test_a_detected_figure_is_used_exactly_as_before(self):
        mb, why = serving_device.memory_for_the_offload_plan(
            device_name=None, device_total_mb=None, device_free_mb=None,
            device_drives_display=None, detected_vram_mb=32624,
            server="/nonexistent/llama-server")
        self.assertEqual(mb, 32624)
        self.assertEqual(why, "no card pinned")

    def test_the_defect_case_takes_the_engines_own_free_figure(self):
        sysfs = _sysfs_with(self.tmp, {IGPU_PCI: True})
        mb, why = serving_device.memory_for_the_offload_plan(
            device_name=None, device_total_mb=None, device_free_mb=None,
            device_drives_display=None, detected_vram_mb=None,
            server="/usr/bin/llama-server", list_output=ONE_DEVICE_LIST,
            sysfs_root=sysfs)
        self.assertEqual(mb, IGPU_FREE_MB)
        self.assertIn("Vulkan0", why)
        self.assertIn("only card", why)
        self.assertIn("free", why)

    def test_a_display_free_sole_card_is_weighed_on_its_total(self):
        sysfs = _sysfs_with(self.tmp, {IGPU_PCI: False})
        mb, why = serving_device.memory_for_the_offload_plan(
            device_name=None, device_total_mb=None, device_free_mb=None,
            device_drives_display=None, detected_vram_mb=None,
            server="/usr/bin/llama-server", list_output=ONE_DEVICE_LIST,
            sysfs_root=sysfs)
        self.assertEqual(mb, IGPU_TOTAL_MB)
        self.assertIn("total", why)
        self.assertIn("Vulkan0", why)

    def test_an_unknowable_display_state_keeps_the_total(self):
        mb, why = serving_device.memory_for_the_offload_plan(
            device_name=None, device_total_mb=None, device_free_mb=None,
            device_drives_display=None, detected_vram_mb=None,
            server="/usr/bin/llama-server",
            list_output=ONE_DEVICE_LIST_NO_PCI, sysfs_root=self.tmp)
        self.assertEqual(mb, IGPU_TOTAL_MB)
        self.assertIn("total", why)

    def test_several_cards_and_no_pin_keep_todays_honest_unknown(self):
        mb, why = serving_device.memory_for_the_offload_plan(
            device_name=None, device_total_mb=None, device_free_mb=None,
            device_drives_display=None, detected_vram_mb=None,
            server="/usr/bin/llama-server", list_output=TWO_DEVICE_LIST)
        self.assertIsNone(mb)
        self.assertEqual(why, "no card pinned")

    def test_an_unreadable_list_keeps_todays_honest_unknown(self):
        mb, why = serving_device.memory_for_the_offload_plan(
            device_name=None, device_total_mb=None, device_free_mb=None,
            device_drives_display=None, detected_vram_mb=None,
            server="/nonexistent/llama-server-that-is-not-here")
        self.assertIsNone(mb)
        self.assertEqual(why, "no card pinned")

    def test_no_engine_at_all_keeps_todays_honest_unknown(self):
        mb, why = serving_device.memory_for_the_offload_plan(
            device_name="Vulkan3", device_total_mb=None, device_free_mb=None,
            device_drives_display=None, detected_vram_mb=None, server=None)
        self.assertIsNone(mb)
        self.assertEqual(why, "the pinned card's size was not reported")

    def test_the_words_always_name_a_card(self):
        sysfs = _sysfs_with(self.tmp, {IGPU_PCI: True})
        for kwargs in (
                {"device_total_mb": 20464, "device_free_mb": 20281,
                 "device_drives_display": True, "detected_vram_mb": None},
                {"device_total_mb": None, "device_free_mb": None,
                 "device_drives_display": None, "detected_vram_mb": None}):
            _mb, why = serving_device.memory_for_the_offload_plan(
                device_name=None, server="/usr/bin/llama-server",
                list_output=ONE_DEVICE_LIST, sysfs_root=sysfs, **kwargs)
            self.assertIn("card", why, kwargs)


class TheLaunchPathAsksThroughThatOneFunctionTest(unittest.TestCase):
    """The daemon's launch path must not keep a second copy of the decision."""

    def test_the_daemon_calls_it(self):
        tree = ast.parse(inspect.getsource(dbus_daemon))
        names = {getattr(n.func, "id", None) or getattr(n.func, "attr", None)
                 for n in ast.walk(tree) if isinstance(n, ast.Call)}
        self.assertIn("memory_for_the_offload_plan", names)

    def test_the_daemon_no_longer_decides_it_itself(self):
        tree = ast.parse(inspect.getsource(dbus_daemon))
        names = [getattr(n.func, "id", None) or getattr(n.func, "attr", None)
                 for n in ast.walk(tree) if isinstance(n, ast.Call)]
        self.assertEqual(
            [n for n in names if n == "memory_to_plan_against"], [],
            "the launch path calls memory_to_plan_against beside the one "
            "function that is supposed to own the whole decision")


class ThePlanTheMeasuredMachineGetsTest(unittest.TestCase):

    def _plan(self, vram_mb):
        return plan_offload(vram_mb=vram_mb, model_bytes=MODEL_BYTES,
                            projector_bytes=PROJECTOR_BYTES,
                            total_layers=TOTAL_LAYERS)

    def test_the_arithmetic_of_the_measured_case(self):
        self.assertEqual(self._plan(IGPU_FREE_MB).required_mb, REQUIRED_MB)

    def test_todays_plan_says_it_could_not_be_read(self):
        """The thing being replaced, kept so the change is visible."""
        plan = self._plan(None)
        self.assertIsNone(plan.fits)
        self.assertIn("could not be read", plan.reason)

    def test_the_engines_figure_turns_it_into_a_measured_fit(self):
        plan = self._plan(IGPU_FREE_MB)
        self.assertIs(plan.fits, True)
        self.assertNotIn("could not be read", plan.reason)
        self.assertIn(str(IGPU_FREE_MB), plan.reason)
        self.assertIn(str(REQUIRED_MB), plan.reason)

    def test_the_same_layer_count_is_reached_by_a_measurement(self):
        """On this machine the answer does not change — only its grounds do."""
        from intergen.llama_manager import resolve_gpu_layers
        blind = resolve_gpu_layers("auto", tier_level=1, plan=self._plan(None))
        measured = resolve_gpu_layers("auto", tier_level=1,
                                      plan=self._plan(IGPU_FREE_MB))
        self.assertEqual(blind, measured)

    def test_a_card_too_small_for_the_model_now_gets_a_partial_plan(self):
        """And where the answer DOES change, it changes because something was
        measured: a card with less free memory than the model needs no longer
        takes every layer blind."""
        plan = self._plan(2048)
        self.assertIs(plan.fits, False)
        self.assertLess(plan.layers, TOTAL_LAYERS)


if __name__ == "__main__":
    unittest.main()
