"""Tests for the license-authority fix: the SIGNED manifest license_ref is the
authority for the acceptance gate; the repo_id heuristic is only the
conservative DENY fallback (WC's fail-open finding), and an unsigned catalog
license_ref default is structurally cleared on a no-manifest-entry model.
"""
import tempfile
import unittest
from pathlib import Path

from intergen.model_manager import (
    ModelManager, _model_license_ref, QWEN_LICENSE_REF, APACHE_LICENSE_REF,
)
from intergen.interfaces.types import ModelInfo, HardwareTierLevel


def _mi(**kw):
    base = dict(name="X", filename="x.gguf", repo_id="org/x", quant="Q4_K_M",
                size_gb=1.0, sha256="", tier=HardwareTierLevel.TIER_1)
    base.update(kw)
    return ModelInfo(**base)


class TestLicenseAuthority(unittest.TestCase):
    def test_signed_apache_relaxes(self):
        m = _mi(repo_id="bartowski/OpenGVLab_InternVL3_5-2B-GGUF",
                license_ref="Apache-2.0")
        self.assertEqual(_model_license_ref(m), "Apache-2.0")

    def test_signed_field_overrides_repo_guess(self):
        # The signed field wins even over a qwen-substring repo_id.
        m = _mi(repo_id="unsloth/Qwen3.5-2B-GGUF", license_ref="Apache-2.0")
        self.assertEqual(_model_license_ref(m), "Apache-2.0")

    def test_empty_signed_falls_to_qwen_substring(self):
        m = _mi(repo_id="unsloth/Qwen3.5-9B-GGUF", license_ref="")
        self.assertEqual(_model_license_ref(m), QWEN_LICENSE_REF)

    def test_empty_signed_nomic_is_apache(self):
        m = _mi(repo_id="nomic-ai/nomic-embed-text-v1.5-GGUF", license_ref="")
        self.assertEqual(_model_license_ref(m), APACHE_LICENSE_REF)

    def test_empty_signed_unknown_denies(self):
        m = _mi(repo_id="bartowski/OpenGVLab_InternVL3_5-2B-GGUF", license_ref="")
        self.assertTrue(_model_license_ref(m).startswith("LicenseRef-Unknown"))

    def test_no_entry_clears_unsigned_catalog_license_default(self):
        # Structural fail-open hardening: a model with a catalog-supplied
        # license_ref but NO signed manifest entry must have it cleared by
        # _apply_manifest, so the unsigned default can never relax the gate.
        with tempfile.TemporaryDirectory() as td:
            mm = ModelManager(
                model_dir=Path(td) / "m",
                manifest_path=Path(td) / "man.json",
                pins_path=Path(td) / "absent.json",  # → empty _entries
            )
            m = _mi(repo_id="org/unknown", license_ref="Apache-2.0")
            mm._apply_manifest(m)
            self.assertEqual(m.license_ref, "")
            self.assertTrue(_model_license_ref(m).startswith("LicenseRef-Unknown"))


if __name__ == "__main__":
    unittest.main()
