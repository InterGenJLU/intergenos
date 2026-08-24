# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The llama_server.gpu_layers contract after the bring-up audition was removed.

Decided 2026-07-31: an explicit integer is honoured verbatim and is the last
word. Nothing probes the GPU, times it, or caches a verdict about it.
Decided 2026-08-12: "auto" resolves to the detected hardware tier's serving
posture — Tier-1 hardware serves over CPU only, higher tiers offload every
layer.

These cases exist so the contract cannot drift back silently — including the
proof that the removed module and its state file are really gone.
"""
from __future__ import annotations

import importlib
import unittest

from intergen.llama_manager import (AUTO_GPU_LAYERS, OFFLOAD_ALL_LAYERS,
                                    resolve_gpu_layers)


class ResolveTests(unittest.TestCase):
    def test_auto_offloads_every_layer(self) -> None:
        self.assertEqual(resolve_gpu_layers(AUTO_GPU_LAYERS), OFFLOAD_ALL_LAYERS)
        self.assertEqual(resolve_gpu_layers("auto"), 999)

    def test_shipped_default_is_the_auto_sentinel(self) -> None:
        from intergen.config import Config
        cfg = Config()
        self.assertEqual(cfg.get("llama_server.gpu_layers"), AUTO_GPU_LAYERS)

    def test_explicit_zero_pins_the_cpu(self) -> None:
        # The user's pin is supreme — 0 must not be "helpfully" upgraded.
        self.assertEqual(resolve_gpu_layers(0), 0)

    def test_explicit_integer_is_verbatim(self) -> None:
        for value in (1, 17, 33, 999, 4096):
            self.assertEqual(resolve_gpu_layers(value), value)

    def test_a_quoted_integer_behaves_as_it_reads(self) -> None:
        self.assertEqual(resolve_gpu_layers("0"), 0)
        self.assertEqual(resolve_gpu_layers(" 33 "), 33)

    def test_unrecognised_values_offload_rather_than_pin_the_cpu(self) -> None:
        # A typo must not silently strand the user on the CPU.
        for value in (None, "", "yes", "全部", [], {}):
            self.assertEqual(resolve_gpu_layers(value), OFFLOAD_ALL_LAYERS)

    def test_booleans_are_not_read_as_layer_counts(self) -> None:
        # bool is an int subclass; `false` must not read as "pin the CPU".
        self.assertEqual(resolve_gpu_layers(False), OFFLOAD_ALL_LAYERS)
        self.assertEqual(resolve_gpu_layers(True), OFFLOAD_ALL_LAYERS)


class ExcisionTests(unittest.TestCase):
    """The removed gate is gone, not merely unreferenced."""

    def test_the_gate_module_no_longer_exists(self) -> None:
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("intergen.offload_gate")

    def test_the_daemon_imports_without_it(self) -> None:
        # A dangling import would surface here rather than at first boot.
        mod = importlib.import_module("intergen.dbus_daemon")
        self.assertFalse(hasattr(mod, "offload_gate"))

    def test_the_device_selector_survived_the_removal(self) -> None:
        # Multi-GPU device pinning was co-located with the gate but is a
        # separate feature the removal decision did not touch.
        from intergen.serving_device import select_serving_device
        listing = (
            "  Vulkan0: AMD Radeon Graphics (512 MiB, 400 MiB free)\n"
            "  Vulkan1: AMD Radeon PRO R9700 (32768 MiB, 32000 MiB free)\n"
        )
        self.assertEqual(
            select_serving_device(list_output=listing, discrete_vram_mb=32768),
            "Vulkan1")


class TierContractTests(unittest.TestCase):
    """Decided 2026-08-24: the MEASURED FIT governs the serving path, not the tier.

    This class asserted the opposite until 2026-08-24 — that a tier-1 machine
    serves over the processor whatever card it has. That rule was written for
    machines with no usable card, and it reached machines that had one: a card
    is called discrete at 3072 MB and the second model tier needs 7168 MB, so
    every card in between was detected, reported, assigned tier 1 and then
    served zero layers while the 1.8 GB model it was serving would have fitted.
    Measured in the field on a 4 GB card: four recorded starts, all at zero
    layers, replies taking 4 to 11 seconds.

    The tier still chooses WHICH model is served. How much of that model goes on
    the card is decided by :func:`intergen.gpu_offload.plan_offload` from the
    detected video memory and the model's size in the signed manifest. An
    explicit integer in config remains the last word (the 2026-07-31
    user-control contract, unchanged).
    """

    def test_the_tier_no_longer_decides_the_serving_path(self) -> None:
        for level in (1, 2, 3, None):
            self.assertNotEqual(resolve_gpu_layers(AUTO_GPU_LAYERS,
                                                   tier_level=level), 0)
            self.assertNotEqual(resolve_gpu_layers("auto", tier_level=level), 0)

    def test_auto_without_a_plan_offloads_every_layer(self) -> None:
        # A caller that measured nothing gets the offloading answer, not the
        # processor-only one: unknown is not the same as absent, and an
        # over-optimistic offload fails loudly at the engine while a needless
        # processor fallback fails where nobody can see it.
        for level in (1, 2, 3, None):
            self.assertEqual(resolve_gpu_layers("auto", tier_level=level),
                             OFFLOAD_ALL_LAYERS)
        self.assertEqual(resolve_gpu_layers("auto"), OFFLOAD_ALL_LAYERS)

    def test_auto_takes_the_measured_plan_when_one_is_supplied(self) -> None:
        from intergen.gpu_offload import plan_offload
        # A 4 GB card and the tier-1 model: fits, so every layer.
        fits = plan_offload(vram_mb=4096, model_bytes=1282436192,
                            projector_bytes=636106144, total_layers=29)
        self.assertEqual(resolve_gpu_layers("auto", tier_level=1, plan=fits),
                         OFFLOAD_ALL_LAYERS)
        # An 8 GB card and the 22 GB model: a partial count, not zero.
        partial = plan_offload(vram_mb=8192, model_bytes=22016023168,
                               projector_bytes=899283648, total_layers=48)
        layers = resolve_gpu_layers("auto", tier_level=3, plan=partial)
        self.assertGreater(layers, 0)
        self.assertLess(layers, 48)

    def test_explicit_pin_outranks_everything_on_every_tier(self) -> None:
        # The user's pin is supreme: written on a tier-1 box it offloads as
        # written; written 0 on a tier-3 box it pins the processor, and it does
        # so even when a plan says the model would fit.
        from intergen.gpu_offload import plan_offload
        fits = plan_offload(vram_mb=24576, model_bytes=1282436192,
                            projector_bytes=0, total_layers=29)
        self.assertEqual(resolve_gpu_layers(999, tier_level=1), 999)
        self.assertEqual(resolve_gpu_layers(17, tier_level=1), 17)
        self.assertEqual(resolve_gpu_layers(" 33 ", tier_level=1), 33)
        self.assertEqual(resolve_gpu_layers(0, tier_level=3), 0)
        self.assertEqual(resolve_gpu_layers(0, tier_level=3, plan=fits), 0)

    def test_non_pin_values_never_resolve_to_processor_only(self) -> None:
        # Anything that is not an explicit integer takes the plan, or the
        # offloading default when there is none. A typo must not quietly take a
        # machine's card off the serving path.
        for value in (None, "", "yes", "全部", [], {}, False, True):
            for level in (1, 2, 3):
                self.assertEqual(resolve_gpu_layers(value, tier_level=level),
                                 OFFLOAD_ALL_LAYERS)


if __name__ == "__main__":
    unittest.main()
