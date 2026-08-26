# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
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
import shutil
import subprocess
import tempfile
import threading
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from intergen import net_diagnostics
from intergen.interfaces.hardware import ModelManagerInterface
from intergen.interfaces.types import HardwareTier, HardwareTierLevel, ModelInfo
from intergen.private_state import private_dir, private_write_text

log = logging.getLogger(__name__)

MODEL_DIR = Path("/var/lib/intergen/models/llm")
MANIFEST_PATH = Path("/var/lib/intergen/models/manifest.json")

# pkexec target that installs a downloaded+verified model into the root-owned
# store. Installed by packages/ai/intergen/build.sh from
# intergen/data/intergen-model-setup-runner; gated by the PolicyKit action
# org.intergenos.intergen.provision-model-storage. Used by the non-root
# `intergen setup` path (see provision_model); the root path writes directly.
PROVISION_RUNNER_PATH = "/usr/bin/intergen-model-setup-runner"
# pkexec's own exit codes, per its documented behaviour: 126 when the
# authorization could not be obtained, 127 when the command could not be
# executed. Named here so the mapping below is readable and so no caller
# has to re-derive what a bare number means.
PKEXEC_NOT_AUTHORIZED = 126
PKEXEC_COMMAND_NOT_EXECUTED = 127

# Two-source model fetch (locked order 2026-06-09: MIRROR FIRST,
# vendor fallback). MIRROR = the InterGenOS public mirror, which hosts the
# SHA-pinned, validated GGUFs flat by filename. VENDOR = Hugging Face
# (per-model repo_id). The order was REVERSED from the original vendor-first
# posture after a real upstream-drift incident: Unsloth re-cut the
# Qwen3.5 GGUF in place (declaring a newer `qwen35` architecture our pinned
# engine could not load), so a mutable vendor file silently diverged from the
# validated artifact. Making the mirror authoritative means a fresh install
# always gets the exact validated file we host; the SHA pin
# (PINS_MANIFEST_PATH) is still verified on WHICHEVER source serves the bytes,
# so the HF fallback can never reintroduce a drifted file — it would fail the
# pin and be rejected. Neither source is trusted without the pin.
MIRROR_BASE_URL = "https://repo.intergenos.org/models"

# T0-4-D — D-008 supply-chain layer. The PINS_MANIFEST is the
# package-shipped canonical SHA256 pin map. Read-only system path;
# populated by the build-system coordinator from a PGP-signed
# in-tree manifest at intergen/data/models-manifest.json + installed
# to /usr/share/intergen/models-manifest.json by packages/ai/intergen/
# build.sh. Closes audit I-005 (Model SHA256 TOFU) by
# enforcing: every model.sha256 derives from this manifest; downloads
# refuse without a pin; verify_model refuses without a pin; the prior
# 'record on first download' (TOFU) branch is removed.
#
# Co-deliverable with the build-system coordinator's manifest +
# signature work (Steps 1+2 of T0-4-D; HOLDS pending operator decision
# on Q2 canonical-SHA-source authority delegation per the 00:45:22Z
# propose-and-wait + the 00:58:09Z concurrence). This file fails closed
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
    """Return the SPDX LicenseRef for a model.

    The SIGNED manifest's license_ref (overlaid onto model.license_ref by
    ModelManager._apply_manifest) is AUTHORITATIVE. Relaxation of the
    acceptance gate happens ONLY on that signed field (WC's fail-open finding:
    a permissive OpenGVLab/InternVL checkpoint un-gates ONLY because the
    master-signed manifest declares Apache-2.0 — never on a repo_id guess, and
    never on an unsigned catalog default, which _apply_manifest clears on a
    no-entry model). When the signed field is ABSENT (empty), fall to the
    conservative repo_id heuristic, which DENIES (requires acceptance) for
    anything unrecognized.
    """
    # getattr (not attribute access) so a minimal/partial model object without
    # the field is treated as "no signed license" → conservative fallback,
    # never a crash.
    signed = (getattr(model, "license_ref", "") or "").strip()
    if signed:
        return signed
    repo = (model.repo_id or "").lower()
    if "qwen" in repo:
        return QWEN_LICENSE_REF
    if "nomic" in repo:
        return APACHE_LICENSE_REF
    # Default: treat unknown as requiring acceptance — conservative.
    return f"LicenseRef-Unknown-{model.repo_id}"


# --------------------------------------------------------------------------
# Computed-digest reuse WITHIN ONE PROCESS (one daemon startup).
#
# A single daemon startup verified the SAME model file TWICE: once when the
# chat model is loaded, and again when the Sentinel deep scanner attaches to
# the same file. Both reads hash the whole file, so a startup paid for the
# model twice (measured on a 1.2 GB model: ~1.2 s each; the cost scales with
# the file, so a 20 GB model pays it twice too).
#
# What is cached is the DIGEST OF THE BYTES READ, never a pass/fail verdict:
# every call still compares that digest against the pin the caller passed in,
# so a caller with a different pin still gets its own answer, and a mismatch
# is still a mismatch. The cache key is the file's IDENTITY — path, device,
# inode, size and modification time in nanoseconds — so a file that is
# replaced, truncated, appended to or swapped between the two verifications
# misses the cache and is read again.
#
# Scope is the PROCESS, which is what makes it per-startup: a daemon startup
# is a new process, so nothing is carried across startups and every startup
# still reads and hashes the file from disk. Nothing is written to disk and
# nothing persists.
#
# What this does NOT do, stated plainly: it does not close the window between
# a verification and the load that follows it. That window already exists —
# the file is hashed, then handed to llama-server as a path — and an in-place
# rewrite that preserved size, inode and mtime to the nanosecond would be
# served from the cache by the second consumer. That is the same exposure the
# first consumer already had; deduplication neither creates nor widens it.
_DIGEST_CACHE_LOCK = threading.Lock()
_DIGEST_CACHE: dict[tuple, str] = {}


def _file_identity(path: Path) -> tuple | None:
    """The cache key: what makes THESE bytes distinguishable from other bytes.

    Returns None when the file cannot be stat'd, which routes the caller to a
    full read (fail toward doing the work, never toward reusing a digest for a
    file we could not identify).
    """
    try:
        st = path.stat()
    except OSError:
        return None
    return (str(path), st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def cached_digest(identity: tuple | None) -> str | None:
    """The digest already computed for this exact file identity, or None."""
    if identity is None:
        return None
    with _DIGEST_CACHE_LOCK:
        return _DIGEST_CACHE.get(identity)


def record_digest(identity: tuple | None, digest: str) -> None:
    """Record what a full read of this exact file identity produced."""
    if identity is None:
        return
    with _DIGEST_CACHE_LOCK:
        _DIGEST_CACHE[identity] = digest


def clear_digest_cache() -> None:
    """Forget every computed digest.

    A process boundary already does this; this exists so a test can put the
    module back to its cold state, and so a long-lived caller that wants a
    fresh read can force one.
    """
    with _DIGEST_CACHE_LOCK:
        _DIGEST_CACHE.clear()


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
        name="InternVL3.5-2B",
        filename="OpenGVLab_InternVL3_5-2B-Q4_K_M.gguf",
        repo_id="bartowski/OpenGVLab_InternVL3_5-2B-GGUF",
        quant="Q4_K_M",
        size_gb=1.3,
        sha256="",  # populated on first download or from manifest
        tier=HardwareTierLevel.TIER_1,
        # Capability defaults (the SIGNED manifest is authoritative + overrides
        # these via _apply_manifest; carried here to document intent). InternVL's
        # dense Qwen3 GQA backbone IS prefix-cacheable + ships a vision projector.
        has_vision=True,
        cacheable=True,
        mmproj_filename="mmproj-OpenGVLab_InternVL3_5-2B-f16.gguf",
    ),
    HardwareTierLevel.TIER_2: ModelInfo(
        name="Qwen3.5-9B",
        # Decided 2026-08-14: tier 2 ships the fine-tuned round-3 build of
        # Qwen3.5-9B (mirror-hosted; base-model repo_id retained for provenance
        # and for the unchanged vision projector's vendor fallback — the vendor
        # never serves this filename, so a mirror miss fails loud, never wrong).
        filename="Qwen3.5-9B-intergen-round3-Q4_K_M.gguf",
        repo_id="unsloth/Qwen3.5-9B-GGUF",
        quant="Q4_K_M",
        size_gb=5.6,
        sha256="",
        tier=HardwareTierLevel.TIER_2,
        # Capability defaults (the SIGNED manifest is authoritative + overrides
        # these via _apply_manifest; carried here to document intent). The
        # Qwen3.5 backbone is a Gated DeltaNet variant llama.cpp cannot
        # prefix-cache, so cacheable=False (never emits --cache-reuse). It ships
        # a paired F16 vision projector — mirror-disambiguated filename here;
        # the vendor serves it under its generic per-repo name (see
        # mmproj_vendor_filename in the manifest for the fallback URL).
        has_vision=True,
        cacheable=False,
        mmproj_filename="mmproj-Qwen3.5-9B-f16.gguf",
    ),
    HardwareTierLevel.TIER_3: ModelInfo(
        name="Qwen3.5-35B-A3B",
        filename="Qwen3.5-35B-A3B-Q4_K_M.gguf",
        repo_id="unsloth/Qwen3.5-35B-A3B-GGUF",
        quant="Q4_K_M",
        size_gb=22.0,
        sha256="",
        tier=HardwareTierLevel.TIER_3,
        # Capability defaults (the SIGNED manifest is authoritative + overrides
        # these via _apply_manifest; carried here to document intent). Same
        # Gated-DeltaNet-backbone family as the 9B → not prefix-cacheable
        # (never emits --cache-reuse); ships a paired F16 vision projector
        # under the mirror-disambiguated name (vendor generic name in the
        # manifest's mmproj_vendor_filename).
        has_vision=True,
        cacheable=False,
        mmproj_filename="mmproj-Qwen3.5-35B-A3B-f16.gguf",
    ),
}

EMBEDDING_MODEL = ModelInfo(
    name="nomic-embed-text-v1.5",
    filename="nomic-embed-text-v1.5.Q8_0.gguf",
    repo_id="nomic-ai/nomic-embed-text-v1.5-GGUF",
    quant="Q8_0",
    size_gb=0.146,
    sha256="",
    tier=HardwareTierLevel.TIER_1,  # works on all tiers
)

CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB download chunks


def _load_manifest_entries(
    pins_path: Path = PINS_MANIFEST_PATH,
) -> dict[str, dict[str, Any]]:
    """Load the signed models-manifest as a dict[filename → entry].

    This is the authority for BOTH the pins (sha256, + paired mmproj_sha256)
    AND the per-model capability descriptor / license (license_ref, has_vision,
    cacheable, mmproj_filename) — overlaid onto a ModelInfo by
    ModelManager._apply_manifest. Empty dict on:
      - missing manifest file (early-install state OR misconfigured install)
      - malformed JSON
      - schema mismatch (no 'entries' field)

    Per the T0-4-D fail-closed contract, an empty dict means every downstream
    verify_model + download_model call refuses. The empty return is intentionally
    not a raise — module import + ModelManager construction must continue so
    callers can surface the misconfiguration cleanly rather than crash at import.

    Manifest schema (the build-system coordinator's Step 1 deliverable + the
    InternVL paired-mmproj extension):
      {
        "version": "0.1",
        "entries": [
          {"name": "...", "filename": "...", "sha256": "...", "license_ref": "...",
           "has_vision": false, "cacheable": false,
           "mmproj_filename": "...", "mmproj_sha256": "...", ...}
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
    out: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if isinstance(entry, dict) and entry.get("filename"):
            out[entry["filename"]] = entry
    return out


def _pins_from_entries(entries: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Derive the filename → sha256 pin map from manifest entries, INCLUDING
    each entry's paired mmproj projector — so verify_model pins (and refuses on
    mismatch for) BOTH artifacts of a vision model, not just the LLM GGUF."""
    pins: dict[str, str] = {}
    for entry in entries.values():
        fn, sha = entry.get("filename"), entry.get("sha256")
        if fn and sha:
            pins[fn] = sha
        mfn, msha = entry.get("mmproj_filename"), entry.get("mmproj_sha256")
        if mfn and msha:
            pins[mfn] = msha
    return pins


def _load_pins(pins_path: Path = PINS_MANIFEST_PATH) -> dict[str, str]:
    """Back-compat: filename → sha256 pin map (now covering paired mmprojs)."""
    return _pins_from_entries(_load_manifest_entries(pins_path))


class ModelManager(ModelManagerInterface):
    """Downloads, verifies, and selects LLM models."""

    def __init__(self, model_dir: Path = MODEL_DIR,
                 manifest_path: Path = MANIFEST_PATH,
                 pins_path: Path = PINS_MANIFEST_PATH) -> None:
        self._model_dir = model_dir
        self._manifest_path = manifest_path
        self._pins_path = pins_path
        self._manifest: dict[str, dict[str, Any]] = {}
        # Why the most recent download attempt failed, as a
        # net_diagnostics cause (plus "pin-mismatch" for a file that arrived
        # but did not match its pin). None means no attempt has failed since
        # this manager was constructed. It exists because "the download did
        # not work" and "your name server is not answering" are different
        # things to tell a user, and only the code that saw the exception
        # knows which one happened.
        self.last_download_failure: str | None = None
        # The same idea for the PRIVILEGED INSTALL step. A download that
        # succeeded and an install the user did not authorize are two
        # different events with two different next steps, and until this
        # existed the second was logged here and thrown away — setup then
        # read last_download_failure, found nothing, and told the user the
        # download had not finished when the file was already on disk.
        #   'not-authorized'     — pkexec 126: the authorization was not
        #                          given (the prompt was dismissed or the
        #                          authentication did not succeed)
        #   'runner-missing'     — pkexec 127: the runner could not be
        #                          executed
        #   'dispatcher-refused' — any other code: the privileged
        #                          dispatcher itself refused, e.g. the
        #                          staged file failed its checksum
        #                          re-verify on the root side
        self.last_provision_failure: str | None = None
        self._load_manifest()
        # T0-4-D — load the package-shipped pin manifest. Empty dict
        # on missing/malformed manifest; downstream operations refuse
        # rather than auto-trust.
        self._entries = _load_manifest_entries(pins_path)
        self._pins = _pins_from_entries(self._entries)
        if not self._pins:
            log.critical(
                "ModelManager constructed with no pinned hashes — every "
                "model download + verification will refuse until the "
                "intergen package ships %s. Operator action required.",
                pins_path,
            )

    def _apply_manifest(self, model: ModelInfo) -> None:
        """Overlay the SIGNED manifest entry onto a ModelInfo (authoritative).

        The package-shipped models-manifest.json is the trust + capability
        anchor: the pin (sha256, + the paired mmproj_sha256), the license_ref
        the acceptance gate keys on, and the declared capability descriptor
        (has_vision / cacheable / mmproj_filename) that drives the launch flags
        ALL come from the master-signed manifest — never the catalog defaults
        or a repo_id guess (WC's fail-open finding: relax/enable only on the
        signed field). No entry => sha256 cleared => fail-closed (download +
        verify refuse, no --cache-reuse / --mmproj, conservative license gate).
        """
        entry = self._entries.get(model.filename)
        if entry is None:
            # No signed entry → grant NO declared capability (authoritative:
            # an unlisted model never launches with --cache-reuse / --mmproj).
            # sha256 is left untouched so the download pin-gate keeps its
            # existing fill-if-empty contract (a caller-supplied pin survives;
            # an empty one stays empty → refuse).
            model.has_vision = False
            model.cacheable = False
            model.mmproj_filename = None
            model.mmproj_sha256 = ""
            model.mmproj_size_gb = 0.0
            model.vendor_filename = None
            model.mmproj_vendor_filename = None
            # Clear license_ref too (WC structural fail-open hardening): an
            # unsigned catalog default must NEVER relax the acceptance gate.
            # Cleared → _model_license_ref falls to the conservative repo_id
            # heuristic (DENY for anything unrecognized).
            model.license_ref = ""
            return
        # Pin: fill only if the caller hasn't already set one (matches the
        # legacy lookup/download semantics; the catalog ships sha256="").
        if not model.sha256:
            model.sha256 = entry.get("sha256", "") or ""
        # Capability + license: the SIGNED manifest is authoritative.
        model.license_ref = entry.get("license_ref", "") or ""
        model.has_vision = bool(entry.get("has_vision", False))
        model.cacheable = bool(entry.get("cacheable", False))
        model.mmproj_filename = entry.get("mmproj_filename") or None
        model.mmproj_sha256 = entry.get("mmproj_sha256", "") or ""
        mmsz = int(entry.get("mmproj_size_bytes", 0) or 0)
        model.mmproj_size_gb = round(mmsz / (1024 ** 3), 3) if mmsz else 0.0
        # Vendor-side filename split (mirror disambiguates upstream's generic
        # per-repo names; the HF fallback must use the vendor's real name).
        model.vendor_filename = entry.get("vendor_filename") or None
        model.mmproj_vendor_filename = entry.get("mmproj_vendor_filename") or None

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
        private_dir(legal_dir)
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
        private_write_text(target, json.dumps(record, indent=2) + "\n")
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
        # explicitly forbidden per I-005 closure. Apply pin
        # from the signed manifest in case the caller passed a stale ModelInfo
        # that pre-dates the manifest load (overlays pin + capability + license).
        self._apply_manifest(model)
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
                return self._download_paired_mmproj(
                    model, progress_callback=progress_callback)
            log.warning("Existing model failed verification, re-downloading")
            local_path.unlink()

        # Two-source fetch (locked order 2026-06-09): the InterGenOS
        # mirror FIRST (authoritative — hosts the exact validated GGUFs), then
        # Hugging Face as fallback. The pin (model.sha256, guaranteed non-empty
        # by the pre-download gate above) is verified on WHICHEVER source serves
        # the bytes — a file that does not match the canonical pin is rejected
        # regardless of origin. If neither source yields a pin-matching file,
        # fail closed (I-005: no TOFU, no unverified model is ever
        # accepted). Mirror-first means a fresh install always pulls the
        # validated artifact we host rather than a mutable upstream that can
        # silently re-cut (the qwen35 drift incident); the HF fallback covers a
        # mirror outage without ever relaxing the integrity check — it cannot
        # serve a drifted file because the pin would reject it.
        sources = (
            ("InterGenOS mirror", self._mirror_url(model)),
            ("Hugging Face (vendor)", self._huggingface_url(model)),
        )
        self.last_download_failure = None
        for label, url in sources:
            log.info("Downloading %s from %s [%s]", model.name, url, label)
            if self._fetch_and_verify(
                url, local_path, model.sha256, progress_callback
            ):
                # Pin matches — record local-state metadata. model.sha256 is
                # preserved as the manifest's canonical value (the pin is the
                # trust anchor; it equals the computed hash by construction).
                model.local_path = str(local_path)
                model.downloaded = True
                self._update_manifest(model)
                log.info(
                    "Model %s ready (pin-verified) via %s", model.name, label
                )
                return self._download_paired_mmproj(
                    model, progress_callback=progress_callback)
            log.warning(
                "Source [%s] failed or pin-mismatched for %s; trying next",
                label, model.name,
            )

        if self.last_download_failure == net_diagnostics.NAME_RESOLUTION:
            log.error(
                "All sources exhausted for %s — fail-closed. Every attempt "
                "failed at the name lookup, so no source was ever contacted: "
                "%s", model.name,
                net_diagnostics.cause_headline(net_diagnostics.NAME_RESOLUTION),
            )
        else:
            log.error(
                "All sources exhausted for %s — fail-closed (no pin-verified "
                "copy obtained from vendor or mirror).", model.name,
            )
        return False

    def _download_paired_mmproj(self, model: ModelInfo, *,
                                progress_callback: Callable | None = None) -> bool:
        """Download + pin-verify a vision model's paired mmproj projector.

        Called once the primary GGUF has landed. A vision model (has_vision)
        ships a SECOND pinned artifact; without it the server can't do vision,
        and a DECLARED-but-unverifiable projector is a distinct integrity
        failure, not a benign skip. Returns:
          - True for a non-vision model (no-op),
          - True for a pin-verified projector,
          - False (fail-closed) if has_vision but the projector is unpinned, or
            no source yields a pin-matching copy.
        Reuses the SAME mirror-first / HF-fallback two-source path + pin check
        as the GGUF — the projector is in the same vendor repo, flat-by-name on
        the mirror.
        """
        if not model.has_vision:
            return True
        if not (model.mmproj_filename and model.mmproj_sha256):
            log.error(
                "Refusing %s — declared has_vision but the paired mmproj is "
                "not pinned in the signed manifest (filename=%s). Fail-closed.",
                model.name, model.mmproj_filename,
            )
            return False
        proj = ModelInfo(
            name=f"{model.name} (mmproj)",
            filename=model.mmproj_filename,
            repo_id=model.repo_id,        # same vendor repo as the GGUF
            quant=model.quant,
            size_gb=model.mmproj_size_gb,
            sha256=model.mmproj_sha256,   # the manifest pin (trust anchor)
            tier=model.tier,
            # the vendor's own name for the projector (mirror name differs)
            vendor_filename=getattr(model, "mmproj_vendor_filename", None),
        )
        local_path = self._model_dir / proj.filename
        if local_path.exists():
            proj.local_path = str(local_path)
            if self.verify_model(proj):
                model.mmproj_local_path = str(local_path)
                return True
            log.warning("Existing mmproj failed verification, re-downloading")
            local_path.unlink()
        for label, url in (
            ("InterGenOS mirror", self._mirror_url(proj)),
            ("Hugging Face (vendor)", self._huggingface_url(proj)),
        ):
            log.info("Downloading mmproj %s from %s [%s]", proj.filename, url, label)
            if self._fetch_and_verify(
                url, local_path, proj.sha256, progress_callback
            ):
                model.mmproj_local_path = str(local_path)
                log.info("mmproj %s ready (pin-verified) via %s",
                         proj.filename, label)
                return True
        log.error(
            "All sources exhausted for mmproj %s — fail-closed (no pin-verified "
            "projector). A vision model cannot serve without it.", proj.filename,
        )
        return False

    def provision_model(self, model: ModelInfo, *,
                        progress_callback: Callable | None = None) -> bool:
        """Download `model` and install it into the system-wide model store.

        The model store (/var/lib/intergen/models) is system-wide root-owned
        read-only by design (HG: a verified model the user cannot tamper with;
        load-time verify_model trusts that RO path). How the verified file lands
        there depends on who is running:

          - root (Forge installer, or `sudo intergen setup`): write straight into
            the store — the existing download_model path.
          - the unprivileged user (`intergen setup`): download + pin-verify to a
            user-writable staging dir, then escalate via pkexec ONCE
            (org.intergenos.intergen.provision-model-storage) to install the
            pin-re-verified file root-owned. Network/download never runs as root;
            only the final install crosses the privilege boundary.

        Either way the store stays root-owned read-only. Returns True on success.
        Raises LicenseNotAcceptedError exactly as download_model does (the staging
        download runs the same license + pin gates).
        """
        if os.geteuid() == 0:
            return self.download_model(model, progress_callback=progress_callback)
        return self._provision_via_pkexec(
            model, progress_callback=progress_callback)

    def _provision_via_pkexec(self, model: ModelInfo, *,
                             progress_callback: Callable | None = None) -> bool:
        """Stage-download as the user, then pkexec-install into the root store.

        The full download + license + pin verification runs unprivileged against
        a temp staging dir (reusing download_model verbatim). The root side
        (intergen.model_setup_dispatch via the pkexec runner) RE-verifies the
        staged file's sha256 against the shipped pin before installing it
        root-owned — the privilege boundary does not trust this side's check.
        """
        self.last_provision_failure = None
        staging_root = Path(tempfile.mkdtemp(prefix="intergen-model-stage-"))
        try:
            staging_mm = ModelManager(
                model_dir=staging_root,
                manifest_path=staging_root / "manifest.json",
                pins_path=self._pins_path,
            )
            if not staging_mm.download_model(
                model, progress_callback=progress_callback):
                return False  # download_model already logged the fail-closed reason

            staged_file = staging_root / model.filename
            if not staged_file.is_file():
                log.error("staged model missing after download: %s", staged_file)
                return False

            payload = {
                "filename": model.filename,
                "staging_path": str(staged_file),
            }
            # A vision model also staged its paired projector (download_model ->
            # _download_paired_mmproj wrote it to the same staging dir); install
            # it in the SAME pkexec escalation — one auth prompt, both files.
            if model.has_vision and model.mmproj_filename:
                staged_mmproj = staging_root / model.mmproj_filename
                if not staged_mmproj.is_file():
                    log.error("staged mmproj missing after download: %s",
                              staged_mmproj)
                    return False
                payload["mmproj_filename"] = model.mmproj_filename
                payload["mmproj_staging_path"] = str(staged_mmproj)
            args_json = json.dumps(payload)
            try:
                completed = subprocess.run(
                    ["pkexec", PROVISION_RUNNER_PATH, args_json],
                    capture_output=True, text=True, check=False,
                )
            except FileNotFoundError:
                log.error(
                    "pkexec not found; cannot install %s into the system model "
                    "store. Install polkit, or run `sudo intergen setup`.",
                    model.name,
                )
                return False
            except OSError as exc:
                log.error("pkexec invocation failed for model install: %s", exc)
                return False

            if completed.returncode == 0:
                self.last_provision_failure = None
                model.local_path = str(self._model_dir / model.filename)
                model.downloaded = True
                if model.has_vision and model.mmproj_filename:
                    model.mmproj_local_path = str(
                        self._model_dir / model.mmproj_filename)
                log.info("Model %s installed to system store via pkexec: %s",
                         model.name, completed.stdout.strip())
                return True

            # 126 = the authorization was not given; 127 = the runner could
            # not be executed; anything else = the dispatcher refused (e.g. a
            # pin re-verify mismatch). The runner emits a human-readable reason
            # on stdout/stderr. The reason is RECORDED as well as logged: the
            # caller turns it into the sentence the user reads, and a log line
            # alone never reached them.
            if completed.returncode == PKEXEC_NOT_AUTHORIZED:
                self.last_provision_failure = "not-authorized"
            elif completed.returncode == PKEXEC_COMMAND_NOT_EXECUTED:
                self.last_provision_failure = "runner-missing"
            else:
                self.last_provision_failure = "dispatcher-refused"
            log.error(
                "Model install via pkexec failed (rc=%d, %s): %s%s",
                completed.returncode, self.last_provision_failure,
                completed.stdout.strip(), completed.stderr.strip(),
            )
            return False
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)

    def _cap_unpinned_to_highest_pinned(self, model: ModelInfo) -> ModelInfo:
        """Cap a recommendation at the highest PINNED tier.

        A model with no shipped pin cannot be downloaded OR load-verified —
        both fail closed without a pin — so recommending it dead-ends the
        install at "no pin." This returns the model for the highest tier
        at-or-below ``model.tier`` that DOES have a shipped pin. As of the
        manifest this ships with, every catalog model is pinned — 2B, 9B and
        35B — so this cap does not fire at all on a current install and a
        Tier-3 box is served the 35B itself. It stays because a manifest that
        drops a pin must degrade rather than dead-end. If NOTHING is pinned
        (e.g. an empty
        manifest in a test/early-install state) the model is returned unchanged
        so the existing fail-closed download path still applies rather than
        silently swapping in a wrong model.
        """
        if self._pins.get(model.filename, ""):
            return model
        for t in sorted((t for t in MODEL_CATALOG if t.value <= model.tier.value),
                        key=lambda t: t.value, reverse=True):
            alt = MODEL_CATALOG[t]
            if self._pins.get(alt.filename, ""):
                log.warning(
                    "Model %s (Tier %d) has no shipped pin; capping the "
                    "recommendation to %s (Tier %d, the highest pinned tier) so "
                    "the install does not dead-end at 'no pin'.",
                    model.name, model.tier.value, alt.name, t.value,
                )
                return alt
        return model

    def get_model_by_name(self, name: str) -> ModelInfo | None:
        """Find a model by name across all tiers."""
        for model in MODEL_CATALOG.values():
            if model.name == name:
                # Never hand back an unpinned model — cap to the highest
                # pinned tier so a download/recommendation can't dead-end.
                # replace() returns a COPY so the overlays below (sha256 /
                # local_path / downloaded) never mutate the shared MODEL_CATALOG
                # singleton, which previously leaked state across callers (and
                # across tests).
                model = replace(self._cap_unpinned_to_highest_pinned(model))
                # T0-4-D — overlay the canonical pin from the package-
                # shipped manifest. Empty pin means downstream
                # operations will refuse (fail-closed per I-005 closure).
                self._apply_manifest(model)
                # Derive downloaded/local_path strictly from disk (see
                # get_model_for_tier) so a copied stale flag can't leak.
                local_path = self._model_dir / model.filename
                model.downloaded = local_path.exists()
                model.local_path = str(local_path) if model.downloaded else None
                self._derive_mmproj_local_path(model)
                return model
        return None

    def get_model_for_tier(self, tier: HardwareTierLevel) -> ModelInfo:
        """Return the recommended model for a hardware tier."""
        # Never recommend an unpinned model — cap to the highest pinned tier
        # rather than dead-ending at "no pin." With the shipped manifest, which
        # pins all three catalog models, the cap does not fire and a Tier-3 box
        # is served the 35B; the DISPATCH lane it runs in is a separate
        # decision, and dispatch_policy floors it (see
        # intergen/tests/test_tier3_dispatch_posture.py). replace() returns a
        # COPY so the overlays below never mutate the shared MODEL_CATALOG
        # singleton.
        model = replace(self._cap_unpinned_to_highest_pinned(MODEL_CATALOG[tier]))

        # T0-4-D — overlay the canonical pin from the package-shipped
        # manifest. The local /var/lib/intergen/models/manifest.json
        # remains as a download-tracking sidecar but is no longer the
        # SoT for sha256 (it records computed hashes for already-trusted
        # downloads; the pinning manifest is the trust anchor).
        self._apply_manifest(model)

        # Check if already downloaded — set authoritatively from disk. replace()
        # above copies the shared-catalog singleton's downloaded/local_path, and
        # a mutating download path elsewhere can leave the singleton's flag set;
        # deriving strictly from file existence here keeps a stale flag from
        # leaking into a fresh lookup (cross-caller / test-pollution safety).
        local_path = self._model_dir / model.filename
        model.downloaded = local_path.exists()
        model.local_path = str(local_path) if model.downloaded else None
        self._derive_mmproj_local_path(model)

        return model

    def resolve_for_detected(self, tier: HardwareTier) -> ModelInfo | None:
        """The ONE model-resolution path shared by `intergen setup` and the daemon.

        Resolve the model the DETECTOR RECOMMENDS: ``get_model_by_name(recommended)``,
        which (a) honors the within-tier CPU-only/iGPU latency adjustment — an
        integrated-GPU Tier-2 box recommends the 2B, not the 9B — and (b) applies
        the unpinned->highest-pinned cap. That cap was what sent a Tier-3 35B
        recommendation to the 9B (the PI-Z13 fix, now carried by
        ``get_model_by_name`` itself); the shipped manifest pins the 35B, so the
        cap no longer fires and a Tier-3 recommendation resolves to the 35B.
        Fall back to the bare tier lookup ONLY when the recommended name is
        unknown to the catalog.

        Onboarding (setup) and engine-start (daemon) MUST resolve through THIS one
        path or they drift: setup downloads the recommended model while the daemon
        looks a different one up by bare tier, finds it absent, and the engine
        never starts on a populated, pin-verified store (the ge9b-01 iGPU-Tier-2
        dead-end). Tiering is data-decided — the recommendation IS the data
        speaking; a code path that second-guesses it is the defect.

        Downloaded walk-down (the belt under detection, mirroring dispatch's
        shipped-lane walk-down): when the recommended model is NOT on disk but
        a smaller tier's model IS, serve the largest downloaded model instead
        of dead-ending at "No model downloaded" — a detected-Tier-3 box whose
        store holds only the 9B serves the 9B, loudly. When nothing smaller is
        downloaded either, the recommendation is returned unchanged (the same
        loud dead-end as before — never a silent substitution).
        """
        model = self.get_model_by_name(tier.recommended_model)
        if model is None:
            model = self.get_model_for_tier(tier.tier)
        if model is not None and not model.downloaded:
            fallback = self._largest_downloaded_below(model.tier)
            if fallback is not None:
                log.warning(
                    "Model walk-down: the recommended %s (tier %d) is not "
                    "downloaded — serving the largest downloaded model %s "
                    "(tier %d) instead",
                    model.name, model.tier.value,
                    fallback.name, fallback.tier.value,
                )
                return fallback
        return model

    def _largest_downloaded_below(
        self, below: HardwareTierLevel
    ) -> ModelInfo | None:
        """The largest catalog model STRICTLY below ``below`` that is on disk.

        Walks tiers top-down; each candidate goes through get_model_for_tier
        so the pin overlay + disk-derived downloaded/local_path/mmproj fields
        are applied exactly as the primary path applies them. Returns None
        when no smaller tier's model is downloaded.
        """
        for level in sorted(
            HardwareTierLevel, key=lambda lv: lv.value, reverse=True
        ):
            if level.value >= below.value:
                continue
            candidate = self.get_model_for_tier(level)
            if candidate is not None and candidate.downloaded:
                return candidate
        return None

    def _derive_mmproj_local_path(self, model: ModelInfo) -> None:
        """Set model.mmproj_local_path strictly from disk.

        The privileged install places the paired projector at
        <model_dir>/<mmproj_filename> (same shape as the GGUF), but
        mmproj_local_path is set only on the download/install paths — so a FRESH
        daemon lookup of an ALREADY-installed vision model would return None and
        the launch would pass mmproj_path=None, making a has_vision model serve
        SILENTLY text-only. Derive it from disk in lockstep with local_path so
        the launch-time has_vision integrity guard sees the real installed
        projector. A non-vision model (or a vision model whose projector is
        absent) gets None, never a stale singleton copy.
        """
        if model.has_vision and model.mmproj_filename:
            mmproj_path = self._model_dir / model.mmproj_filename
            model.mmproj_local_path = (
                str(mmproj_path) if mmproj_path.exists() else None
            )
        else:
            model.mmproj_local_path = None

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
        removed per I-005 closure: any MITM / compromised
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

        # Read the file once per process per file identity. The comparison
        # against the caller's pin happens below either way — what is skipped
        # is re-reading bytes this process has already read, not the check.
        identity = _file_identity(path)
        computed = cached_digest(identity)
        if computed is not None:
            log.info(
                "SHA256 for %s already computed in this process from the same "
                "file (same path, inode, size and mtime) — comparing against "
                "the pin without re-reading it",
                model.filename,
            )
        else:
            log.info("Verifying SHA256 for %s...", model.filename)
            sha256 = hashlib.sha256()
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    sha256.update(chunk)

            computed = sha256.hexdigest()
            record_digest(identity, computed)

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
        """Build the HuggingFace (vendor) download URL for a model.

        Uses the vendor-side filename when the manifest declares one — the
        mirror disambiguates upstream's generic per-repo names (e.g. every
        unsloth projector is served as mmproj-F16.gguf), so the mirror name
        does not exist on the vendor repo. The sha256 pin gates the bytes
        identically on either source.
        """
        fn = getattr(model, "vendor_filename", None) or model.filename
        return (
            f"https://huggingface.co/{model.repo_id}"
            f"/resolve/main/{fn}"
        )

    def _mirror_url(self, model: ModelInfo) -> str:
        """Build the InterGenOS-mirror (fallback) download URL for a model.

        The mirror hosts the same SHA-pinned GGUFs flat by filename under
        MIRROR_BASE_URL. Filenames are unique per model, so no repo_id path
        component is needed; the pin is the trust anchor either way.
        """
        return f"{MIRROR_BASE_URL}/{model.filename}"

    def _fetch_and_verify(
        self,
        url: str,
        local_path: Path,
        expected_sha: str,
        progress_callback: Callable | None = None,
    ) -> bool:
        """Stream-download ``url`` to ``local_path``, verifying against the pin.

        Fail-closed: returns False (and removes any partial or mismatched
        file) on ANY network/HTTP error OR a SHA256 mismatch. ``expected_sha``
        is the package-shipped manifest pin (the caller's pre-download gate
        guarantees it is non-empty), so a downloaded file that does not match
        the pin is rejected regardless of which source served it — vendor or
        mirror. Returns True only when the file is fully written AND its hash
        equals the pin.
        """
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
            if expected_sha != computed_hash:
                log.error(
                    "SHA256 mismatch for %s from %s: expected %s, got %s",
                    local_path.name, url, expected_sha, computed_hash,
                )
                self.last_download_failure = "pin-mismatch"
                local_path.unlink(missing_ok=True)
                return False

            log.info(
                "Download complete + pin-verified: %s (SHA256: %s)",
                local_path.name, computed_hash,
            )
            return True

        except Exception as e:
            # Name the cause rather than only the exception. A download that
            # dies because the name server stopped answering is a different
            # problem from one that dies because the server hung up, and the
            # log is where that distinction has to survive — it is what the
            # caller turns into the sentence the user reads.
            cause = net_diagnostics.classify_exception(e)
            self.last_download_failure = cause
            if cause == net_diagnostics.NAME_RESOLUTION:
                log.error(
                    "Download failed from %s: the host name could not be "
                    "looked up (%s). The network connection is not "
                    "necessarily the problem — the name server is not "
                    "answering.", url, e,
                )
            else:
                log.error("Download failed from %s: %s", url, e)
            local_path.unlink(missing_ok=True)
            return False

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
