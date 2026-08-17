# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Privileged model-storage provisioner — pkexec target for `intergen setup`.

`intergen setup` runs as the (unprivileged) user, but the model store at
/var/lib/intergen/models is system-wide root-owned read-only BY DESIGN (HG: a
verified model the user cannot tamper with — load-time verify_model trusts that
RO path). So the final install of a downloaded model into that store crosses a
privilege boundary. This module is that root-side entry point.

Trust model — DELIBERATELY SIMPLER than the AI-6 tool-dispatch path
(intergen.privileged_dispatch): this is a HUMAN-AT-THE-KEYBOARD setup action, not
an LLM tool call. There is no ingress, no provenance gate, and no dispatch token
to verify — the human who typed `intergen setup` authenticated to PolicyKit
directly (org.intergenos.intergen.provision-model-storage). What root MUST NOT do
is trust the caller's claim that the staged file is the right model: it
re-derives the canonical sha256 pin from its OWN trust anchor
(/usr/share/intergen/models-manifest.json, on the dm-verity RO image) and refuses
to install anything that does not match (I-005: no TOFU, no unverified
model ever lands in the store — the same pin gate the unprivileged side ran, run
again at the privilege boundary because the privileged context cannot trust the
caller's prior check in the abstract).

Argv contract (from intergen-model-setup-runner):

    python3 -m intergen.model_setup_dispatch <args_json>

  args_json — a JSON object:
    {"filename": "<model.gguf>", "staging_path": "<absolute path>",
     "mmproj_filename": "<mmproj.gguf>",        # OPTIONAL (vision models)
     "mmproj_staging_path": "<absolute path>"}  # OPTIONAL, paired with above
    filename     — basename of a model pinned in the shipped manifest. No path
                   components (traversal is rejected).
    staging_path — absolute path to the user-downloaded, already-pin-verified
                   file in a user-writable staging dir. Root RE-verifies it.
    mmproj_*     — OPTIONAL paired vision projector (a vision model ships two
                   pinned artifacts). When present, both are validated against
                   their manifest pins BEFORE either is installed, and both ride
                   THIS single pkexec escalation (no second auth prompt).

Environment (set by the runner from pkexec):

    PKEXEC_UID — calling user's uid. Its presence proves we are inside the
                 runner; absence means a direct invocation (bug/bypass) → refuse.

Exit code: 0 install succeeded / 1 refusal or failure / 2 argv shape wrong.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

from intergen.model_manager import (
    MODEL_DIR,
    MANIFEST_PATH,
    PINS_MANIFEST_PATH,
    SYSTEM_LEGAL_DIR,
    MODEL_CATALOG,
    ModelManager,
    APACHE_LICENSE_REF,
    QWEN_LICENSE_REF,
    QWEN_LICENSE_URL,
    _load_pins,
    _model_license_ref,
)

#: Directory the model store lives in (root-owned, RO to users). Same constant
#: the unprivileged ModelManager writes-or-reads; imported so there is one SoT.
_MODEL_DIR = MODEL_DIR
_MANIFEST_PATH = MANIFEST_PATH
_SYSTEM_LEGAL_DIR = SYSTEM_LEGAL_DIR

_SHA_CHUNK = 1024 * 1024  # 1 MiB streaming read for the integrity re-hash.


def _emit(message: str) -> None:
    """Print to stdout (the caller — pkexec/subprocess — captures this)."""
    print(message)


def _fail(message: str, exit_code: int = 1) -> int:
    """Print a human-readable refusal/error and return the exit code."""
    _emit(message)
    return exit_code


def _sha256_file(path: Path) -> str:
    """Stream-hash a file (1 MiB chunks) — the model is ~1.3 GB; don't slurp."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_SHA_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_filename(filename: str) -> bool:
    """A model filename must be a bare basename — no path traversal, no slashes.

    The store path is built as _MODEL_DIR / filename; a filename carrying ``/``
    or ``..`` could escape the store. Reject anything that is not exactly its own
    basename (this also rejects '', '.', '..', and absolute paths).
    """
    if not filename or filename in (".", ".."):
        return False
    if "/" in filename or "\0" in filename:
        return False
    return os.path.basename(filename) == filename


def _validate_staged(filename: str, staging_path: str,
                     pins: dict) -> tuple[str, str]:
    """Re-derive + re-verify a staged file's pin at the privilege boundary.

    Pure (no write side-effects) so the caller can validate EVERY artifact
    before installing ANY — a vision model's GGUF + mmproj both clear the pin
    gate before either lands. Returns (expected_sha, "") on success, or
    ("", refusal_message) on any failure (unsafe name / no pin / missing staged
    file / sha mismatch). No-TOFU, I-005 — root trusts only its own pin.
    """
    if not _safe_filename(filename):
        return "", (f"provision: refusing unsafe filename {filename!r} "
                    "(must be a bare basename, no path components).")
    expected_sha = pins.get(filename, "")
    if not expected_sha:
        return "", (f"provision: no pin for {filename!r}; refusing to install "
                    "an unpinned/unknown artifact.")
    staged = Path(staging_path)
    if not staged.is_absolute():
        return "", f"provision: staging_path must be absolute; got {staging_path!r}."
    if not staged.is_file():
        return "", f"provision: staged file not found: {staged}."
    actual_sha = _sha256_file(staged)
    if actual_sha != expected_sha:
        return "", (
            f"provision: sha256 mismatch for {filename} — refusing install. "
            f"expected {expected_sha[:16]}…, got {actual_sha[:16]}…. The staged "
            "file is not the pinned artifact; nothing was written to the store."
        )
    return expected_sha, ""


def _install_staged(filename: str, staging_path: str,
                    model_dir: Path) -> tuple[bool, str]:
    """Atomic root-owned 0644 install of an ALREADY-VALIDATED staged file.

    Returns (True, str(dest)) or (False, error_message). Same-dir temp +
    os.replace so the store never exposes a partial file; the euid guard keeps
    unit tests (unprivileged) exercising the path without EPERM while production
    (pkexec/root) pins it root:root.
    """
    staged = Path(staging_path)
    dest = model_dir / filename
    tmp = model_dir / f".{filename}.incoming"
    try:
        model_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(model_dir, 0o755)
        shutil.copyfile(staged, tmp)
        os.chmod(tmp, 0o644)
        if os.geteuid() == 0:
            os.chown(tmp, 0, 0)
        os.replace(tmp, dest)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False, f"provision: install of {filename} failed: {exc}."
    return True, str(dest)


def provision(
    arguments: dict,
    *,
    pins_path: Path = PINS_MANIFEST_PATH,
    model_dir: Path = _MODEL_DIR,
    manifest_path: Path = _MANIFEST_PATH,
    system_legal_dir: Path = _SYSTEM_LEGAL_DIR,
    accepted_by: str = "",
) -> tuple[bool, str]:
    """Validate + install a staged model into the root-owned store.

    Pure of argv/env so it is unit-testable with injected paths. Returns
    (ok, message). The store, manifest, and system-legal paths are injectable
    for tests; production uses the module-default root paths.

    Steps (fail-closed at each): filename safety → pin lookup (no pin ⇒ refuse,
    no TOFU) → staged-file existence → sha256 RE-verify vs pin (mismatch ⇒
    refuse) → atomic install root-owned 0644 → manifest sidecar update →
    system-wide license-acceptance record (for licenses that require it).
    """
    filename = arguments.get("filename")
    staging_path = arguments.get("staging_path")
    if not isinstance(filename, str) or not isinstance(staging_path, str):
        return False, ("provision: args_json must carry string 'filename' and "
                       "'staging_path'.")

    # A vision model rides a SECOND pinned artifact (the mmproj projector) in
    # the SAME pkexec escalation — both validate + install here, or neither.
    mmproj_filename = arguments.get("mmproj_filename")
    mmproj_staging_path = arguments.get("mmproj_staging_path")
    has_mmproj = mmproj_filename is not None or mmproj_staging_path is not None
    if has_mmproj and not (isinstance(mmproj_filename, str)
                           and isinstance(mmproj_staging_path, str)):
        return False, ("provision: a paired projector needs string "
                       "'mmproj_filename' AND 'mmproj_staging_path'.")

    # Pin gate — re-derive every artifact's canonical sha256 from ROOT's own
    # trust anchor and re-verify BEFORE writing anything to the store (no TOFU,
    # I-005). The projector is validated up front too, so a bad mmproj aborts
    # the whole install rather than leaving a vision GGUF in the store without
    # its projector.
    pins = _load_pins(pins_path)
    expected_sha, err = _validate_staged(filename, staging_path, pins)
    if err:
        return False, err
    if has_mmproj:
        _mmproj_sha, err = _validate_staged(
            mmproj_filename, mmproj_staging_path, pins)
        if err:
            return False, err

    # Integrity proven for every artifact → install (primary, then projector),
    # each root-owned 0644 via a same-dir temp + atomic os.replace.
    ok, primary = _install_staged(filename, staging_path, model_dir)
    if not ok:
        return False, primary
    dest = Path(primary)
    mmproj_dest: Path | None = None
    if has_mmproj:
        ok, mmproj_out = _install_staged(
            mmproj_filename, mmproj_staging_path, model_dir)
        if not ok:
            # The primary GGUF already landed; remove it so a projector-install
            # failure leaves NEITHER artifact — parity with the sha-mismatch
            # path (which validates both before installing either). Otherwise
            # get_model_for_tier derives downloaded=True from the on-disk GGUF
            # and a daemon without the has_vision-requires-mmproj launch guard
            # would serve the projector-less model silently text-only.
            # Best-effort: a failed unlink is no worse than the orphan it clears.
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass
            return False, mmproj_out
        mmproj_dest = Path(mmproj_out)

    # Manifest sidecar — record the install via the same writer the unprivileged
    # side uses, so list_downloaded() stays consistent. Look the structural
    # metadata up by filename from the catalog; the pin is the trust anchor.
    catalog_model = next(
        (m for m in MODEL_CATALOG.values() if m.filename == filename), None
    )
    if catalog_model is not None:
        try:
            mm = ModelManager(model_dir=model_dir, manifest_path=manifest_path,
                              pins_path=pins_path)
            catalog_model.sha256 = expected_sha
            catalog_model.local_path = str(dest)
            catalog_model.downloaded = True
            if mmproj_dest is not None:
                catalog_model.mmproj_local_path = str(mmproj_dest)
            mm._update_manifest(catalog_model)
        except OSError as exc:
            # The file IS installed + verified — a manifest-write hiccup is not
            # fatal (get_model_for_tier checks the file on disk, not the
            # manifest). Report it but treat the install as succeeded.
            _emit(f"provision: note — manifest sidecar update failed: {exc}.")

    # System-wide license acceptance — complete the setup write-set under
    # /var/lib/intergen (the .gguf + manifest above, the legal record here). The
    # store is system-wide; its license acceptance belongs at system scope too,
    # the same record Forge writes at install time. The human who authenticated
    # this pkexec install ("install InterGen's AI model") is accepting the
    # model's license for the system — so we write it root-owned, attributed to
    # them. Derived entirely from trusted catalog data (license_ref from the
    # model's repo_id; no caller-supplied license content), and only for licenses
    # that actually require acceptance (Apache and other permissive licenses are
    # auto-accepted, so no record is written for them — matching
    # ModelManager.record_license_acceptance).
    if catalog_model is not None:
        note = _write_system_acceptance(
            catalog_model, expected_sha, system_legal_dir, accepted_by,
        )
        if note:
            _emit(note)

    msg = f"provision: installed {filename} ({expected_sha[:16]}…) to {dest}."
    if mmproj_dest is not None:
        msg += f" + projector {mmproj_filename} to {mmproj_dest}."
    return True, msg


def _write_system_acceptance(model, sha256: str, system_legal_dir: Path,
                            accepted_by: str) -> str:
    """Write the system-wide license-acceptance record (root-owned).

    No-op for permissive licenses (returns ""). For acceptance-requiring
    licenses, writes <system_legal_dir>/<filename>-accepted.json with the same
    schema as ModelManager.record_license_acceptance, attributed to the
    authenticating user. Returns a human-readable note on a non-fatal failure
    (the model is already installed; a legal-record hiccup does not fail the
    install — but it IS surfaced).
    """
    license_ref = _model_license_ref(model)
    if license_ref == APACHE_LICENSE_REF:
        return ""  # permissive — auto-accepted, no record needed.

    record = {
        "model": model.name,
        "filename": model.filename,
        "repo_id": model.repo_id,
        "license_ref": license_ref,
        "canonical_url": (
            QWEN_LICENSE_URL if license_ref == QWEN_LICENSE_REF else "unknown"
        ),
        "accepted_at": datetime.datetime.now(
            datetime.timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "accepted_by": accepted_by or os.environ.get("PKEXEC_USER", "unknown"),
        "scope": "system",  # distinguishes from a per-user XDG record.
    }
    target = system_legal_dir / f"{model.filename}-accepted.json"
    try:
        system_legal_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(system_legal_dir, 0o755)
        tmp = system_legal_dir / f".{model.filename}-accepted.json.incoming"
        tmp.write_text(json.dumps(record, indent=2) + "\n")
        os.chmod(tmp, 0o644)
        if os.geteuid() == 0:
            os.chown(tmp, 0, 0)
        os.replace(tmp, target)
    except OSError as exc:
        return (f"provision: note — system license-acceptance record write "
                f"failed ({exc}); model is installed, record it via Forge or "
                "re-run setup.")
    return f"provision: recorded system license acceptance ({license_ref}) at {target}."


def main(argv: list[str] | None = None) -> int:
    """Entry point. 0 success / 1 refusal-or-failure / 2 argv shape wrong."""
    args = argv if argv is not None else sys.argv[1:]

    if len(args) != 1:
        return _fail(
            "model_setup_dispatch: usage: python3 -m intergen.model_setup_dispatch "
            "<args_json> (exactly 1 arg)",
            exit_code=2,
        )

    # PKEXEC_UID is set by the runner shim; absence means we were invoked outside
    # the pkexec runner — a bug or a bypass attempt. Refuse rather than run.
    if not os.environ.get("PKEXEC_UID"):
        return _fail(
            "model_setup_dispatch: PKEXEC_UID unset; refusing to run outside the "
            "pkexec runner context."
        )

    try:
        arguments = json.loads(args[0])
    except json.JSONDecodeError as exc:
        return _fail(f"model_setup_dispatch: args_json is not valid JSON: {exc}.")
    if not isinstance(arguments, dict):
        return _fail(
            "model_setup_dispatch: args_json must decode to a JSON object; got "
            f"{type(arguments).__name__}."
        )

    ok, message = provision(
        arguments, accepted_by=os.environ.get("PKEXEC_USER", ""),
    )
    _emit(message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
