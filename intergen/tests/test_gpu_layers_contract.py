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
        # separate feature the ruling did not touch.
        from intergen.serving_device import select_serving_device
        listing = (
            "  Vulkan0: AMD Radeon Graphics (512 MiB, 400 MiB free)\n"
            "  Vulkan1: AMD Radeon PRO R9700 (32768 MiB, 32000 MiB free)\n"
        )
        self.assertEqual(
            select_serving_device(list_output=listing, discrete_vram_mb=32768),
            "Vulkan1")


class TierContractTests(unittest.TestCase):
    """Decided 2026-08-12: the hardware tier governs the serving path.

    Tier-1 hardware serves inference over CPU only — the 2B model tier is
    sized for CPU serving, and a Tier-1 GPU is never an inference device by
    default. Higher tiers offload as before. An explicit integer in config
    remains the last word on every tier (the 2026-07-31 user-control
    contract, unchanged).
    """

    def test_auto_on_tier_1_pins_the_cpu(self) -> None:
        self.assertEqual(resolve_gpu_layers(AUTO_GPU_LAYERS, tier_level=1), 0)
        self.assertEqual(resolve_gpu_layers("auto", tier_level=1), 0)

    def test_auto_on_higher_tiers_offloads_every_layer(self) -> None:
        for level in (2, 3):
            self.assertEqual(resolve_gpu_layers("auto", tier_level=level),
                             OFFLOAD_ALL_LAYERS)

    def test_unknown_tier_keeps_the_offload_default(self) -> None:
        # Failed/absent detection follows the daemon's existing
        # assume-Tier-2 precedent rather than inventing a third posture.
        self.assertEqual(resolve_gpu_layers("auto", tier_level=None),
                         OFFLOAD_ALL_LAYERS)
        self.assertEqual(resolve_gpu_layers("auto"), OFFLOAD_ALL_LAYERS)

    def test_explicit_pin_outranks_the_tier_on_every_tier(self) -> None:
        # The user's pin is supreme: written on a Tier-1 box it offloads as
        # written; written 0 on a Tier-3 box it pins the CPU.
        self.assertEqual(resolve_gpu_layers(999, tier_level=1), 999)
        self.assertEqual(resolve_gpu_layers(17, tier_level=1), 17)
        self.assertEqual(resolve_gpu_layers(" 33 ", tier_level=1), 33)
        self.assertEqual(resolve_gpu_layers(0, tier_level=3), 0)

    def test_non_pin_values_resolve_to_the_tier_default(self) -> None:
        # Anything that is not an explicit integer takes the tier's default:
        # CPU on Tier 1, full offload above it. A typo on a Tier-1 box must
        # not put its GPU back on the serving path.
        for value in (None, "", "yes", "全部", [], {}, False, True):
            self.assertEqual(resolve_gpu_layers(value, tier_level=1), 0)
            self.assertEqual(resolve_gpu_layers(value, tier_level=2),
                             OFFLOAD_ALL_LAYERS)


if __name__ == "__main__":
    unittest.main()
