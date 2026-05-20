"""T0-4-D — D-008 supply-chain layer integration tests.

Closes audit I-005 (Holy-Grail Model SHA256 TOFU) + I-016 adjacent
(INTERGEN_MODEL_PATH env-override bypass) by exercising the
package-shipped pin manifest + the fail-closed semantics in
`intergen.model_manager.ModelManager`:

  - test_pin_required_for_download   — refuse to fetch when no pin
  - test_pin_mismatch_refuses_load   — verify_model returns False on
                                       SHA mismatch
  - test_pin_match_loads             — verify_model returns True on
                                       SHA match
  - test_manifest_missing_refuses_all — empty manifest fails closed
  - test_intergen_model_path_env_pin_verify — env-override goes
                                       through verify_arbitrary_path
                                       (the dbus_daemon I-016 closure
                                       gate)

The pin manifest is the SoT for sha256; TOFU is forbidden. Every
test seeds a synthetic pins manifest via the `pins_path=` constructor
keyword to keep the tests isolated from `/usr/share/intergen/models-
manifest.json` system state.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import pytest

from intergen.interfaces.types import HardwareTierLevel, ModelInfo
from intergen.model_manager import ModelManager, MODEL_CATALOG


# ── Helpers ──


def _write_pins_manifest(entries: list[dict]) -> Path:
    """Seed a temporary pins manifest with the given entries."""
    tmp = tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False,
    )
    json.dump({"version": "0.1", "entries": entries}, tmp)
    tmp.close()
    return Path(tmp.name)


def _write_model_file(content: bytes) -> tuple[Path, str]:
    """Write a fake model file + return (path, sha256)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".gguf", delete=False)
    tmp.write(content)
    tmp.close()
    return Path(tmp.name), hashlib.sha256(content).hexdigest()


def _accept_qwen_license_for(model: ModelInfo, monkeypatch) -> None:
    """Bypass the P-016 license gate for download tests.

    Patches ModelManager.check_license_acceptance to return True for
    the specific model under test. The license gate is separate scope
    (P-016 closure) — these tests target the I-005 pin gate only.
    """
    monkeypatch.setattr(
        ModelManager,
        "check_license_acceptance",
        lambda self, m: True,
    )


# ── 1. Pin required for download (fail-closed pre-network) ──


def test_pin_required_for_download(monkeypatch, tmp_path):
    """download_model() must refuse before any network activity when
    the requested model's filename has no pin entry in the manifest.
    """
    # Empty manifest (no entries at all)
    pins_path = _write_pins_manifest([])
    mgr = ModelManager(
        model_dir=tmp_path,
        manifest_path=tmp_path / "local-manifest.json",
        pins_path=pins_path,
    )

    # Construct a ModelInfo with no pin (sha256="")
    unpinned = ModelInfo(
        name="Qwen3.5-2B",
        filename="Qwen3.5-2B-Q4_K_M.gguf",
        repo_id="unsloth/Qwen3.5-2B-GGUF",
        quant="Q4_K_M",
        size_gb=1.5,
        sha256="",
        tier=HardwareTierLevel.TIER_1,
    )
    _accept_qwen_license_for(unpinned, monkeypatch)

    result = mgr.download_model(unpinned)
    assert result is False
    # File should not have been created — no network activity
    assert not (tmp_path / unpinned.filename).exists()


# ── 2. Pin mismatch refuses verify_model ──


def test_pin_mismatch_refuses_load(tmp_path):
    """verify_model() must return False when the file's computed
    SHA256 does not match the pin from the manifest. Delete the
    file from the in-memory pin path comparison.
    """
    model_file, _real_sha = _write_model_file(b"valid-model-content")
    pins_path = _write_pins_manifest([
        {"filename": model_file.name, "sha256": "wrong-pin-value-deadbeef"},
    ])
    mgr = ModelManager(pins_path=pins_path, model_dir=tmp_path)

    model = ModelInfo(
        name=model_file.stem,
        filename=model_file.name,
        repo_id="r",
        quant="q",
        size_gb=0.1,
        sha256="wrong-pin-value-deadbeef",
        tier=HardwareTierLevel.TIER_1,
        local_path=str(model_file),
    )
    assert mgr.verify_model(model) is False


# ── 3. Pin match accepts verify_model ──


def test_pin_match_loads(tmp_path):
    """verify_model() must return True when the file's computed
    SHA256 matches the pin from the manifest.
    """
    content = b"valid-model-content-pin-match"
    model_file, real_sha = _write_model_file(content)
    pins_path = _write_pins_manifest([
        {"filename": model_file.name, "sha256": real_sha},
    ])
    mgr = ModelManager(pins_path=pins_path, model_dir=tmp_path)

    model = ModelInfo(
        name=model_file.stem,
        filename=model_file.name,
        repo_id="r",
        quant="q",
        size_gb=0.1,
        sha256=real_sha,
        tier=HardwareTierLevel.TIER_1,
        local_path=str(model_file),
    )
    assert mgr.verify_model(model) is True


# ── 4. Missing manifest fails closed across all operations ──


def test_manifest_missing_refuses_all(monkeypatch, tmp_path):
    """When the pin manifest is absent (early-install state OR a
    misconfigured install), ModelManager construction succeeds with an
    empty pin map + every downstream verify + download fails closed.
    The prior TOFU branch in verify_model is gone.
    """
    nonexistent = tmp_path / "no-such-pins.json"
    mgr = ModelManager(
        model_dir=tmp_path,
        manifest_path=tmp_path / "local-manifest.json",
        pins_path=nonexistent,
    )
    assert mgr._pins == {}

    # verify_model refuses without pin (fail-closed; no TOFU)
    model_file, _ = _write_model_file(b"anything")
    model = ModelInfo(
        name=model_file.stem,
        filename=model_file.name,
        repo_id="r",
        quant="q",
        size_gb=0.1,
        sha256="",
        tier=HardwareTierLevel.TIER_1,
        local_path=str(model_file),
    )
    assert mgr.verify_model(model) is False

    # get_model_for_tier overlays empty pin -> downstream refuses
    overlaid = mgr.get_model_for_tier(HardwareTierLevel.TIER_1)
    assert overlaid.sha256 == ""

    # download_model refuses pre-network with empty pin
    _accept_qwen_license_for(overlaid, monkeypatch)
    assert mgr.download_model(overlaid) is False


# ── 5. INTERGEN_MODEL_PATH env-override goes through pin verify (I-016 closure) ──


def test_intergen_model_path_env_pin_verify(tmp_path):
    """verify_arbitrary_path is the gate the dbus_daemon's
    INTERGEN_MODEL_PATH env-override consults. Test the 4 acceptance
    cases the gate must enforce: existence + pin presence + SHA match
    + the negative (unpinned filename refused even when SHA-matched
    elsewhere).
    """
    content = b"env-override-target-model"
    model_file, real_sha = _write_model_file(content)
    pins_path = _write_pins_manifest([
        {"filename": model_file.name, "sha256": real_sha},
    ])
    mgr = ModelManager(pins_path=pins_path, model_dir=tmp_path)

    # Accept: exists + pinned + SHA matches
    assert mgr.verify_arbitrary_path(model_file) is True

    # Refuse: file missing
    assert mgr.verify_arbitrary_path(tmp_path / "missing.gguf") is False

    # Refuse: file exists but filename not in pin manifest
    unpinned_file, _ = _write_model_file(b"unpinned-content")
    assert mgr.verify_arbitrary_path(unpinned_file) is False

    # Refuse: pinned filename but SHA mismatch (forge the filename to
    # match the pin entry, but write different bytes)
    forged = tmp_path / model_file.name
    forged.write_bytes(b"forged-content-different-sha")
    assert mgr.verify_arbitrary_path(forged) is False
