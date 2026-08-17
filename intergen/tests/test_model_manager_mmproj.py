# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""mmproj / capability-descriptor manifest schema (InternVL swap Ph2, step 3a).

The SIGNED models-manifest is the authority for the pin (LLM GGUF + the paired
mmproj projector), the license_ref the acceptance gate keys on, and the declared
capability (has_vision / cacheable / mmproj_filename) that drives the launch
flags. ModelManager._apply_manifest overlays those onto a ModelInfo; _load_pins
now pins BOTH artifacts so verify_model covers the projector too.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from intergen.interfaces.types import HardwareTierLevel
from intergen.model_manager import (
    ModelManager,
    _load_manifest_entries,
    _pins_from_entries,
)

# Catalog filenames (must match MODEL_CATALOG) so the overlay/cap logic resolves.
INTERNVL_GGUF = "OpenGVLab_InternVL3_5-2B-Q4_K_M.gguf"
INTERNVL_MMPROJ = "mmproj-OpenGVLab_InternVL3_5-2B-f16.gguf"
QWEN9B_GGUF = "Qwen3.5-9B-intergen-round3-Q4_K_M.gguf"

_SHA_GGUF = "a" * 64
_SHA_MMPROJ = "b" * 64
_SHA_QWEN9B = "c" * 64


def _fixture_manifest() -> dict:
    return {
        "version": "0.1",
        "entries": [
            {
                "name": "InternVL3.5-2B",
                "filename": INTERNVL_GGUF,
                "repo_id": "bartowski/OpenGVLab_InternVL3_5-2B-GGUF",
                "quant": "Q4_K_M",
                "size_bytes": 1288490188,
                "sha256": _SHA_GGUF,
                "tier": 1,
                "license_ref": "Apache-2.0",
                "has_vision": True,
                "cacheable": True,
                "mmproj_filename": INTERNVL_MMPROJ,
                "mmproj_sha256": _SHA_MMPROJ,
                "mmproj_size_bytes": 666894336,
            },
            {   # a non-vision entry — has_vision/mmproj must stay falsy/None
                "name": "Qwen3.5-9B",
                "filename": QWEN9B_GGUF,
                "repo_id": "unsloth/Qwen3.5-9B-GGUF",
                "quant": "Q4_K_M",
                "size_bytes": 5680522464,
                "sha256": _SHA_QWEN9B,
                "tier": 2,
                "license_ref": "LicenseRef-Tongyi-Qianwen",
            },
        ],
        "signing": {"fingerprint": "DEADBEEF", "signature_path": "/x.asc"},
    }


class ManifestLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.pins = Path(self._tmp.name) / "models-manifest.json"
        self.pins.write_text(json.dumps(_fixture_manifest()))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_entries_keyed_by_filename(self):
        entries = _load_manifest_entries(self.pins)
        self.assertIn(INTERNVL_GGUF, entries)
        self.assertEqual(entries[INTERNVL_GGUF]["license_ref"], "Apache-2.0")

    def test_pins_cover_both_artifacts(self):
        pins = _pins_from_entries(_load_manifest_entries(self.pins))
        # The LLM GGUF AND the paired mmproj are both pinned (verify covers both).
        self.assertEqual(pins.get(INTERNVL_GGUF), _SHA_GGUF)
        self.assertEqual(pins.get(INTERNVL_MMPROJ), _SHA_MMPROJ)

    def test_empty_sha_is_not_pinned(self):
        m = _fixture_manifest()
        m["entries"][0]["sha256"] = ""          # staging placeholder
        m["entries"][0]["mmproj_sha256"] = ""
        pins = _pins_from_entries({e["filename"]: e for e in m["entries"]})
        self.assertNotIn(INTERNVL_GGUF, pins)    # fail-closed
        self.assertNotIn(INTERNVL_MMPROJ, pins)

    def test_missing_manifest_is_empty(self):
        self.assertEqual(_load_manifest_entries(Path("/no/such/manifest.json")), {})


class OverlayTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.pins = Path(self._tmp.name) / "models-manifest.json"
        self.pins.write_text(json.dumps(_fixture_manifest()))
        self.mm = ModelManager(
            model_dir=Path(self._tmp.name) / "llm",
            manifest_path=Path(self._tmp.name) / "m.json",
            pins_path=self.pins,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_tier1_surfaces_vision_capability(self):
        m = self.mm.get_model_for_tier(HardwareTierLevel.TIER_1)
        self.assertEqual(m.filename, INTERNVL_GGUF)   # pinned → not capped away
        self.assertEqual(m.sha256, _SHA_GGUF)
        self.assertEqual(m.license_ref, "Apache-2.0")
        self.assertTrue(m.has_vision)
        self.assertTrue(m.cacheable)
        self.assertEqual(m.mmproj_filename, INTERNVL_MMPROJ)
        self.assertEqual(m.mmproj_sha256, _SHA_MMPROJ)
        self.assertGreater(m.mmproj_size_gb, 0.0)

    def test_non_vision_entry_has_no_projector(self):
        m = self.mm.get_model_for_tier(HardwareTierLevel.TIER_2)
        self.assertEqual(m.filename, QWEN9B_GGUF)
        self.assertFalse(m.has_vision)
        self.assertFalse(m.cacheable)
        self.assertIsNone(m.mmproj_filename)
        self.assertEqual(m.mmproj_sha256, "")
        self.assertEqual(m.license_ref, "LicenseRef-Tongyi-Qianwen")

    def test_unknown_filename_grants_no_capability(self):
        # An unlisted filename gets NO declared capability (authoritative), but
        # its sha is left to the pin-gate's fill-if-empty contract (a caller pin
        # survives — the legacy two-source-fetch behavior).
        from intergen.interfaces.types import ModelInfo
        ghost = ModelInfo(
            name="ghost", filename="not-in-manifest.gguf",
            repo_id="x/y", quant="Q4_K_M", size_gb=1.0, sha256="deadbeef",
            tier=HardwareTierLevel.TIER_1, has_vision=True, cacheable=True,
        )
        self.mm._apply_manifest(ghost)
        self.assertEqual(ghost.sha256, "deadbeef")   # caller pin preserved
        self.assertFalse(ghost.has_vision)           # capability denied
        self.assertFalse(ghost.cacheable)
        self.assertIsNone(ghost.mmproj_filename)


class DownloadMmprojTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.pins = Path(self._tmp.name) / "models-manifest.json"
        self.pins.write_text(json.dumps(_fixture_manifest()))
        self.store = Path(self._tmp.name) / "llm"
        self.store.mkdir(parents=True)
        self.mm = ModelManager(
            model_dir=self.store,
            manifest_path=Path(self._tmp.name) / "m.json",
            pins_path=self.pins,
        )
        # InternVL (vision) with the paired projector pinned via the fixture.
        self.vision = self.mm.get_model_for_tier(HardwareTierLevel.TIER_1)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_non_vision_is_noop(self):
        from intergen.interfaces.types import ModelInfo
        plain = ModelInfo(
            name="x", filename="x.gguf", repo_id="a/b", quant="Q4_K_M",
            size_gb=1.0, sha256="d" * 64, tier=HardwareTierLevel.TIER_1,
        )
        with mock.patch.object(self.mm, "_fetch_and_verify") as fv:
            self.assertTrue(self.mm._download_paired_mmproj(plain))
            fv.assert_not_called()

    def test_has_vision_unpinned_fail_closed(self):
        self.vision.mmproj_sha256 = ""   # declared vision but no pin
        with mock.patch.object(self.mm, "_fetch_and_verify") as fv:
            self.assertFalse(self.mm._download_paired_mmproj(self.vision))
            fv.assert_not_called()       # never reaches the network

    def test_mirror_first_success(self):
        with mock.patch.object(self.mm, "_fetch_and_verify",
                               return_value=True) as fv:
            self.assertTrue(self.mm._download_paired_mmproj(self.vision))
            url0 = fv.call_args_list[0].args[0]
            self.assertIn("repo.intergenos.org/models", url0)  # mirror FIRST
            self.assertIn(INTERNVL_MMPROJ, url0)
            self.assertEqual(self.vision.mmproj_local_path,
                             str(self.store / INTERNVL_MMPROJ))

    def test_hf_fallback(self):
        with mock.patch.object(self.mm, "_fetch_and_verify",
                               side_effect=[False, True]) as fv:
            self.assertTrue(self.mm._download_paired_mmproj(self.vision))
            self.assertEqual(fv.call_count, 2)
            self.assertIn("huggingface.co", fv.call_args_list[1].args[0])

    def test_both_sources_fail_closed(self):
        with mock.patch.object(self.mm, "_fetch_and_verify", return_value=False):
            self.assertFalse(self.mm._download_paired_mmproj(self.vision))
            self.assertIsNone(self.vision.mmproj_local_path)

    def test_existing_verified_skips_fetch(self):
        (self.store / INTERNVL_MMPROJ).write_bytes(b"x")
        with mock.patch.object(self.mm, "verify_model", return_value=True), \
                mock.patch.object(self.mm, "_fetch_and_verify") as fv:
            self.assertTrue(self.mm._download_paired_mmproj(self.vision))
            fv.assert_not_called()
            self.assertEqual(self.vision.mmproj_local_path,
                             str(self.store / INTERNVL_MMPROJ))


class MmprojDiskDerivationTests(unittest.TestCase):
    """A FRESH lookup must re-derive mmproj_local_path from disk — it's set only
    on download/install, so an already-installed vision model would otherwise
    return None and the launch guard would refuse a model whose projector is
    actually present (or, worse, serve text-only)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.pins = Path(self._tmp.name) / "models-manifest.json"
        self.pins.write_text(json.dumps(_fixture_manifest()))
        self.store = Path(self._tmp.name) / "llm"
        self.store.mkdir(parents=True)
        self.mm = ModelManager(
            model_dir=self.store,
            manifest_path=Path(self._tmp.name) / "m.json",
            pins_path=self.pins,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_tier_lookup_derives_projector_from_disk(self):
        (self.store / INTERNVL_MMPROJ).write_bytes(b"proj")
        m = self.mm.get_model_for_tier(HardwareTierLevel.TIER_1)
        self.assertTrue(m.has_vision)
        self.assertEqual(m.mmproj_local_path,
                         str(self.store / INTERNVL_MMPROJ))

    def test_tier_lookup_projector_none_when_absent(self):
        # Projector not on disk → None (the launch-time has_vision guard refuses
        # rather than serve silently text-only).
        m = self.mm.get_model_for_tier(HardwareTierLevel.TIER_1)
        self.assertTrue(m.has_vision)
        self.assertIsNone(m.mmproj_local_path)

    def test_by_name_derives_projector_from_disk(self):
        (self.store / INTERNVL_MMPROJ).write_bytes(b"proj")
        m = self.mm.get_model_by_name("InternVL3.5-2B")
        self.assertIsNotNone(m)
        self.assertEqual(m.mmproj_local_path,
                         str(self.store / INTERNVL_MMPROJ))

    def test_non_vision_lookup_has_no_projector_path(self):
        # Even if a same-named file existed, a non-vision model carries no
        # mmproj_filename → mmproj_local_path stays None (no stale leak).
        m = self.mm.get_model_for_tier(HardwareTierLevel.TIER_2)
        self.assertFalse(m.has_vision)
        self.assertIsNone(m.mmproj_local_path)


if __name__ == "__main__":
    unittest.main()
