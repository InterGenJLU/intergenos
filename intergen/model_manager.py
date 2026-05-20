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

# T0-4-D — D-008 supply-chain layer. The PINS_MANIFEST is the
# package-shipped canonical SHA256 pin map. Read-only system path;
# populated by the build-system coordinator from a PGP-signed
# in-tree manifest at intergen/data/models-manifest.json + installed
# to /usr/share/intergen/models-manifest.json by packages/ai/intergen/
# build.sh. Closes audit I-005 (Holy-Grail Model SHA256 TOFU) by
# enforcing: every model.sha256 derives from this manifest; downloads
# refuse without a pin; verify_model refuses without a pin; the prior
# 'record on first download' (TOFU) branch is removed.
#
# Co-deliverable with the build-system coordinator's manifest +
# signature work (Steps 1+2 of T0-4-D; HOLDS pending operator decision
# on Q2 canonical-SHA-source authority delegation per the 00:45:22Z
# propose-and-wait + SPOC 00:58:09Z concur). This file fails closed
# until the manifest exists.
PINS_MANIFEST_PATH = Path("/usr/share/intergen/models-manifest.json")

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

# SHA256 hashes are populated from PINS_MANIFEST_PATH at ModelManager
# construction time. The entries below ship sha256="" intentionally —
# the pinning manifest is the SoT; the catalog is the structural
# model-tier mapping. ModelManager.__init__ overlays sha256 from
# self._pins[filename]; methods that return ModelInfo objects re-apply
# the pin so callers always see the authoritative sha256 value.
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


def _load_pins(pins_path: Path = PINS_MANIFEST_PATH) -> dict[str, str]:
    """Load the package-shipped pin map from PINS_MANIFEST_PATH.

    Returns a dict[filename → sha256]. Empty dict on:
      - missing manifest file (early-install state OR misconfigured install)
      - malformed JSON
      - schema mismatch (no 'entries' field)

    Per the T0-4-D fail-closed contract, an empty dict means every
    downstream verify_model + download_model call refuses. The empty
    return is intentionally not a raise — module import + ModelManager
    construction must continue so callers can surface the misconfiguration
    cleanly rather than crash at import time.

    Manifest schema (matches the build-system coordinator's Step 1
    deliverable per the T0-4-D propose-and-wait Q1 concur):
      {
        "version": "0.1",
        "entries": [
          {"name": "...", "filename": "...", "sha256": "...", ...}
        ],
        "signing": {"fingerprint": "...", "signature_path": "..."}
      }
    """
    if not pins_path.exists():
        log.warning(
            "models pins manifest missing at %s — model downloads + "
            "verification will fail-closed per T0-4-D until the "
            "intergen package ships the manifest",
            pins_path,
        )
        return {}
    try:
        data = json.loads(pins_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        log.error(
            "models pins manifest at %s is unreadable (%s) — "
            "fail-closed posture in effect",
            pins_path, exc,
        )
        return {}
    entries = data.get("entries")
    if not isinstance(entries, list):
        log.error(
            "models pins manifest at %s lacks 'entries' list — "
            "fail-closed posture in effect",
            pins_path,
        )
        return {}
    pins: dict[str, str] = {}
    for entry in entries:
        filename = entry.get("filename")
        sha256 = entry.get("sha256")
        if filename and sha256:
            pins[filename] = sha256
    return pins


class ModelManager(ModelManagerInterface):
    """Downloads, verifies, and selects LLM models."""

    def __init__(self, model_dir: Path = MODEL_DIR,
                 manifest_path: Path = MANIFEST_PATH,
                 pins_path: Path = PINS_MANIFEST_PATH) -> None:
        self._model_dir = model_dir
        self._manifest_path = manifest_path
        self._manifest: dict[str, dict[str, Any]] = {}
        self._load_manifest()
        # T0-4-D — load the package-shipped pin manifest. Empty dict
        # on missing/malformed manifest; downstream operations refuse
        # rather than auto-trust.
        self._pins = _load_pins(pins_path)
        if not self._pins:
            log.critical(
                "ModelManager constructed with no pinned hashes — every "
                "model download + verification will refuse until the "
                "intergen package ships %s. Operator action required.",
                pins_path,
            )

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

        # T0-4-D pin gate — refuse to download a model whose canonical
        # sha256 is not pinned in the package-shipped manifest. Without
        # a pin we cannot verify the downloaded file, and TOFU is
        # explicitly forbidden per I-005 Holy-Grail closure. Apply pin
        # from self._pins in case the caller passed a stale ModelInfo
        # that pre-dates the manifest load.
        if not model.sha256:
            model.sha256 = self._pins.get(model.filename, "")
        if not model.sha256:
            log.error(
                "Refusing to download %s — no pin in models manifest. "
                "The intergen package must ship %s with a signed entry "
                "for this filename before download is authorized.",
                model.filename, PINS_MANIFEST_PATH,
            )
            return False

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

            # T0-4-D — pin gate at the post-download boundary. The pin
            # is required (the pre-download gate above refuses empty
            # pins) so model.sha256 is non-empty here; the only failure
            # mode is a mismatch, which means the downloaded bytes did
            # not match the canonical pin. Delete + fail per the Q5
            # delete-and-fail concur.
            if model.sha256 != computed_hash:
                log.error(
                    "SHA256 mismatch for %s: expected %s, got %s",
                    model.filename, model.sha256, computed_hash,
                )
                local_path.unlink()
                return False

            # Pin matches — record local-state metadata. model.sha256
            # is preserved as the manifest's canonical value (NOT
            # overwritten with computed_hash — they're equal anyway,
            # and preserving the source-of-truth ordering keeps the
            # 'pin is the trust anchor' invariant explicit).
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
                # T0-4-D — overlay the canonical pin from the package-
                # shipped manifest. Empty pin means downstream
                # operations will refuse (fail-closed per I-005 closure).
                model.sha256 = self._pins.get(model.filename, "")
                local_path = self._model_dir / model.filename
                if local_path.exists():
                    model.local_path = str(local_path)
                    model.downloaded = True
                return model
        return None

    def get_model_for_tier(self, tier: HardwareTierLevel) -> ModelInfo:
        """Return the recommended model for a hardware tier."""
        model = MODEL_CATALOG[tier]

        # T0-4-D — overlay the canonical pin from the package-shipped
        # manifest. The local /var/lib/intergen/models/manifest.json
        # remains as a download-tracking sidecar but is no longer the
        # SoT for sha256 (it records computed hashes for already-trusted
        # downloads; the pinning manifest is the trust anchor).
        model.sha256 = self._pins.get(model.filename, "")

        # Check if already downloaded
        local_path = self._model_dir / model.filename
        if local_path.exists():
            model.local_path = str(local_path)
            model.downloaded = True

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
        """Verify SHA256 hash of a downloaded model against the
        package-shipped pin.

        T0-4-D — fail-closed semantics. If the model has no pinned
        sha256 (empty pin from PINS_MANIFEST_PATH), refuse to verify.
        The prior 'No expected hash — recording' TOFU branch has been
        removed per I-005 Holy-Grail closure: any MITM / compromised
        HF mirror / repo rug-pull is now caught at this boundary
        because the trust anchor is the package-shipped signed
        manifest, not the file the dispatcher just downloaded.
        """
        if not model.local_path:
            return False

        path = Path(model.local_path)
        if not path.exists():
            return False

        if not model.sha256:
            # T0-4-D fail-closed — no pin means we cannot establish
            # trust in the local file. Refuse rather than auto-record.
            log.error(
                "Cannot verify %s — no pin available in models manifest. "
                "Install the intergen package's pins manifest at %s OR "
                "wait for the build-system coordinator's Steps 1+2 of "
                "T0-4-D to land.",
                model.filename, PINS_MANIFEST_PATH,
            )
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

        if computed == model.sha256:
            log.info("SHA256 verified: %s", model.filename)
            return True

        log.error(
            "SHA256 MISMATCH for %s: expected %s, got %s",
            model.filename, model.sha256, computed,
        )
        return False

    def verify_arbitrary_path(self, path: Path) -> bool:
        """Verify an arbitrary on-disk model file against the package-
        shipped pin manifest. Used by intergen.dbus_daemon to gate the
        INTERGEN_MODEL_PATH env-var override per T0-4-D (closes audit
        I-016 adjacent: env-var path used to bypass model_manager
        verification entirely; now it's a 'select a different pinned
        model' override, not an 'arbitrary path' override).

        Returns True only if:
          - the file exists, AND
          - the filename appears in the pin manifest, AND
          - the file's computed SHA256 matches the manifest pin.

        Returns False on any failure (missing file / no pin for this
        filename / SHA mismatch). All failures log an error so the
        caller can surface a clear diagnostic to the user.
        """
        if not path.exists():
            log.error("verify_arbitrary_path: %s does not exist", path)
            return False
        pin = self._pins.get(path.name)
        if not pin:
            log.error(
                "verify_arbitrary_path: %s has no pin entry in %s — "
                "env-var override refused per T0-4-D (closes I-016)",
                path.name, PINS_MANIFEST_PATH,
            )
            return False
        synthetic = ModelInfo(
            name=path.stem,
            filename=path.name,
            repo_id="(env-override)",
            quant="(env-override)",
            size_gb=0.0,
            sha256=pin,
            tier=HardwareTierLevel.TIER_1,
            local_path=str(path),
        )
        return self.verify_model(synthetic)

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
