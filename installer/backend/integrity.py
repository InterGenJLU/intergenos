# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Install-time integrity verification for Forge installer.

Implements the install-time half of the install-time integrity verification
feature (design doc: docs/research/security/install-integrity-verification.md).

Inserted as PHASE_VERIFY before PHASE_PARTITION in the orchestrator. The flow:

    1. Verify GPG signature on the embedded archive manifest. Signature
       failure is NON-OVERRIDABLE — manifest itself is compromised, no
       per-package consent makes sense.
    2. Walk archive_dir, sha256 every .igos.tar.gz found.
    3. For each archive whose sha doesn't match the manifest entry (or whose
       filename isn't in the manifest at all), invoke the warning_callback
       (frontend renders the warning text) followed by ack_callback (frontend
       collects the typed-phrase override).
    4. Ask the OPPOSITE question: does every entry the manifest promises have an
       archive that was actually checked? The entries with none are collected
       and raised as ONE warning and ONE typed-phrase decision — see
       "THE SECOND QUESTION" below.
    5. ack_callback returns True only if the user typed the exact phrase for
       that decision: OVERRIDE_HASH_MISMATCH_<package_normalized> for a
       per-archive mismatch, OVERRIDE_MISSING_ARCHIVES_<count> for the short
       media. False = abort.
    6. Every event (verify_started, override granted, abort, verify_completed)
       is appended to a hash-chained JSONL audit log so silent post-install
       tampering of the log is detectable.

THE SECOND QUESTION, and why it is one decision rather than many. Walking the
archives that are PRESENT can only ever find a bad archive; it cannot find an
absent one, because an entry with no file is never visited. One installed
system's own record showed the consequence: 1145 manifest entries, 861 archives
checked, verification reported successful, zero overrides — 284 promised
archives never looked at and nothing in the record to tell a short install from
a complete one. The manifest is a census rather than a wish list (the build
emits it by hashing every archive in the chroot's own archive directory), so an
entry without an archive means something that existed when the media was signed
is not on the media now.

That class is raised as a SINGLE warning naming every missing entry, and a
single typed phrase carrying the COUNT. Per-archive prompting is right for a
mismatch, where the user is judging one package they may have built themselves;
it is useless for a short media, because no one types 284 phrases, and an
override channel a person cannot use is the same as no override channel at all.
The count is in the phrase so it cannot be typed without reading the warning.

Frontend split — the warning text and ack collection are NOT done in this
module. TUI passes input()-style callbacks; GUI passes GLib-marshaled
callbacks against a Gtk.Entry with paste-clipboard suppressed. Same backend
function signature, two different presentation surfaces.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional


# Canonical InterGenOS master release-key fingerprint (40-hex, no spaces).
# MUST match the spaced form shown in INTEGRITY_WARNING_TEMPLATE below and the
# canonical publication (docs/signing-key.md / intergenstudios.com/signing-key).
# This is the IN-CODE PIN used by verify_manifest_signature: the manifest
# signature must chain to THIS primary key, so an attacker who rewrites the
# whole install medium (manifest + sig + their OWN key) cannot verify green.
# It is the independent trust anchor that does not lean on the off-by-default
# Secure Boot chain (install-integrity design §6.3; reviewed 2026-06-17).
RELEASE_MASTER_FPR = "5597A3E0587B253006D0DD7B8C50826182083050"


# Hard-coded warning text. Pulled from design doc §6.3, with master fingerprint
# from docs/signing-key.md hard-coded so an attacker who controls only the
# manifest cannot also control the warning the user sees.
INTEGRITY_WARNING_TEMPLATE = """
═════════════════════════════════════════════════════════════════════════
  ⚠  Integrity mismatch detected
═════════════════════════════════════════════════════════════════════════

  Package:   {package}
  Expected:  {expected_sha256}
  Found:     {actual_sha256}

  This package archive on the install media does not match the
  cryptographically-signed manifest published with this InterGenOS
  release. This means one of:

    • The install media was tampered with after it was signed.
    • The archive was corrupted in transit (USB write error, network).
    • You are intentionally running a custom-built or modified package.

  We strongly recommend that you do not proceed. Instead:

    1. Re-write the install media from a trusted source.
    2. Email security@intergenstudios.com with this warning text and
       a description of where you obtained the install media.
    3. Wait for guidance before retrying.

  Cross-check the release signing key fingerprint independently
  before trusting any artifact you download from us:

      Master:  5597 A3E0 587B 2530 06D0  DD7B 8C50 8261 8208 3050

  This fingerprint is published canonically at
  https://intergenstudios.com/signing-key and at the project's
  GitHub repository docs/signing-key.md. Both copies must match;
  if they do not, you are looking at a compromised source.

  This is your machine, however. If you intentionally created this
  mismatch (for example, testing a patched package), you may proceed
  at your own risk by acknowledging this warning explicitly.

  This override will be recorded in:
      /var/log/igos-integrity-override.log

  To proceed despite this mismatch, type the following exactly
  (case-sensitive):

      {override_phrase}

  To abort the install, type anything else (or press Ctrl+C).

═════════════════════════════════════════════════════════════════════════
"""


#: Placed in the ``actual_sha256`` position of warning_callback when the
#: subject is not a bad archive but an archive that is not there at all. There
#: is no hash to report, and reporting an empty string or a row of zeroes would
#: read as a hash.
ARCHIVE_ABSENT = "<no archive on the install media>"

#: The pseudo package name used for the short-media decision. It is not a
#: package: it carries the COUNT so that one callback signature serves both
#: decisions and both frontends can build the right phrase from the name alone,
#: without either of them string-matching on their own.
MISSING_ARCHIVES_KEY_PREFIX = "missing-archives:"

#: Typed phrase for the short-media decision. The count is part of it
#: deliberately — it cannot be typed without reading how many are missing.
MISSING_ARCHIVES_OVERRIDE_FORMAT = "OVERRIDE_MISSING_ARCHIVES_{count}"

#: How many missing entries the warning text lists before it summarises the
#: rest. The audit log always records every one of them.
MISSING_ARCHIVES_LISTED = 40


MISSING_ARCHIVES_WARNING_TEMPLATE = """
═════════════════════════════════════════════════════════════════════════
  ⚠  Archives promised by the signed manifest are not on the install media
═════════════════════════════════════════════════════════════════════════

  Missing: {count} of the archives the signed manifest lists

{package_list}

  The cryptographically-signed manifest published with this InterGenOS
  release lists these package archives, and they are not present on this
  install media. This means one of:

    • The install media is incomplete — it was written from a partial
      source, or the write did not finish.
    • The media was tampered with and archives were removed.
    • The build that produced this media did not ship everything its
      own manifest recorded.

  Installing anyway gives you a system that is missing software the
  release says it has. Nothing will warn you again: the missing pieces
  are found later, by whoever needed them.

  We strongly recommend that you do not proceed. Instead:

    1. Re-write the install media from a trusted, complete source.
    2. Email security@intergenstudios.com with this text and a
       description of where you obtained the install media.
    3. Wait for guidance before retrying.

  This is your machine, however. If you know this media is a partial
  build and you want it anyway, you may proceed at your own risk.

  The full list of missing archives is recorded in:
      /var/log/igos-integrity-override.log

  To proceed despite the missing archives, type the following exactly
  (case-sensitive):

      {override_phrase}

  To abort the install, type anything else (or press Ctrl+C).

═════════════════════════════════════════════════════════════════════════
"""


# Per-mismatch typed-phrase format. The package_normalized component is
# the package name with non-alphanumeric chars replaced by underscores
# (e.g. "gtk+3" → "gtk_3"), which avoids shell-interpretation issues during
# type-in and forces the user to read which package they're acknowledging.
OVERRIDE_PHRASE_FORMAT = "OVERRIDE_HASH_MISMATCH_{package_normalized}"


# BSD-style sha256sum line:  SHA256 (path) = hash
_BSD_SHA256_LINE = re.compile(r"^SHA256 \((?P<path>[^)]+)\) = (?P<sha>[0-9a-fA-F]{64})\s*$")


@dataclass
class VerifyResult:
    """Outcome of verify_archives().

    success:           True iff every archive matched OR every mismatch
                       was successfully overridden.
    overrides_granted: count of mismatches the user accepted via typed phrase.
    aborted_at:        package name where user declined to override; None on
                       successful run (with or without overrides).
    error:             non-None if signature verification or manifest parse
                       failed; in that case overrides_granted == 0 and
                       aborted_at is None (we never got far enough to ask).
    manifest_entry_count: how many archives the signed manifest promises.
    archives_checked:  how many archives were actually found and hashed.
                       These two are reported together, always, because either
                       one alone reads as a complete check.
    missing_archives:  manifest entries with no archive on the media, sorted.
                       Empty on a complete media. A frontend that shows only
                       success/failure cannot tell a complete install from a
                       short one; this is the field that lets it.
    """
    success: bool
    overrides_granted: int = 0
    aborted_at: Optional[str] = None
    error: Optional[str] = None
    manifest_entry_count: int = 0
    archives_checked: int = 0
    missing_archives: list = field(default_factory=list)


def normalize_package_name(name: str) -> str:
    """Map package name to override-phrase suffix form.

    Replace any non-alphanumeric character with underscore. Examples:
        "gtk+3"          → "gtk_3"
        "core/glibc"     → "core_glibc"
        "gnome-shell"    → "gnome_shell"
    """
    return re.sub(r"[^A-Za-z0-9]+", "_", name)


def missing_archives_key(count: int) -> str:
    """The pseudo package name for the short-media decision.

    Passed to warning_callback and ack_callback in the package position so both
    frontends keep one signature and one way to build the phrase.
    """
    return f"{MISSING_ARCHIVES_KEY_PREFIX}{count}"


def missing_archives_count(package_name: str) -> Optional[int]:
    """How many archives are missing, if *package_name* is the short-media key.

    ``None`` for an ordinary package name. Returns ``None`` rather than raising
    on a malformed count so a frontend rendering an unexpected key degrades to
    the per-package path instead of crashing mid-install.
    """
    if not package_name.startswith(MISSING_ARCHIVES_KEY_PREFIX):
        return None
    try:
        return int(package_name[len(MISSING_ARCHIVES_KEY_PREFIX):])
    except ValueError:
        return None


def expected_override_phrase(package_name: str) -> str:
    """The exact typed phrase the user must enter to override a given decision.

    Two decisions, two phrases: a per-archive mismatch names the package, and
    the short-media decision names how many archives are missing.
    """
    count = missing_archives_count(package_name)
    if count is not None:
        return MISSING_ARCHIVES_OVERRIDE_FORMAT.format(count=count)
    return OVERRIDE_PHRASE_FORMAT.format(
        package_normalized=normalize_package_name(package_name)
    )


def render_integrity_warning(package_name: str, expected_sha256: str,
                             actual_sha256: str) -> str:
    """The warning text for one integrity decision, ready to display.

    Both frontends call THIS rather than formatting a template themselves. When
    they each did their own formatting, a second kind of warning could reach one
    surface and not the other; there is now one place that decides which text a
    given decision gets, and adding a third kind cannot miss a surface.
    """
    count = missing_archives_count(package_name)
    if count is not None:
        listed = [line for line in expected_sha256.splitlines() if line.strip()]
        shown = listed[:MISSING_ARCHIVES_LISTED]
        body = "\n".join(f"    {rel}" for rel in shown)
        if len(listed) > len(shown):
            body += (f"\n    ... and {len(listed) - len(shown)} more, all of "
                     f"them listed in the log named below")
        return MISSING_ARCHIVES_WARNING_TEMPLATE.format(
            count=count,
            package_list=body,
            override_phrase=expected_override_phrase(package_name),
        )
    return INTEGRITY_WARNING_TEMPLATE.format(
        package=package_name,
        expected_sha256=expected_sha256,
        actual_sha256=actual_sha256,
        override_phrase=expected_override_phrase(package_name),
    )


def sha256_file(path: Path, _chunk_size: int = 64 * 1024) -> str:
    """Compute hex-encoded SHA-256 of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_chunk_size):
            h.update(chunk)
    return h.hexdigest()


def _validsig_primary_fpr(status_text: str) -> Optional[str]:
    """Return the primary-key fingerprint from a gpgv ``--status-fd`` VALIDSIG line.

    gpgv emits, per good signature::

        [GNUPG:] VALIDSIG <signing-key-fpr> <date> <ts> <exp> <ver> <res> \\
                 <pkalgo> <hashalgo> <class> <primary-key-fpr>

    The LAST field is the PRIMARY (master) key fingerprint — identical whether
    the signature was made by the master itself or by one of its signing
    subkeys. Pinning it therefore accepts legitimate subkey rotation while
    rejecting a foreign key. Returns the upper-cased fpr, or None if there is
    no VALIDSIG line (i.e. no good signature).
    """
    for line in status_text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "[GNUPG:]" and parts[1] == "VALIDSIG":
            return parts[-1].upper()
    return None


def verify_manifest_signature(
    manifest_path: Path,
    public_key_path: Path,
    expected_primary_fpr: str = RELEASE_MASTER_FPR,
) -> bool:
    """Verify the DETACHED signature on the manifest, requiring it to chain to
    the PINNED canonical master key — not merely that *some* supplied key signed it.

    Signing model (both signers): the manifest is left PLAINTEXT and signed by
    the release signing subkey ([S1], under master ``RELEASE_MASTER_FPR``) with
    ``gpg --detach-sign --armor`` into a separate ``<manifest>.sig``
    (scripts/sign-manifest.sh, scripts/sign-release.sh). The release public key
    is staged ASCII-ARMORED (sign-release.sh ``gpg --armor --export``).

    Hardening (install-integrity security review, 2026-06-17):
      1. PIN THE KEY. A clean exit code alone is insufficient — it accepts
         whatever key ships at ``public_key_path``, so a full-media-rewrite
         attacker who ships their own {manifest, sig, key} verifies green. We
         require the gpgv VALIDSIG primary-key fingerprint to equal
         ``expected_primary_fpr`` (default: the in-code ``RELEASE_MASTER_FPR``).
         This is the independent anchor that does NOT rely on the off-by-default
         Secure Boot chain.
      2. gpgv, NOT ``gpg --keyring``. gpg 2.5.x + use-keyboxd silently ignores
         ``--keyring`` on --verify (→ NO_PUBKEY / undefined behavior) — the
         project's documented hazard. The armored release key is converted to a
         binary keyring with the pure-transform ``gpg --dearmor`` (which keyboxd
         cannot divert), and the detached sig is checked with the
         project-blessed ``gpgv``.

    Returns True iff gpgv reports a good signature whose PRIMARY key is the
    pinned fingerprint. False on a missing ``.sig`` (fail-closed — absent
    signature is not a skip), dearmor failure, a bad/absent signature, a good
    signature by a NON-pinned key, a missing gpgv binary, or any error.
    """
    sig_path = Path(str(manifest_path) + ".sig")
    if not sig_path.exists():
        # Fail-closed: a manifest with no detached signature cannot be
        # trusted. (verify_archives treats a False here as a hard,
        # non-overridable signature failure.)
        return False
    pinned = expected_primary_fpr.upper()
    try:
        with tempfile.TemporaryDirectory(prefix="igos-verify-") as tmp:
            os.chmod(tmp, 0o700)
            keyring = str(Path(tmp) / "release.gpg")

            # Build a binary keyring from the armored release key via the pure
            # `--dearmor` transform. This touches NO keyring/keyboxd machinery,
            # so gpg 2.5.x + use-keyboxd cannot silently divert it.
            dearmor = subprocess.run(
                ["gpg", "--batch", "--yes", "--dearmor",
                 "-o", keyring, str(public_key_path)],
                capture_output=True, text=True, timeout=30,
            )
            if dearmor.returncode != 0 or not Path(keyring).exists():
                return False

            # Verify the detached signature with gpgv (keyboxd-immune) and
            # capture the machine-readable status for the fingerprint pin.
            result = subprocess.run(
                ["gpgv", "--keyring", keyring, "--status-fd=1",
                 str(sig_path), str(manifest_path)],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                return False
            # Require a good signature AND that it chains to the pinned key.
            return _validsig_primary_fpr(result.stdout) == pinned
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def parse_manifest(manifest_path: Path) -> dict[str, str]:
    """Parse a BSD-style sha256sum manifest into {path: sha256}.

    Ignores comment lines (starting with #) and blank lines. Stops at
    the first PGP-signature header (cleartext signing leaves the body
    in plain view followed by `-----BEGIN PGP SIGNATURE-----`).

    Raises ValueError on malformed lines (anything that's not blank,
    a comment, a recognized SHA256 line, or a PGP block header).
    """
    entries: dict[str, str] = {}
    in_signature_block = False

    with open(manifest_path, "r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.rstrip("\n").rstrip("\r")

            if line.startswith("-----BEGIN PGP SIGNATURE-----"):
                in_signature_block = True
                continue
            if in_signature_block:
                continue

            # gpg --clear-sign prefix
            if line.startswith("-----BEGIN PGP SIGNED MESSAGE-----"):
                continue
            if line.startswith("Hash:"):
                continue
            if not line or line.startswith("#"):
                continue

            m = _BSD_SHA256_LINE.match(line)
            if not m:
                raise ValueError(
                    f"manifest {manifest_path}:{lineno}: malformed line: {line!r}"
                )
            entries[m.group("path")] = m.group("sha").lower()

    return entries


def _now_iso8601() -> str:
    """UTC ISO-8601 timestamp with second precision; suitable for audit log entries."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash_chain_entry(entry_without_chain: dict, prev_hash: str) -> str:
    """Compute the entry_sha256 for an audit-log entry given prior chain hash.

    Hash includes prev_hash so silent deletion of any entry breaks the chain
    at the next entry. Hash is computed over canonical JSON of the entry's
    business fields plus prev_hash.
    """
    body = json.dumps(entry_without_chain, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((body + "|" + prev_hash).encode("utf-8")).hexdigest()


def _last_chain_hash(log_path: Path) -> str:
    """Read the most recent entry_sha256 from the audit log, or 'GENESIS'.

    Returns 'GENESIS' if the log doesn't exist or is empty. Otherwise
    parses the last non-empty line and returns its entry_sha256 field.
    Malformed last line raises ValueError — we never want to silently
    re-genesis a log that already exists; that would let an attacker
    wipe history.
    """
    if not log_path.exists() or log_path.stat().st_size == 0:
        return "GENESIS"
    last_line = ""
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                last_line = line
    if not last_line:
        return "GENESIS"
    obj = json.loads(last_line)  # raises on malformed; that's the right behavior
    return obj["entry_sha256"]


def _audit_log_append(log_path: Path, event_fields: dict) -> None:
    """Append one event to the hash-chained JSONL audit log.

    event_fields should NOT include 'prev', 'ts', 'v', or 'entry_sha256';
    those are added here. event_fields SHOULD include 'event' and any
    event-specific keys (package, expected, actual, etc.).
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    prev = _last_chain_hash(log_path)
    entry: dict = {
        "v": 1,
        "prev": prev,
        "ts": _now_iso8601(),
        **event_fields,
    }
    entry["entry_sha256"] = _hash_chain_entry(entry, prev)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")


def verify_archives(
    archive_dir: Path,
    manifest_path: Path,
    public_key_path: Path,
    warning_callback: Callable[[str, str, str], None],
    ack_callback: Callable[[str], bool],
    audit_log_path: Path,
) -> VerifyResult:
    """Verify every .igos.tar.gz archive against the signed manifest.

    Args:
        archive_dir:       directory containing .igos.tar.gz files to check.
        manifest_path:     path to the signed BSD-style sha256 manifest
                           (intergenos-archive-manifest.txt).
        public_key_path:   path to release-key public component (a single
                           keyring file containing only the master + S1
                           release keys).
        warning_callback:  fn(package_name, expected_sha, actual_sha) — frontend
                           is responsible for rendering the warning text built
                           from INTEGRITY_WARNING_TEMPLATE.
        ack_callback:      fn(package_name) → bool. Frontend prompts the user
                           to type the override phrase and returns True iff
                           the user typed expected_override_phrase(package_name)
                           exactly.
        audit_log_path:    path to the JSONL audit log. Created (with parents)
                           if missing. Hash-chained — silent deletion is
                           detectable.

    Returns:
        VerifyResult dataclass.

    On manifest signature failure: returns immediately with success=False +
    error set; no callbacks invoked, no override possible.

    On manifest parse failure: same — manifest is structurally broken; treat
    as if signature failed.

    On per-archive mismatch: invoke warning_callback then ack_callback. If
    ack_callback returns False, write abort entry + return aborted_at set.
    If True, write override entry + continue.

    All side effects (audit log writes) happen inside this function.
    """
    # 1. Verify signature.
    if not verify_manifest_signature(manifest_path, public_key_path):
        # Note: we do NOT write to the audit log here. The audit log is a
        # record of what the USER chose to do. Signature failures are a
        # backend-detected condition where no user choice was offered.
        return VerifyResult(
            success=False,
            error="manifest signature verification failed",
        )

    # 2. Parse manifest.
    try:
        manifest = parse_manifest(manifest_path)
    except (ValueError, OSError) as e:
        return VerifyResult(
            success=False,
            error=f"manifest parse failed: {e}",
        )

    # 3. Open audit log session.
    _audit_log_append(
        audit_log_path,
        {
            "event": "verify_started",
            "manifest_path": str(manifest_path),
            "archive_dir": str(archive_dir),
            "manifest_entry_count": len(manifest),
        },
    )

    # 4. Walk archive_dir, sha + cross-reference each file.
    overrides = 0
    archives = sorted(p for p in archive_dir.rglob("*.igos.tar.gz") if p.is_file())
    checked: set[str] = set()

    def _counts(**extra):
        """The two numbers that must always travel together, plus the caller's."""
        base = {
            "manifest_entry_count": len(manifest),
            "archives_checked": len(checked),
        }
        base.update(extra)
        return base

    for archive in archives:
        # Manifest paths are relative to archive_dir (e.g. "toolchain/glibc-2.40-1.igos.tar.gz").
        # .as_posix() forces forward-slash separators to match BSD manifest format on every host;
        # without it, Windows os-native "\" leaks through to ack/warning callbacks + audit log.
        rel = archive.relative_to(archive_dir).as_posix()
        actual = sha256_file(archive)
        expected = manifest.get(rel)
        checked.add(rel)

        if expected is not None and expected == actual:
            continue  # silent pass

        # mismatch (either missing-from-manifest or hash-different)
        expected_str = expected if expected is not None else "<not in manifest>"

        # Render warning to frontend, then collect typed-phrase ack.
        warning_callback(rel, expected_str, actual)
        granted = ack_callback(rel)

        if granted:
            overrides += 1
            _audit_log_append(
                audit_log_path,
                {
                    "event": "override",
                    "package": rel,
                    "expected": expected_str,
                    "actual": actual,
                },
            )
            continue

        # User declined to override — abort.
        _audit_log_append(
            audit_log_path,
            _counts(
                event="abort",
                package=rel,
                expected=expected_str,
                actual=actual,
                overrides_granted_before_abort=overrides,
            ),
        )
        return VerifyResult(
            success=False,
            overrides_granted=overrides,
            aborted_at=rel,
            manifest_entry_count=len(manifest),
            archives_checked=len(checked),
            missing_archives=sorted(set(manifest) - checked),
        )

    # 5. The second question: does every entry the manifest promises have an
    #    archive that was actually checked? Walking the files that are PRESENT
    #    cannot answer it — an entry with no file is never visited — so it is
    #    asked here, once, against the whole manifest.
    missing = sorted(set(manifest) - checked)

    if missing:
        key = missing_archives_key(len(missing))
        # The package position carries the count; the expected position carries
        # the entries themselves, one per line, which is what the frontend
        # renders and what makes the decision informed rather than a number.
        warning_callback(key, "\n".join(missing), ARCHIVE_ABSENT)
        if not ack_callback(key):
            _audit_log_append(
                audit_log_path,
                _counts(
                    event="abort",
                    package=key,
                    reason="archives promised by the manifest are not on the "
                           "install media",
                    missing_archives=missing,
                    manifest_entries_without_archive=len(missing),
                    overrides_granted_before_abort=overrides,
                ),
            )
            return VerifyResult(
                success=False,
                overrides_granted=overrides,
                aborted_at=key,
                manifest_entry_count=len(manifest),
                archives_checked=len(checked),
                missing_archives=missing,
            )
        overrides += 1
        _audit_log_append(
            audit_log_path,
            _counts(
                event="override",
                package=key,
                reason="archives promised by the manifest are not on the "
                       "install media",
                missing_archives=missing,
                manifest_entries_without_archive=len(missing),
            ),
        )

    # 6. Everything the manifest promised is accounted for, either by an archive
    #    that was checked or by an override the user typed out in full.
    _audit_log_append(
        audit_log_path,
        _counts(
            event="verify_completed",
            overrides_granted=overrides,
            manifest_entries_without_archive=len(missing),
            expected_entries_missing=missing,
        ),
    )

    return VerifyResult(
        success=True,
        overrides_granted=overrides,
        manifest_entry_count=len(manifest),
        archives_checked=len(checked),
        missing_archives=missing,
    )


def copy_audit_log_to_target(audit_log_path: Path, target: Path) -> None:
    """Copy the audit log onto the installed target system.

    Called from PHASE_CLEANUP. The audit log lives in the install
    environment at audit_log_path; we want a record on the user's
    installed system so post-incident forensics has the trail.

    Idempotent — overwrites if target already has a stale copy from
    a previous install attempt; user re-asserts intent via re-ack
    each install per design doc §6.4.
    """
    if not audit_log_path.exists():
        return
    target_log = target / "var" / "log" / "igos-integrity-override.log"
    target_log.parent.mkdir(parents=True, exist_ok=True)
    target_log.write_bytes(audit_log_path.read_bytes())


def copy_signed_manifest_to_target(
    manifest_path: Path,
    public_key_path: Path,
    target: Path,
) -> list[str]:
    """Copy signed manifest + detached signature + release key to target.

    Called from PHASE_CLEANUP. The live install media ships the trust
    artifacts at /install/intergenos-archive-manifest.txt(+.sig) +
    /install/intergenos-release-key.asc. Preserving them at
    /var/lib/igos/manifest/ on the installed system lets the post-install
    smoke check (installer/smoke/checks/signing.sh
    check_signing_manifest_signature) revalidate the chain at any time
    after install — independent of whether the live media is still
    around.

    Signature path is `<manifest_path>.sig` (matches scripts/sign-
    release.sh naming convention).

    Idempotent — overwrites if target already has stale copies (rerun
    install over previous attempt is supported by design).

    Returns the list of MISSING source filenames (empty list = a complete
    trust set was copied). The caller surfaces a loud warning + an install-
    trace entry when the returned list is non-empty, so a target that cannot
    self-revalidate post-install is VISIBLE rather than silently skipped
    (install-integrity §4C — convert the silent skip into a loud signal).
    """
    sig_path = Path(str(manifest_path) + ".sig")
    target_dir = target / "var" / "lib" / "igos" / "manifest"
    target_dir.mkdir(parents=True, exist_ok=True)
    missing = []
    for src in (manifest_path, sig_path, public_key_path):
        if src.exists():
            shutil.copy2(src, target_dir / src.name)
        else:
            missing.append(src.name)
    return missing


# Install-media trust-set location (the verity-sealed triplet lives under
# /install/ on real release media; an UNSIGNED_TEST / dev ISO instead carries
# this explicit marker — written by build-squashfs Step 4.8). Mirrored as
# frontend constants in tui.py / gui progress.py / backend_service.py; the
# trust DECISION below is the single source of truth those three share so
# the policy cannot drift between them.
INSTALL_MEDIA_DEV_MARKER = Path("/install/IGOS_DEV_ALLOW_UNVERIFIED")


class ReleaseMediaIntegrityError(Exception):
    """Install media lacks a verifiable archive-integrity trust set AND is not
    an explicitly-marked dev/unsigned-test artifact — i.e. real release media
    whose integrity cannot be established.

    Callers MUST treat this as a HARD, pre-disk-write abort (install-integrity
    §4C). Returning None on mere file-absence — the prior behavior — is the
    dormancy bug this closes: it silently skipped verification on stripped
    release media (red-team R1).
    """


def install_media_trust_decision(
    manifest_path: Path,
    public_key_path: Path,
    dev_marker_path: Path = INSTALL_MEDIA_DEV_MARKER,
) -> str:
    """Decide how to treat the install media's archive-integrity trust set.

    Single source of truth shared by the three frontend verify-config
    builders so the fail-closed policy is identical across TUI, GUI, and the
    D-Bus backend.

    Returns:
        "verify"   — the signed manifest + release key are present; the caller
                     MUST build a VerifyConfig and run PHASE_VERIFY. (A bad
                     signature is then caught fail-closed by verify_archives /
                     verify_manifest_signature — not this function's job.)
        "skip-dev" — the trust set is absent BUT the explicit
                     IGOS_DEV_ALLOW_UNVERIFIED dev marker is present; the
                     caller skips verification EXPLICITLY (the sanctioned,
                     loud dev/unsigned-test seam).

    Raises:
        ReleaseMediaIntegrityError — the trust set is absent and no dev marker
        is present. Real release media missing its integrity set: refuse to
        install (fail-closed). NEVER skip on mere file-absence (red-team R1).
    """
    if manifest_path.exists() and public_key_path.exists():
        return "verify"
    if dev_marker_path.exists():
        return "skip-dev"
    raise ReleaseMediaIntegrityError(
        "archive-integrity trust set is absent on this install media and no "
        "IGOS_DEV_ALLOW_UNVERIFIED dev marker is present — refusing to install "
        "from media whose archive integrity cannot be verified. A release ISO "
        "must carry /install/intergenos-archive-manifest.txt (+ .sig) and "
        "intergenos-release-key.asc; a dev/unsigned-test ISO must carry the "
        "explicit IGOS_DEV_ALLOW_UNVERIFIED marker. Aborting before any disk "
        "write."
    )
