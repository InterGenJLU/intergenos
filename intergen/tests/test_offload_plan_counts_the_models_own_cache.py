# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The offload plan counts the served model's own key/value cache at the configured context.

THE DEFECT THIS PINS. The plan reserved one measured constant beyond the weights for the key/value cache and the
engine's compute buffers: 1088 MiB, measured on the 9B model, whose cache is 512 MiB at the shipped 16384-cell
context. The 2B model served on a 4 GB card has a cache of 1792 MiB at that context, so the plan called the load
a fit and put every layer on the card; the engine then ran out of device memory allocating the vision projector
and aborted, at every start. Measured on such a card: the weights 1050 MiB, the cache 1792 MiB and the compute
buffer 301 MiB on the card, then the projector's allocation failing.

THE RULE THESE CELLS HOLD. When the model's header says how big its cache is, the plan charges that cache at the
configured context, a reserve for the compute buffers and fixed state, and the free margin the engine keeps when
it fits a model itself, and it charges each layer its weights and its share of the cache; the module's three
outcomes are unchanged. When the header does not say, the measured constant stands, as before.

Hermetic: the header figures are given as data or read from a header these cells write; nothing loads a model,
starts an engine or touches a card.
"""

from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from intergen import gpu_offload

MIB = 1024 * 1024

# The 2B model's own header, as the engine and the GGUF reader see it (qwen3 architecture).
QWEN3_2B_HEADER = {
    "general.architecture": "qwen3",
    "qwen3.block_count": 28,
    "qwen3.attention.head_count": 16,
    "qwen3.attention.head_count_kv": 8,
    "qwen3.attention.key_length": 128,
    "qwen3.attention.value_length": 128,
    "qwen3.embedding_length": 2048,
}
# The shipped files' sizes for that model (the signed manifest's figures).
MODEL_BYTES_2B = 1282436192
PROJECTOR_BYTES_2B = 636106144
UNITS_2B = 29                    # 28 blocks and the output layer

CUDA_CARD_MB = 3714              # the 4 GB card as its CUDA build reports it
VULKAN_CARD_MB = 4342            # the same card as its Vulkan build reports it


def _kv(header, context):
    function = getattr(gpu_offload, "kv_cache_mb", None)
    if function is None:
        raise AssertionError("gpu_offload offers no reading of the model's own key/value cache")
    return function(header, context)


class TheModelsOwnCacheIsRead(unittest.TestCase):

    def test_the_2b_models_cache_at_the_shipped_context_is_1792_mib(self) -> None:
        self.assertEqual(_kv(QWEN3_2B_HEADER, 16384), 1792,
                         "the engine itself allocates 1792 MiB of cache for this model at 16384 cells")

    def test_the_cache_is_linear_in_the_context(self) -> None:
        self.assertEqual(_kv(QWEN3_2B_HEADER, 4096), 448)

    def test_a_hybrid_model_charges_only_its_attention_layers(self) -> None:
        header = {"general.architecture": "hyb", "hyb.block_count": 32,
                  "hyb.full_attention_interval": 4, "hyb.attention.head_count_kv": 4,
                  "hyb.attention.key_length": 256, "hyb.attention.value_length": 256}
        self.assertEqual(_kv(header, 16384), 512,
                         "8 of 32 blocks carry a cache: 8 x 16384 x 4 x 512 x 2 bytes")

    def test_a_recurrent_hybrid_that_does_not_say_which_layers_attend_is_unknown(self) -> None:
        header = {"general.architecture": "hyb", "hyb.block_count": 32,
                  "hyb.ssm.state_size": 128, "hyb.attention.head_count_kv": 4,
                  "hyb.attention.key_length": 256, "hyb.attention.value_length": 256}
        self.assertIsNone(_kv(header, 16384),
                          "charging every block a cache would be a figure the header never gave")

    def test_widths_fall_back_to_the_embedding_over_the_heads(self) -> None:
        header = {k: v for k, v in QWEN3_2B_HEADER.items()
                  if not k.endswith(("key_length", "value_length"))}
        header["qwen3.embedding_length"] = 4096
        header["qwen3.attention.head_count"] = 32
        self.assertEqual(_kv(header, 16384), 1792)

    def test_a_header_without_the_figures_says_it_does_not_know(self) -> None:
        for missing in ("qwen3.block_count", "qwen3.attention.head_count_kv",
                        "general.architecture"):
            with self.subTest(missing=missing):
                header = {k: v for k, v in QWEN3_2B_HEADER.items() if k != missing}
                self.assertIsNone(_kv(header, 16384))
        self.assertIsNone(_kv(None, 16384))
        self.assertIsNone(_kv(QWEN3_2B_HEADER, None))
        self.assertIsNone(_kv(QWEN3_2B_HEADER, 0))


class ThePlanChargesTheCache(unittest.TestCase):

    def _plan(self, vram_mb, kv_mb):
        try:
            return gpu_offload.plan_offload(
                vram_mb=vram_mb, model_bytes=MODEL_BYTES_2B,
                projector_bytes=PROJECTOR_BYTES_2B, total_layers=UNITS_2B,
                kv_mb=kv_mb, context_size=16384)
        except TypeError as exc:
            raise AssertionError(f"the plan cannot be given the model's own cache: {exc}") from exc

    def test_the_4_gb_card_gets_the_layers_that_fit_not_every_layer(self) -> None:
        for card in (CUDA_CARD_MB, VULKAN_CARD_MB):
            with self.subTest(card=card):
                plan = self._plan(card, 1792)
                self.assertIs(plan.fits, False, plan.reason)
                self.assertGreater(plan.layers, 0, plan.reason)
                self.assertLess(plan.layers, UNITS_2B, plan.reason)
                self.assertIn("1792 MiB of key/value cache", plan.reason)

    def test_the_layers_charged_with_their_cache_fit_the_card(self) -> None:
        plan = self._plan(CUDA_CARD_MB, 1792)
        per_layer = MODEL_BYTES_2B / MIB / UNITS_2B + 1792 / 28
        used = (plan.layers * per_layer + PROJECTOR_BYTES_2B // MIB
                + gpu_offload.COMPUTE_AND_STATE_RESERVE_MB
                + gpu_offload.ENGINE_FREE_MEMORY_TARGET_MB)
        self.assertLessEqual(used, CUDA_CARD_MB)

    def test_a_large_card_still_takes_every_layer(self) -> None:
        plan = self._plan(24576, 1792)
        self.assertIs(plan.fits, True, plan.reason)
        self.assertEqual(plan.layers, gpu_offload.OFFLOAD_ALL_LAYERS)

    def test_a_card_too_small_for_one_layer_is_the_processor_with_a_reason(self) -> None:
        plan = self._plan(2048, 1792)
        self.assertEqual(plan.layers, 0)
        self.assertIn("processor", plan.reason)

    def test_the_9b_models_own_figures_meet_the_measured_constant(self) -> None:
        self.assertEqual(512 + gpu_offload.COMPUTE_AND_STATE_RESERVE_MB,
                         gpu_offload.KV_AND_COMPUTE_HEADROOM_MB,
                         "the 9B's own cache and the reserve add up to the measured 1088 MiB")

    def test_without_the_cache_the_measured_constant_stands(self) -> None:
        plan = gpu_offload.plan_offload(
            vram_mb=CUDA_CARD_MB, model_bytes=MODEL_BYTES_2B,
            projector_bytes=PROJECTOR_BYTES_2B, total_layers=UNITS_2B)
        self.assertEqual(plan.layers, gpu_offload.OFFLOAD_ALL_LAYERS,
                         "a model whose cache cannot be read keeps today's plan")


def _write_header(path: Path, header: dict) -> None:
    """A GGUF version 3 header holding ``header``'s string and uint32 keys, no tensors."""
    def string(text: str) -> bytes:
        data = text.encode()
        return struct.pack("<Q", len(data)) + data
    body = b""
    for key, value in header.items():
        if isinstance(value, str):
            body += string(key) + struct.pack("<I", 8) + string(value)
        else:
            body += string(key) + struct.pack("<I", 4) + struct.pack("<I", value)
    path.write_bytes(b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0)
                     + struct.pack("<Q", len(header)) + body)


class ThePlanForTheServedModelReadsItsHeader(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.model = Path(self._tmp.name) / "OpenGVLab_InternVL3_5-2B-Q4_K_M.gguf"
        _write_header(self.model, QWEN3_2B_HEADER)
        sizes = {self.model.name: MODEL_BYTES_2B,
                 "mmproj-OpenGVLab_InternVL3_5-2B-f16.gguf": PROJECTOR_BYTES_2B}
        patch = mock.patch.object(gpu_offload, "_manifest_sizes_by_filename",
                                  return_value=sizes)
        patch.start()
        self.addCleanup(patch.stop)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _plan(self, **kwargs):
        try:
            return gpu_offload.plan_for_model(
                vram_mb=CUDA_CARD_MB, model_path=str(self.model),
                mmproj_path="mmproj-OpenGVLab_InternVL3_5-2B-f16.gguf", **kwargs)
        except TypeError as exc:
            raise AssertionError(f"the plan cannot be given the context: {exc}") from exc

    def test_the_daemons_own_call_plans_the_layers_that_fit(self) -> None:
        """The launch path's call exactly as it is made: the card, the model, the projector, nothing else."""
        from intergen.config import Config
        real_get = Config.get

        def get(self, key, default=None):
            if key == "llm.context_size":
                return 16384
            return real_get(self, key, default)

        with mock.patch.object(Config, "get", get):
            plan = gpu_offload.plan_for_model(
                vram_mb=CUDA_CARD_MB, model_path=str(self.model),
                mmproj_path="mmproj-OpenGVLab_InternVL3_5-2B-f16.gguf")
        self.assertNotEqual(plan.layers, gpu_offload.OFFLOAD_ALL_LAYERS,
                            f"every layer was planned onto a card the load does not fit: {plan.reason}")
        self.assertGreater(plan.layers, 0, plan.reason)

    def test_the_served_model_is_planned_with_its_own_cache(self) -> None:
        plan = self._plan(context_size=16384)
        self.assertIs(plan.fits, False, plan.reason)
        self.assertGreater(plan.layers, 0)
        self.assertIn("1792 MiB of key/value cache at 16384 cells", plan.reason)

    def test_without_a_context_the_configured_one_is_read(self) -> None:
        from intergen.config import Config
        real_get = Config.get

        def get(self, key, default=None):
            if key == "llm.context_size":
                return 4096
            return real_get(self, key, default)

        with mock.patch.object(Config, "get", get):
            plan = self._plan()
        self.assertIn("448 MiB of key/value cache at 4096 cells", plan.reason)


if __name__ == "__main__":
    unittest.main()
