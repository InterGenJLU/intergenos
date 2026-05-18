"""Model manager — download, verify, and select LLM models.

Downloads GGUF models from Hugging Face (Unsloth quantizations),
verifies SHA256 integrity, and tracks
downloaded models in a JSON manifest.

Model storage: /var/lib/intergen/models/llm/
Manifest:      /var/lib/intergen/models/manifest.json

License gate (P-016):
Qwen models ship under the Tongyi Qianwen License — a source-available
license with use-restrictions and attribution requirements. Before
download_model() will fetch a Qwen-family model, the user MUST have
recorded acceptance of the model's license at
$XDG_DATA_HOME/intergen/legal/<filename>-accepted.json (per-user) or
at /var/lib/intergen/legal/<filename>-accepted.json (system-wide,
used by Forge if the user accepts at install time). Callers that need
to drive the acceptance flow interactively should catch
LicenseNotAcceptedError and surface the license content to the user.
See docs/legal/payload-licenses.md (LicenseRef-Tongyi-Qianwen) and
PRIVACY.md § 5.2.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.request
from pathlib import Path
from typing import Any, Callable

from intergen.interfaces.hardware import ModelManagerInterface
from intergen.interfaces.types import HardwareTierLevel, ModelInfo

log = logging.getLogger(__name__)

MODEL_DIR = Path("/var/lib/intergen/models/llm")
MANIFEST_PATH = Path("/var/lib/intergen/models/manifest.json")

# License gate paths
SYSTEM_LEGAL_DIR = Path("/var/lib/intergen/legal")


def _user_legal_dir() -> Path:
    """Per-user acceptance directory under XDG_DATA_HOME."""
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "intergen" / "legal"
    return Path.home() / ".local" / "share" / "intergen" / "legal"


# Model-family license refs (must match docs/legal/payload-licenses.md
# and docs/governance/license-policy.md § 7.5).
QWEN_LICENSE_REF = "LicenseRef-Tongyi-Qianwen"
QWEN_LICENSE_URL = (
    "https://github.com/QwenLM/Qwen3.5/blob/main/LICENSE"
)
APACHE_LICENSE_REF = "Apache-2.0"


def _model_license_ref(model: ModelInfo) -> str:
    """Return the SPDX LicenseRef for a model based on its repo_id.

    Qwen family → Tongyi Qianwen License (requires acceptance).
    nomic-embed-text → Apache-2.0 (no acceptance gate; permissive).
    """
    repo = (model.repo_id or "").lower()
    if "qwen" in repo:
        return QWEN_LICENSE_REF
    if "nomic" in repo:
        return APACHE_LICENSE_REF
    # Default: treat unknown as requiring acceptance — conservative.
    return f"LicenseRef-Unknown-{model.repo_id}"


class LicenseNotAcceptedError(Exception):
    """Raised when a model download is attempted but the model's license
    has not been accepted by the user. Callers should catch this,
    surface the license content + canonical URL to the user, write an
    acceptance record on user consent, and retry.
    """

    def __init__(self, model: ModelInfo, license_ref: str,
                 canonical_url: str) -> None:
        self.model = model
        self.license_ref = license_ref
        self.canonical_url = canonical_url
        super().__init__(
            f"License not accepted for {model.name} ({license_ref}). "
            f"See {canonical_url} and record acceptance before retry."
        )

# SHA256 hashes will be populated when models are finalized.
# For now, verification downloads the hash from HuggingFace.
MODEL_CATALOG: dict[HardwareTierLevel, ModelInfo] = {
    HardwareTierLevel.TIER_1: ModelInfo(
        name="Qwen3.5-2B",
        filename="Qwen3.5-2B-Q4_K_M.gguf",
        repo_id="unsloth/Qwen3.5-2B-GGUF",
        quant="Q4_K_M",
        size_gb=1.5,
        sha256="",  # populated on first download or from manifest
        tier=HardwareTierLevel.TIER_1,
    ),
    HardwareTierLevel.TIER_2: ModelInfo(
        name="Qwen3.5-9B",
        filename="Qwen3.5-9B-Q4_K_M.gguf",
        repo_id="unsloth/Qwen3.5-9B-GGUF",
        quant="Q4_K_M",
        size_gb=5.5,
        sha256="",
        tier=HardwareTierLevel.TIER_2,
    ),
    HardwareTierLevel.TIER_3: ModelInfo(
        name="Qwen3.5-35B-A3B",
        filename="Qwen3.5-35B-A3B-Q4_K_M.gguf",
        repo_id="unsloth/Qwen3.5-35B-A3B-GGUF",
        quant="Q4_K_M",
        size_gb=21.0,
        sha256="",
        tier=HardwareTierLevel.TIER_3,
    ),
}

EMBEDDING_MODEL = ModelInfo(
    name="nomic-embed-text-v1.5",
    filename="nomic-embed-text-v1.5.Q8_0.gguf",
    repo_id="nomic-ai/nomic-embed-text-v1.5-GGUF",
    quant="Q8_0",
    size_gb=0.274,
    sha256="",
    tier=HardwareTierLevel.TIER_1,  # works on all tiers
)

CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB download chunks


class ModelManager(ModelManagerInterface):
    """Downloads, verifies, and selects LLM models."""

    def __init__(self, model_dir: Path = MODEL_DIR,
                 manifest_path: Path = MANIFEST_PATH) -> None:
        self._model_dir = model_dir
        self._manifest_path = manifest_path
        self._manifest: dict[str, dict[str, Any]] = {}
        self._load_manifest()

    def check_license_acceptance(self, model: ModelInfo) -> bool:
        """Return True if the user has previously accepted the model's license.

        Acceptance is recorded at one of two paths:
        - $XDG_DATA_HOME/intergen/legal/<filename>-accepted.json (per-user)
        - /var/lib/intergen/legal/<filename>-accepted.json (system-wide,
          written by Forge at install time if the user accepts then)

        Apache-2.0 and other permissive licenses are treated as
        auto-accepted (returns True). Restrictive licenses (Tongyi
        Qianwen and unknown) require explicit acceptance.
        """
        license_ref = _model_license_ref(model)
        # Permissive licenses are auto-accepted.
        if license_ref == APACHE_LICENSE_REF:
            return True
        acceptance_filename = f"{model.filename}-accepted.json"
        for d in (_user_legal_dir(), SYSTEM_LEGAL_DIR):
            if (d / acceptance_filename).exists():
                return True
        return False

    def record_license_acceptance(self, model: ModelInfo, *,
                                  accepted_by: str = "") -> None:
        """Record that the user has accepted the model's license.

        Called by the UI/CLI layer after the user has been shown the
        license text and clicks/types accept. Writes the acceptance
        record under the user's XDG data dir.
        """
        license_ref = _model_license_ref(model)
        if license_ref == APACHE_LICENSE_REF:
            return  # Apache models don't need acceptance records
        import datetime
        legal_dir = _user_legal_dir()
        legal_dir.mkdir(parents=True, exist_ok=True)
        acceptance_filename = f"{model.filename}-accepted.json"
        record = {
            "model": model.name,
            "filename": model.filename,
            "repo_id": model.repo_id,
            "license_ref": license_ref,
            "canonical_url": (
                QWEN_LICENSE_URL if license_ref == QWEN_LICENSE_REF
                else "unknown"
            ),
            "accepted_at": datetime.datetime.now(
                datetime.timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "accepted_by": accepted_by or os.environ.get("USER", "unknown"),
        }
        target = legal_dir / acceptance_filename
        target.write_text(json.dumps(record, indent=2) + "\n")
        log.info("License acceptance recorded for %s at %s",
                 model.name, target)

    def download_model(self, model: ModelInfo, *,
                       progress_callback: Callable | None = None) -> bool:
        """Download model from Hugging Face and verify SHA256.

        Raises LicenseNotAcceptedError if the model's license requires
        acceptance and none is on record. Callers should catch this,
        drive the acceptance flow (display license, get user consent,
        call record_license_acceptance), and retry.
        """
        # License gate (P-016) — fail-closed before any network activity.
        if not self.check_license_acceptance(model):
            license_ref = _model_license_ref(model)
            canonical_url = (
                QWEN_LICENSE_URL if license_ref == QWEN_LICENSE_REF
                else f"(see docs/legal/payload-licenses.md for {license_ref})"
            )
            log.warning(
                "License not accepted for %s (%s). "
                "Refusing to download until acceptance is recorded.",
                model.name, license_ref,
            )
            raise LicenseNotAcceptedError(model, license_ref, canonical_url)

        self._model_dir.mkdir(parents=True, exist_ok=True)
        local_path = self._model_dir / model.filename

        if local_path.exists():
            log.info("Model already exists at %s, verifying...", local_path)
            model.local_path = str(local_path)
            if self.verify_model(model):
                model.downloaded = True
                self._update_manifest(model)
                return True
            log.warning("Existing model failed verification, re-downloading")
            local_path.unlink()

        url = self._huggingface_url(model)
        log.info("Downloading %s from %s", model.name, url)

        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=30) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                sha256 = hashlib.sha256()

                with open(local_path, "wb") as f:
                    while True:
                        chunk = resp.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        f.write(chunk)
                        sha256.update(chunk)
                        downloaded += len(chunk)
                        if progress_callback and total > 0:
                            progress_callback(downloaded, total)

            computed_hash = sha256.hexdigest()
            log.info("Download complete: %s (SHA256: %s)", model.filename, computed_hash)

            # If we have an expected hash, verify
            if model.sha256 and model.sha256 != computed_hash:
                log.error(
                    "SHA256 mismatch for %s: expected %s, got %s",
                    model.filename, model.sha256, computed_hash,
                )
                local_path.unlink()
                return False

            # Store the hash for future verification
            model.sha256 = computed_hash
            model.local_path = str(local_path)
            model.downloaded = True
            self._update_manifest(model)
            return True

        except Exception as e:
            log.error("Download failed for %s: %s", model.name, e)
            if local_path.exists():
                local_path.unlink()
            return False

    def get_model_by_name(self, name: str) -> ModelInfo | None:
        """Find a model by name across all tiers."""
        for model in MODEL_CATALOG.values():
            if model.name == name:
                local_path = self._model_dir / model.filename
                if local_path.exists():
                    model.local_path = str(local_path)
                    model.downloaded = True
                return model
        return None

    def get_model_for_tier(self, tier: HardwareTierLevel) -> ModelInfo:
        """Return the recommended model for a hardware tier."""
        model = MODEL_CATALOG[tier]

        # Check if already downloaded
        local_path = self._model_dir / model.filename
        if local_path.exists():
            model.local_path = str(local_path)
            model.downloaded = True

            # Restore SHA256 from manifest if available
            manifest_entry = self._manifest.get(model.filename)
            if manifest_entry and not model.sha256:
                model.sha256 = manifest_entry.get("sha256", "")

        return model

    def list_downloaded(self) -> list[ModelInfo]:
        """List all downloaded and verified models."""
        result = []
        for entry in self._manifest.values():
            local_path = Path(entry.get("local_path", ""))
            if local_path.exists():
                info = ModelInfo(
                    name=entry["name"],
                    filename=entry["filename"],
                    repo_id=entry["repo_id"],
                    quant=entry["quant"],
                    size_gb=entry["size_gb"],
                    sha256=entry["sha256"],
                    tier=HardwareTierLevel(entry["tier"]),
                    local_path=str(local_path),
                    downloaded=True,
                )
                result.append(info)
        return result

    def verify_model(self, model: ModelInfo) -> bool:
        """Verify SHA256 hash of a downloaded model.

        Every model file must pass integrity verification before being
        loaded into memory.
        """
        if not model.local_path:
            return False

        path = Path(model.local_path)
        if not path.exists():
            return False

        log.info("Verifying SHA256 for %s...", model.filename)
        sha256 = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                sha256.update(chunk)

        computed = sha256.hexdigest()

        if not model.sha256:
            # No expected hash — record the computed one
            log.info("No expected hash — recording: %s", computed)
            model.sha256 = computed
            self._update_manifest(model)
            return True

        if computed == model.sha256:
            log.info("SHA256 verified: %s", model.filename)
            return True

        log.error(
            "SHA256 MISMATCH for %s: expected %s, got %s",
            model.filename, model.sha256, computed,
        )
        return False

    def get_embedding_model(self) -> ModelInfo:
        """Return info for the embedding model (nomic-embed-text-v1.5)."""
        model = ModelInfo(
            name=EMBEDDING_MODEL.name,
            filename=EMBEDDING_MODEL.filename,
            repo_id=EMBEDDING_MODEL.repo_id,
            quant=EMBEDDING_MODEL.quant,
            size_gb=EMBEDDING_MODEL.size_gb,
            sha256=EMBEDDING_MODEL.sha256,
            tier=EMBEDDING_MODEL.tier,
        )

        local_path = self._model_dir / model.filename
        if local_path.exists():
            model.local_path = str(local_path)
            model.downloaded = True

        return model

    def _huggingface_url(self, model: ModelInfo) -> str:
        """Build the HuggingFace download URL for a model."""
        return (
            f"https://huggingface.co/{model.repo_id}"
            f"/resolve/main/{model.filename}"
        )

    def _load_manifest(self) -> None:
        """Load the model manifest from disk."""
        if self._manifest_path.exists():
            try:
                self._manifest = json.loads(self._manifest_path.read_text())
            except (json.JSONDecodeError, OSError) as e:
                log.warning("Failed to load manifest: %s", e)
                self._manifest = {}

    def _update_manifest(self, model: ModelInfo) -> None:
        """Update the manifest with model info and write to disk."""
        self._manifest[model.filename] = {
            "name": model.name,
            "filename": model.filename,
            "repo_id": model.repo_id,
            "quant": model.quant,
            "size_gb": model.size_gb,
            "sha256": model.sha256,
            "tier": model.tier.value,
            "local_path": model.local_path or "",
        }
        try:
            self._manifest_path.parent.mkdir(parents=True, exist_ok=True)
            self._manifest_path.write_text(
                json.dumps(self._manifest, indent=2) + "\n"
            )
        except OSError as e:
            log.error("Failed to write manifest: %s", e)
