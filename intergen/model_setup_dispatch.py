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
    record_only  — OPTIONAL boolean. True re-verifies the installed filename in
                   the trusted model store and repairs only its legal record.
                   No staging or projector arguments are accepted in this mode;
                   no model is downloaded or copied and no sidecar is updated.

Environment (set by the runner from pkexec):

    PKEXEC_UID — calling user's uid. Its presence proves we are inside the
                 runner; absence means a direct invocation (bug/bypass) → refuse.
                 A staged file is read only when this uid owns it.

The bytes installed are the bytes verified. A staged file sits in a directory
the caller's account writes, so its path can name another file, or a link, a
moment after it was hashed. Root therefore opens each staged file ONCE, without
following a link at its name, requires a regular file the caller owns, hashes
it from that descriptor, copies it from the same descriptor into the store's
temporary file, and hashes that temporary file again before it replaces the
store's entry. The path is never opened a second time.

Exit code: 0 install succeeded / 1 refusal or failure / 2 argv shape wrong.
"""

from __future__ import annotations

import datetime
import errno
import hashlib
import json
import os
import stat
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from intergen.model_manager import (
    MODEL_DIR,
    MANIFEST_PATH,
    PINS_MANIFEST_PATH,
    SYSTEM_LEGAL_DIR,
    MODEL_CATALOG,
    EMBEDDING_MODEL,
    ModelManager,
    APACHE_LICENSE_REF,
    QWEN_LICENSE_REF,
    QWEN_LICENSE_URL,
    _load_manifest_entries,
    _pins_from_entries,
)

#: Directory the model store lives in (root-owned, RO to users). Same constant
#: the unprivileged ModelManager writes-or-reads; imported so there is one SoT.
_MODEL_DIR = MODEL_DIR
_MANIFEST_PATH = MANIFEST_PATH
_SYSTEM_LEGAL_DIR = SYSTEM_LEGAL_DIR

_SHA_CHUNK = 1024 * 1024  # 1 MiB streaming read for the integrity re-hash.

# URLs follow the descriptor's explicit license identifier, never a repository
# name. A new license needs a reviewed mapping before provisioning can record it.
_LICENSE_URLS = {
    APACHE_LICENSE_REF: "https://www.apache.org/licenses/LICENSE-2.0",
    QWEN_LICENSE_REF: QWEN_LICENSE_URL,
}


def _emit(message: str) -> None:
    """Print to stdout (the caller — pkexec/subprocess — captures this)."""
    print(message)


def _fail(message: str, exit_code: int = 1) -> int:
    """Print a human-readable refusal/error and return the exit code."""
    _emit(message)
    return exit_code


def _sha256_descriptor(fd: int) -> str:
    """Stream-hash an open file from its start (1 MiB chunks) — the model is
    ~1.3 GB; don't slurp. Reads the descriptor, never a path."""
    h = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    for chunk in iter(lambda: os.read(fd, _SHA_CHUNK), b""):
        h.update(chunk)
    return h.hexdigest()


@dataclass
class _Verified:
    """A staged artifact opened once and hashed to its pin. `fd` is the
    descriptor every later read of it is made from; provision closes it."""

    fd: int
    path: Path
    sha256: str

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


def _open_artifact(path: Path, owner_uid: int | None) -> tuple[int, str]:
    """Open an artifact root is about to verify, once, as the file itself.

    O_NOFOLLOW refuses a link at its name and O_NONBLOCK keeps a FIFO put there
    from holding the open; the descriptor's own fstat then requires a regular
    file and, when `owner_uid` is given, one that uid owns, so a root-only file
    or another account's is refused before a byte of it is read. Returns
    (fd, "") or (-1, refusal).
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                     | os.O_CLOEXEC)
    except FileNotFoundError:
        return -1, f"provision: model artifact not found: {path}."
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return -1, (f"provision: refusing {path}: it is a symbolic link, and "
                        "a staged file is read only as the file itself.")
        raise
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            reason = f"provision: refusing {path}: it is not a regular file."
        elif owner_uid is not None and info.st_uid != owner_uid:
            reason = (f"provision: refusing {path}: it is owned by uid "
                      f"{info.st_uid}, not by the calling user (uid {owner_uid}).")
        else:
            return fd, ""
    except BaseException:
        os.close(fd)
        raise
    os.close(fd)
    return -1, reason


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


def _validate_staged(filename: str, staging_path: str, pins: dict,
                     owner_uid: int | None) -> tuple[_Verified | None, str]:
    """Re-derive + re-verify a staged file's pin at the privilege boundary.

    No write side-effects, so the caller can validate EVERY artifact before
    installing ANY — a vision model's GGUF + mmproj both clear the pin gate
    before either lands. The file is opened once (_open_artifact; `owner_uid`
    is the uid that must own it) and hashed from that descriptor, which the
    returned _Verified holds open for _install_staged to copy from. Returns
    (verified, "") on success, or (None, refusal_message) on any failure
    (unsafe name / no pin / missing, linked, irregular or foreign staged file /
    sha mismatch). No-TOFU, I-005 — root trusts only its own pin.
    """
    if not _safe_filename(filename):
        return None, (f"provision: refusing unsafe filename {filename!r} "
                      "(must be a bare basename, no path components).")
    expected_sha = pins.get(filename, "")
    if not expected_sha:
        return None, (f"provision: no pin for {filename!r}; refusing "
                      "an unpinned/unknown artifact.")
    staged = Path(staging_path)
    if not staged.is_absolute():
        return None, f"provision: staging_path must be absolute; got {staging_path!r}."
    fd, err = _open_artifact(staged, owner_uid)
    if err:
        return None, err
    try:
        actual_sha = _sha256_descriptor(fd)
    except BaseException:
        os.close(fd)
        raise
    if actual_sha != expected_sha:
        os.close(fd)
        return None, (
            f"provision: sha256 mismatch for {filename} — refusing request. "
            f"expected {expected_sha[:16]}…, got {actual_sha[:16]}…. The staged "
            "or installed file is not the pinned artifact; nothing was written to the store."
        )
    return _Verified(fd=fd, path=staged, sha256=expected_sha), ""


def _install_staged(filename: str, verified: _Verified,
                    model_dir: Path) -> tuple[bool, str]:
    """Atomic root-owned 0644 install of an ALREADY-VALIDATED staged file.

    The bytes are copied from the descriptor _validate_staged hashed, never
    from the staged path again, into a same-dir temp, and that temp is hashed
    again from its own descriptor before os.replace, so what lands is the
    pinned artifact or nothing does; the store never exposes a partial file.
    Returns (True, str(dest)) or (False, error_message). The euid guard keeps
    unit tests (unprivileged) exercising the path without EPERM while production
    (pkexec/root) pins it root:root.
    """
    dest = model_dir / filename
    tmp = model_dir / f".{filename}.incoming"
    out = -1
    try:
        model_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(model_dir, 0o755)
        tmp.unlink(missing_ok=True)  # an interrupted run's; only root writes here
        out = os.open(tmp, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                      | os.O_CLOEXEC, 0o600)
        os.lseek(verified.fd, 0, os.SEEK_SET)
        for chunk in iter(lambda: os.read(verified.fd, _SHA_CHUNK), b""):
            view = memoryview(chunk)
            while view:
                view = view[os.write(out, view):]
        os.fsync(out)
        landed_sha = _sha256_descriptor(out)
        if landed_sha != verified.sha256:
            os.close(out)
            out = -1
            tmp.unlink(missing_ok=True)
            return False, (
                f"provision: the copy of {filename} does not hash to its pin "
                f"(expected {verified.sha256[:16]}…, got {landed_sha[:16]}…): "
                f"{verified.path} changed after it was verified; nothing was "
                "installed.")
        os.fchmod(out, 0o644)
        if os.geteuid() == 0:
            os.fchown(out, 0, 0)
        os.close(out)
        out = -1
        os.replace(tmp, dest)
    except OSError as exc:
        if out >= 0:
            os.close(out)
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
    caller_uid: int | None = None,
) -> tuple[bool, str]:
    """Validate + install a staged model into the root-owned store.

    Pure of argv/env so it is unit-testable with injected paths. Returns
    (ok, message). The store, manifest, and system-legal paths are injectable
    for tests; production uses the module-default root paths. caller_uid is
    the authenticated caller's uid (main passes PKEXEC_UID); a staged file is
    read only when that uid owns it, and an install without it is refused.

    Steps (fail-closed at each): filename safety → pin lookup (no pin ⇒ refuse,
    no TOFU) → the staged file opened once, a regular file the caller owns →
    sha256 RE-verify vs pin from that descriptor (mismatch ⇒ refuse) → atomic
    install root-owned 0644, copied from the same descriptor and re-hashed →
    manifest sidecar update → system-wide license record from the
    package-shipped descriptor.

    record_only checks the installed artifact through the same pin gate, then
    repairs its legal record or leaves an already-correct record untouched.
    This mode never installs a model or updates the manifest sidecar.
    """
    filename = arguments.get("filename")
    record_only = arguments.get("record_only", False)
    if not isinstance(record_only, bool):
        return False, "provision: record_only must be a boolean; refusing request."
    if not isinstance(filename, str):
        return False, "provision: args_json must carry a string 'filename'."
    if record_only:
        if any(key in arguments for key in
               ("staging_path", "mmproj_filename", "mmproj_staging_path")):
            return False, ("provision: record-only verification accepts no "
                           "staging or projector paths; refusing request.")
        # The caller names an artifact, never its location. _validate_staged
        # checks the basename before it opens this trusted-store path.
        staging_path = str(model_dir / filename)
    else:
        staging_path = arguments.get("staging_path")
    if not isinstance(staging_path, str):
        return False, ("provision: args_json must carry string 'filename' and "
                       "'staging_path'.")
    if not record_only and caller_uid is None:
        return False, ("provision: the calling user's uid is not known, so a "
                       "staged file cannot be checked as theirs; refusing request.")
    # A staged file must be the caller's own; the store's entry is root's, in a
    # directory only root writes.
    owner_uid = None if record_only else caller_uid

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
    # Use the same descriptor snapshot for artifact pins and the legal record.
    # This loader relies on package verification;
    # it does not perform detached-signature verification here.
    entries = _load_manifest_entries(pins_path)
    pins = _pins_from_entries(entries)
    # Each artifact is held open from its hash to its copy; closed on every exit.
    held: list[_Verified] = []
    try:
        try:
            primary, err = _validate_staged(filename, staging_path, pins, owner_uid)
        except OSError as exc:
            return False, f"provision: cannot verify {filename!r}: {exc}; refusing request."
        if err:
            return False, err
        held.append(primary)
        expected_sha = primary.sha256
        projector: _Verified | None = None
        if has_mmproj:
            try:
                projector, err = _validate_staged(
                    mmproj_filename, mmproj_staging_path, pins, owner_uid)
            except OSError as exc:
                return False, (f"provision: cannot verify {mmproj_filename!r}: "
                               f"{exc}; refusing request.")
            if err:
                return False, err
            held.append(projector)

        entry = entries.get(filename)
        if (entry is None or entry.get("filename") != filename
                or entry.get("sha256") != expected_sha):
            return False, (f"provision: {filename!r} has no matching descriptor "
                           "entry for its filename and sha256; refusing request.")
        license_ref = entry.get("license_ref")
        if not isinstance(license_ref, str) or license_ref not in _LICENSE_URLS:
            return False, (f"provision: {filename!r} has a missing or unsupported "
                           f"descriptor license_ref {license_ref!r}; refusing request.")

        if record_only:
            # No model/sidecar write occurs on this path. A record-write failure
            # must fail setup too; it cannot inherit provision's non-fatal note.
            return _write_system_acceptance(
                entry, expected_sha, system_legal_dir, accepted_by,
                pins_path=pins_path, preserve_correct=True,
            )

        # Integrity proven for every artifact → install (primary, then projector),
        # each root-owned 0644 via a same-dir temp + atomic os.replace.
        ok, primary_out = _install_staged(filename, primary, model_dir)
        if not ok:
            return False, primary_out
        dest = Path(primary_out)
        mmproj_dest: Path | None = None
        if projector is not None:
            ok, mmproj_out = _install_staged(
                mmproj_filename, projector, model_dir)
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
            (replace(m) for m in [*MODEL_CATALOG.values(), EMBEDDING_MODEL]
             if m.filename == filename), None
        )
        if catalog_model is not None:
            try:
                mm = ModelManager(model_dir=model_dir, manifest_path=manifest_path,
                                  pins_path=pins_path)
                catalog_model.sha256 = expected_sha
                catalog_model.local_path = str(dest)
                catalog_model.downloaded = True
                if mmproj_dest is not None:
                    catalog_model.mmproj_filename = mmproj_filename
                    catalog_model.mmproj_sha256 = projector.sha256
                    catalog_model.mmproj_local_path = str(mmproj_dest)
                if not mm._update_manifest(catalog_model):
                    return False, "provision: files installed, but runtime manifest write failed."
            except OSError as exc:
                return False, f"provision: files installed, but runtime manifest update failed: {exc}."

        # Record the descriptor's license even for permissive models, replacing an
        # inaccurate older record when setup is rerun. Catalog defaults and caller
        # arguments do not supply legal metadata.
        _record_ok, note = _write_system_acceptance(
            entry, expected_sha, system_legal_dir, accepted_by, pins_path=pins_path,
        )
        if note:
            _emit(note)

        msg = f"provision: installed {filename} ({expected_sha[:16]}…) to {dest}."
        if mmproj_dest is not None:
            msg += f" + projector {mmproj_filename} to {mmproj_dest}."
        return True, msg
    finally:
        for artifact in held:
            artifact.close()


def _write_system_acceptance(entry: dict, sha256: str, system_legal_dir: Path,
                            accepted_by: str, *, pins_path: Path,
                            preserve_correct: bool = False) -> tuple[bool, str]:
    """Write the system-wide license-acceptance record (root-owned).

    The entry and its license mapping were checked before installing files.
    Writes <system_legal_dir>/<filename>-accepted.json, attributed to the
    authenticating user. Returns success and a human-readable result. Normal
    provisioning reports record failure after installation as a note; a
    record-only caller must propagate failure. After artifact verification,
    preserve_correct leaves an accurate record's original attribution and
    timestamp untouched.
    """
    license_ref = entry["license_ref"]
    filename = entry["filename"]
    record = {
        "model": entry.get("name") or filename,
        "filename": filename,
        "repo_id": entry.get("repo_id", ""),
        "sha256": sha256,
        "license_ref": license_ref,
        "canonical_url": _LICENSE_URLS[license_ref],
        "license_source": f"the shipped descriptor {pins_path} (package-verified)",
        "scope": "system",  # distinguishes from a per-user XDG record.
    }
    target = system_legal_dir / f"{filename}-accepted.json"
    try:
        if preserve_correct:
            try:
                existing = json.loads(target.read_text())
            except (FileNotFoundError, json.JSONDecodeError, UnicodeError):
                existing = None
            if (isinstance(existing, dict)
                    and all(existing.get(key) == value for key, value in record.items())
                    and all(isinstance(existing.get(key), str) and existing[key]
                            for key in ("accepted_at", "accepted_by"))):
                return True, (f"provision: re-verified {filename}; license record "
                              f"already correct at {target}.")
        record.update(
            accepted_at=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            accepted_by=accepted_by or os.environ.get("PKEXEC_USER", "unknown"),
        )
        system_legal_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(system_legal_dir, 0o755)
        tmp = system_legal_dir / f".{filename}-accepted.json.incoming"
        tmp.write_text(json.dumps(record, indent=2) + "\n")
        os.chmod(tmp, 0o644)
        if os.geteuid() == 0:
            os.chown(tmp, 0, 0)
        os.replace(tmp, target)
    except OSError as exc:
        if preserve_correct:
            return False, (f"provision: license record update refused for "
                           f"{filename}: {exc}.")
        return False, (f"provision: note — system license-acceptance record write "
                       f"failed ({exc}); model is installed, record it via Forge or "
                       "re-run setup.")
    if preserve_correct:
        return True, (f"provision: re-verified {filename}; license record "
                      f"rewritten at {target}.")
    return True, f"provision: recorded system license acceptance ({license_ref}) at {target}."


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
    caller = os.environ.get("PKEXEC_UID")
    if not caller:
        return _fail(
            "model_setup_dispatch: PKEXEC_UID unset; refusing to run outside the "
            "pkexec runner context."
        )
    if not (caller.isascii() and caller.isdigit()):
        return _fail(
            f"model_setup_dispatch: PKEXEC_UID {caller!r} is not a uid; refusing "
            "to run."
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
        caller_uid=int(caller),
    )
    _emit(message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
