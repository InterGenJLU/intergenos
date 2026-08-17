# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Vendor-filename split + the shipped 35B pin.

The mirror disambiguates upstream's generic per-repo GGUF names (every unsloth
projector is served as mmproj-F16.gguf; the mirror publishes
mmproj-Qwen3.5-{9B,35B-A3B}-f16.gguf). The manifest's optional
vendor_filename / mmproj_vendor_filename fields carry the name the VENDOR
actually serves so the Hugging Face fallback URL stays real while the mirror
URL (primary) uses the mirror name. The sha256 pin gates the bytes identically
on either source, so the split never weakens verification.

Also pins the shipped intergen/data/models-manifest.json data itself: the
Tier-3 35B entry (the un-cap trigger — see test_model_manager_35b_uncap) and
schema coherence of every entry.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from intergen.interfaces.types import HardwareTierLevel, ModelInfo
from intergen.model_manager import ModelManager

SHIPPED_MANIFEST = (
    Path(__file__).resolve().parent.parent / "data" / "models-manifest.json"
)

HEX64 = set("0123456789abcdef")


def _mk_manager(tmp: str, entries: list[dict]) -> ModelManager:
    pins = Path(tmp) / "models-manifest.json"
    pins.write_text(json.dumps({"version": "0.1", "entries": entries}))
    store = Path(tmp) / "llm"
    store.mkdir(parents=True, exist_ok=True)
    return ModelManager(
        model_dir=store,
        manifest_path=Path(tmp) / "m.json",
        pins_path=pins,
    )


class VendorFilenameUrlTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.mm = _mk_manager(self._tmp.name, [])

    def tearDown(self):
        self._tmp.cleanup()

    def _model(self, **kw) -> ModelInfo:
        base = dict(
            name="M", filename="mirror-name.gguf", repo_id="vendor/repo",
            quant="Q4_K_M", size_gb=1.0, sha256="a" * 64,
            tier=HardwareTierLevel.TIER_2,
        )
        base.update(kw)
        return ModelInfo(**base)

    def test_hf_url_uses_vendor_filename_when_declared(self):
        m = self._model(vendor_filename="vendor-name.gguf")
        self.assertEqual(
            self.mm._huggingface_url(m),
            "https://huggingface.co/vendor/repo/resolve/main/vendor-name.gguf",
        )

    def test_hf_url_falls_back_to_filename_when_undeclared(self):
        m = self._model()
        self.assertEqual(
            self.mm._huggingface_url(m),
            "https://huggingface.co/vendor/repo/resolve/main/mirror-name.gguf",
        )

    def test_mirror_url_always_uses_mirror_filename(self):
        m = self._model(vendor_filename="vendor-name.gguf")
        self.assertTrue(self.mm._mirror_url(m).endswith("/mirror-name.gguf"))


class VendorFilenameOverlayTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.entry = {
            "name": "M", "filename": "mirror-name.gguf",
            "repo_id": "vendor/repo", "quant": "Q4_K_M",
            "size_bytes": 10, "sha256": "a" * 64, "tier": 2,
            "license_ref": "Apache-2.0",
            "has_vision": True, "cacheable": False,
            "mmproj_filename": "mmproj-mirror-name.gguf",
            "mmproj_vendor_filename": "mmproj-F16.gguf",
            "mmproj_sha256": "b" * 64,
            "mmproj_size_bytes": 5,
        }
        self.mm = _mk_manager(self._tmp.name, [self.entry])

    def tearDown(self):
        self._tmp.cleanup()

    def _model(self, filename="mirror-name.gguf") -> ModelInfo:
        return ModelInfo(
            name="M", filename=filename, repo_id="vendor/repo",
            quant="Q4_K_M", size_gb=1.0, sha256="",
            tier=HardwareTierLevel.TIER_2,
        )

    def test_overlay_carries_mmproj_vendor_filename(self):
        m = self._model()
        self.mm._apply_manifest(m)
        self.assertEqual(m.mmproj_filename, "mmproj-mirror-name.gguf")
        self.assertEqual(m.mmproj_vendor_filename, "mmproj-F16.gguf")

    def test_no_entry_clears_vendor_fields(self):
        m = self._model(filename="unlisted.gguf")
        m.vendor_filename = "stale.gguf"
        m.mmproj_vendor_filename = "stale-mmproj.gguf"
        self.mm._apply_manifest(m)
        self.assertIsNone(m.vendor_filename)
        self.assertIsNone(m.mmproj_vendor_filename)


class ShippedManifestDataTest(unittest.TestCase):
    """Pins the in-tree manifest DATA (the artifact the intergen package
    installs to /usr/share/intergen/models-manifest.json)."""

    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(SHIPPED_MANIFEST.read_text())
        cls.by_name = {e["name"]: e for e in cls.data["entries"]}

    def test_every_entry_schema_coherent(self):
        for e in self.data["entries"]:
            with self.subTest(entry=e.get("name")):
                self.assertTrue(e.get("filename"))
                sha = e.get("sha256", "")
                self.assertEqual(len(sha), 64)
                self.assertTrue(set(sha) <= HEX64)
                self.assertGreater(int(e.get("size_bytes", 0)), 0)
                if e.get("mmproj_filename"):
                    msha = e.get("mmproj_sha256", "")
                    self.assertEqual(len(msha), 64)
                    self.assertTrue(set(msha) <= HEX64)
                    self.assertGreater(int(e.get("mmproj_size_bytes", 0)), 0)

    def test_35b_entry_pinned_tier3_with_vision(self):
        e = self.by_name["Qwen3.5-35B-A3B"]
        self.assertEqual(e["filename"], "Qwen3.5-35B-A3B-Q4_K_M.gguf")
        self.assertEqual(e["tier"], 3)
        self.assertTrue(e["has_vision"])
        self.assertFalse(e["cacheable"])
        self.assertEqual(
            e["mmproj_filename"], "mmproj-Qwen3.5-35B-A3B-f16.gguf"
        )
        self.assertEqual(e["mmproj_vendor_filename"], "mmproj-F16.gguf")

    def test_9b_mmproj_uses_disambiguated_mirror_name(self):
        e = self.by_name["Qwen3.5-9B"]
        self.assertEqual(e["mmproj_filename"], "mmproj-Qwen3.5-9B-f16.gguf")
        self.assertEqual(e["mmproj_vendor_filename"], "mmproj-F16.gguf")


if __name__ == "__main__":
    unittest.main()
