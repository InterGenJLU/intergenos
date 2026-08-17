# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Destructive-policy never-list — path-matcher core.

The operator-signed manifest (intergen/data/destructive-policy-manifest.json,
ratified 2026-05-30) enumerates the paths InterGen's AI may NEVER perform a
destructive operation on — no config option, ever (anti-self-tamper + system
survival + credential/boot integrity). Everything NOT on this list and not
dm-verity read-only is fair game under per-capability opt-in + per-action human
consent. The manifest's `system_ai` category also covers ~/.config/intergen/
and /var/lib/intergen/, so enforcing it delivers the Sentinel decision #5
AI-IMMUTABLE config protection (sentinel/escalation/providers) in the same pass.

This module is the PURE matcher: given an already-loaded manifest dict, decide
whether a candidate path is protected. It holds no I/O and no signature logic —
the signature-verifying loader (verify the detached .asc against the operator
key before trusting the JSON) and the dispatch-chokepoint enforcement that
consults this matcher are separate, composing pieces.

Match semantics (from the manifest's `match_rules`):
  * expand_user      — a leading ~ in a candidate OR a manifest pattern expands.
  * resolve_symlinks — the candidate is resolved (symlinks + .. collapsed) BEFORE
                       matching, so a symlink/.. detour cannot smuggle a write
                       past a prefix entry. This is the security-critical step.
  * prefix match     — candidate == prefix.rstrip('/')  OR  candidate.startswith(
                       prefix). Manifest prefixes keep their trailing slash so
                       "/boot/" matches "/boot" and "/boot/grub" but not "/booty".
  * exact / glob     — string equality / fnmatch against the resolved candidate.
  * default_on_ambiguity = block — a candidate that cannot be normalized
                       (resolve raised) is treated as PROTECTED (fail closed),
                       never waved through (security-only-alignment rule #10).
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# The operator's OpenPGP master key — the never-list manifest is trusted ONLY
# when its detached signature verifies to this primary-key fingerprint (the
# VALIDSIG line's last field; signing is done by a subkey). Same key
# scripts/sign-with-gpg.sh signs with + scripts/check-manifest-signature.sh
# cross-checks. A manifest that does not verify to this key is NOT trusted.
OPERATOR_FINGERPRINT = "5597A3E0587B253006D0DD7B8C50826182083050"

# Installed locations (dm-verity read-only /usr/share per the manifest's own
# dm_verity_readonly_reference) — the JSON + its detached .asc ship together.
DEFAULT_MANIFEST_PATH = "/usr/share/intergen/destructive-policy-manifest.json"
DEFAULT_SIGNATURE_PATH = "/usr/share/intergen/destructive-policy-manifest.json.asc"

# The shipped operator master keyring — a binary keyring carrying the master
# pubkey, installed on EVERY system by the intergenos-keyring package (it
# dearmors docs/signing-key.asc into this file so pkm can verify signed index
# downloads). We verify the never-list manifest against THIS keyring; the
# installed system has no per-user gpg keyring with the master key, which is
# why `gpg --verify` against the default keyring failed NO_PUBKEY on every
# install and the never-list silently degraded to the interim floor (PI-D).
DEFAULT_KEYRING_PATH = "/etc/pkm/trusted.gpg"

# Injectable gpg verifier: (sig_path, data_bytes) -> (returncode, status_text)
# where status_text is gpg's --status-fd machine output. It verifies the detached
# sig over the EXACT bytes handed in (read once), not by re-reading the file —
# closing a verify-then-parse TOCTOU. Injected so the loader logic unit-tests
# without a real gpg/keyring.
GpgVerify = Callable[[str, bytes], "tuple[int, str]"]


class PolicyLoad(Enum):
    """WHY a never-list load produced (or failed to produce) a trusted manifest.

    The loader has always returned None for every untrusted outcome, which left
    the enforcement chokepoint unable to tell a benign *absent* manifest apart
    from a *tampered* one. PI-D hardening adds this distinction so the chokepoint
    can fail LOUD on tamper while staying quiet on a legitimate absence.

      LOADED    — verified, trusted manifest in hand.
      ABSENT    — no manifest artifact present at all (the documented
                  defense-in-depth floor fallback; benign on a from-source/dev
                  box that never installed the manifest). Quiet.
      UNTRUSTED — the manifest artifact IS present but trust could NOT be
                  established: a bad/failed signature, a valid signature by the
                  wrong key, a present-but-unreadable file, corrupt JSON, or gpgv
                  could not run / the keyring is missing. This is an INTEGRITY
                  failure — tamper or corruption — and the caller must surface it
                  LOUDLY (alert-level + user-visible), never degrade to the floor
                  silently. A tamper-induced downgrade of the authoritative
                  never-list must not hide behind a healthy-looking self-test.
    """

    LOADED = "loaded"
    ABSENT = "absent"
    UNTRUSTED = "untrusted"


@dataclass(frozen=True)
class ProtectedMatch:
    """Why a candidate path is protected — surfaced in the refusal + audit."""

    category: str       # the manifest category that matched (e.g. "identity_auth_privilege")
    rule: str           # "exact" | "prefix" | "glob" | "ambiguity-default"
    pattern: str        # the manifest entry (or the raw input on ambiguity)
    candidate: str      # the normalized candidate that matched


class DestructivePolicy:
    """Pure never-list path matcher over an already-loaded manifest dict."""

    def __init__(self, manifest: dict) -> None:
        rules = manifest.get("match_rules", {})
        self.manifest_version = manifest.get("manifest_version")
        self._expand_user: bool = bool(rules.get("expand_user", True))
        self._resolve_symlinks: bool = bool(rules.get("resolve_symlinks", True))
        self._default_block: bool = rules.get("default_on_ambiguity", "block") == "block"

        # Flatten the categories into matcher tables, keeping the category label
        # so a refusal can name WHY. Patterns are expanduser-ed here (string-based,
        # preserving any trailing slash the prefix semantics rely on); the
        # candidate is expanduser-ed + resolved at match time.
        self._exact: dict[str, str] = {}
        self._prefix: list[tuple[str, str]] = []
        self._glob: list[tuple[str, str]] = []
        for category, spec in manifest.get("categories", {}).items():
            for entry in spec.get("exact", []) or []:
                self._exact[self._expand_pattern(entry)] = category
            for entry in spec.get("prefix", []) or []:
                self._prefix.append((self._expand_pattern(entry), category))
            for entry in spec.get("glob", []) or []:
                self._glob.append((self._expand_pattern(entry), category))

    @classmethod
    def from_manifest(cls, manifest: dict) -> "DestructivePolicy":
        return cls(manifest)

    def _expand_pattern(self, pattern: str) -> str:
        # Canonicalize a stored pattern the SAME way a candidate is normalized
        # (expanduser + resolve), so the LOCATION side cannot diverge from the
        # candidate side. Without this, a pre-existing symlink in a protected
        # location's PARENT — a Stow/chezmoi-symlinked ~/.config is the common
        # case — is followed when resolving the candidate but NOT the pattern, so
        # the resolved candidate no longer prefix-matches the unresolved pattern
        # and is_protected() wrongly returns None: a bypass of the anti-self-tamper
        # guard whose whole purpose is to keep the AI off its own guardrail config.
        # Resolving BOTH sides closes it (candidate-side resolve already defeats an
        # attacker symlink INTO a protected dir; this defeats the location-side
        # symlinked-parent divergence).
        #
        # The trailing slash that prefix semantics rely on is string-based and
        # resolve()/normpath drop it, so capture it and re-append. Any resolve
        # failure falls back to the expanduser-only form — never weaker than the
        # prior behavior, only potentially stronger.
        if not self._expand_user and not self._resolve_symlinks:
            return pattern
        had_trailing = pattern.endswith("/") and pattern != "/"
        try:
            p = Path(pattern)
            if self._expand_user:
                p = p.expanduser()
            if self._resolve_symlinks:
                p = p.resolve()
            else:
                p = Path(os.path.normpath(str(p)))
            canon = str(p)
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning("destructive-policy: cannot canonicalize pattern %r "
                           "(%s); using expanduser-only form", pattern, exc)
            return os.path.expanduser(pattern) if self._expand_user else pattern
        if had_trailing and not canon.endswith("/"):
            canon += "/"
        return canon

    def _normalize_candidate(self, path: str) -> str | None:
        """Resolve a candidate to its canonical absolute form, or None if it
        cannot be normalized (caller treats None as fail-closed)."""
        try:
            p = Path(path)
            if self._expand_user:
                p = p.expanduser()
            if self._resolve_symlinks:
                # strict=False: normalize + resolve symlinks even for a path that
                # does not exist yet (a write target may be a new file).
                p = p.resolve()
            else:
                p = Path(os.path.normpath(str(p)))
            return str(p)
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning("destructive-policy: cannot normalize %r (%s); "
                           "treating as protected (fail closed)", path, exc)
            return None

    def is_protected(self, path: str) -> ProtectedMatch | None:
        """Return a ProtectedMatch if `path` is on the never-list, else None."""
        norm = self._normalize_candidate(path)
        if norm is None:
            if self._default_block:
                return ProtectedMatch("(unresolvable)", "ambiguity-default", path, path)
            return None

        category = self._exact.get(norm)
        if category is not None:
            return ProtectedMatch(category, "exact", norm, norm)

        for prefix, category in self._prefix:
            if norm == prefix.rstrip("/") or norm.startswith(prefix):
                return ProtectedMatch(category, "prefix", prefix, norm)

        for pattern, category in self._glob:
            if fnmatch.fnmatch(norm, pattern):
                return ProtectedMatch(category, "glob", pattern, norm)

        return None


# --- signature-verifying loader ------------------------------------------------
# The matcher above trusts an already-loaded dict. These functions are how that
# dict is obtained SAFELY: the JSON is parsed only after its detached OpenPGP
# signature verifies to the operator's primary key. An unsigned, tampered, or
# wrong-key manifest yields None — the caller (the dispatch-chokepoint
# enforcement) decides the fail-closed posture, but it never gets an untrusted
# never-list to act on.

def _default_gpg_verify(sig_path: str, data: bytes,
                        keyring_path: str = DEFAULT_KEYRING_PATH) -> tuple[int, str]:
    """Verify the detached sig over the EXACT bytes `data` (stdin) against the
    shipped operator master keyring.

    Uses ``gpgv --keyring``, NOT ``gpg --verify``: gpg 2.5.x + use-keyboxd
    silently ignores ``--keyring`` on --verify and falls back to the default
    keyring, which on an installed system has no master key — so the manifest
    failed NO_PUBKEY on every install and the never-list degraded to the interim
    floor (PI-D). gpgv honors ``--keyring`` and is keyboxd-immune (the same
    project-blessed path as installer/backend/integrity.py). The pinned master
    fingerprint is still enforced by the caller against the VALIDSIG primary key,
    so the shipped keyring is only the verification VEHICLE — trust derives from
    the in-code OPERATOR_FINGERPRINT, not from whatever key happens to ship.

    Read-once: gpgv reads the signed data from ``-`` (stdin), so the same in-hand
    bytes are verified and then parsed — no verify-then-parse TOCTOU. The
    [GNUPG:] machine lines go to fd 1 (status-fd).
    """
    proc = subprocess.run(
        ["gpgv", "--keyring", keyring_path, "--status-fd=1", sig_path, "-"],
        input=data, capture_output=True, check=False,
    )
    # input is bytes -> stdout is bytes; decode the status lines for parsing.
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def _validsig_primary_fpr(status_text: str) -> str | None:
    """Extract the VALIDSIG primary-key fingerprint (its LAST field), or None.

    gpg emits: `[GNUPG:] VALIDSIG <subkey-fpr> <date> <ts> ... <primary-key-fpr>`
    The signing subkey is field 3; the primary key (what we pin) is the last
    field (matches scripts/sign-with-gpg.sh's awk '{print $NF}').
    """
    for line in status_text.splitlines():
        if line.startswith("[GNUPG:] VALIDSIG"):
            parts = line.split()
            if len(parts) >= 3:
                return parts[-1].upper()
    return None


def _load_verified_manifest_status(
    manifest_path: str,
    sig_path: str,
    *,
    fingerprint: str,
    gpg_verify: GpgVerify | None,
) -> "tuple[dict | None, PolicyLoad]":
    """Core of load_verified_manifest, ALSO reporting WHY the load failed.

    Returns (manifest_dict, LOADED) on success, (None, ABSENT) when no artifact
    is present (a benign floor fallback), or (None, UNTRUSTED) for every
    integrity failure — a present-but-unreadable file, a non-zero gpg exit, a
    VALIDSIG whose primary key is not the pin, unparseable/non-object JSON, or
    gpgv that could not run. ABSENT vs UNTRUSTED is the PI-D distinction: only a
    PRESENT-but-unverifiable manifest is tamper, and only tamper fails loud.

    READ-ONCE: the bytes are read a single time, the detached signature is
    verified over THOSE EXACT bytes, and the SAME bytes are parsed — no re-open
    between check and parse (no verify-then-parse TOCTOU).
    """
    verify = gpg_verify or _default_gpg_verify
    want = fingerprint.replace(" ", "").upper()
    try:
        with open(manifest_path, "rb") as fh:
            raw = fh.read()          # read ONCE — these exact bytes are verified + parsed
    except FileNotFoundError:
        # No artifact at all — the documented defense-in-depth floor fallback,
        # not an integrity failure. Benign on a from-source/dev box.
        logger.warning("destructive-policy: manifest absent (%s); never-list not "
                       "loaded — interim floor only", manifest_path)
        return None, PolicyLoad.ABSENT
    except OSError as exc:
        # The artifact is THERE but unreadable (permission / I/O) — suspicious,
        # treat as an integrity failure rather than a benign absence.
        logger.error("destructive-policy: manifest present but unreadable (%s); "
                     "NOT trusted", type(exc).__name__)
        return None, PolicyLoad.UNTRUSTED
    try:
        returncode, status = verify(sig_path, raw)
    except (OSError, ValueError) as exc:
        logger.error("destructive-policy: gpg verify failed to run (%s); "
                     "manifest NOT trusted", type(exc).__name__)
        return None, PolicyLoad.UNTRUSTED
    if returncode != 0:
        logger.error("destructive-policy: signature verify FAILED (gpg rc=%s); "
                     "manifest NOT trusted", returncode)
        return None, PolicyLoad.UNTRUSTED
    got = _validsig_primary_fpr(status)
    if got != want:
        logger.error("destructive-policy: signature is valid but signed by %r, "
                     "not the pinned operator key; manifest NOT trusted", got)
        return None, PolicyLoad.UNTRUSTED
    try:
        data = json.loads(raw)       # parse the SAME bytes that were verified
    except ValueError as exc:
        logger.error("destructive-policy: manifest verified but unparseable "
                     "(%s); NOT trusted", type(exc).__name__)
        return None, PolicyLoad.UNTRUSTED
    if not isinstance(data, dict):
        logger.error("destructive-policy: manifest is not a JSON object; NOT trusted")
        return None, PolicyLoad.UNTRUSTED
    return data, PolicyLoad.LOADED


def load_verified_manifest(
    manifest_path: str = DEFAULT_MANIFEST_PATH,
    sig_path: str = DEFAULT_SIGNATURE_PATH,
    *,
    fingerprint: str = OPERATOR_FINGERPRINT,
    gpg_verify: GpgVerify | None = None,
) -> dict | None:
    """Verify the detached signature, then parse the manifest. None on any doubt.

    READ-ONCE: the manifest bytes are read a single time, the detached signature
    is verified over THOSE EXACT bytes, and the SAME bytes are parsed — there is
    no re-open between the check and the parse, so the file cannot be swapped in
    that window (no verify-then-parse TOCTOU). Fail-closed: a missing/unreadable
    file, a non-zero gpg exit, a VALIDSIG whose primary-key fingerprint is not the
    pinned operator key, or unparseable JSON all return None. Use load_policy_status
    when the caller needs to tell a benign ABSENT manifest from an UNTRUSTED one.
    """
    return _load_verified_manifest_status(
        manifest_path, sig_path, fingerprint=fingerprint, gpg_verify=gpg_verify
    )[0]


def load_verified_manifest_status(
    manifest_path: str,
    sig_path: str,
    *,
    fingerprint: str = OPERATOR_FINGERPRINT,
    gpg_verify: GpgVerify | None = None,
) -> "tuple[dict | None, PolicyLoad]":
    """Generic signed-JSON-manifest loader that ALSO reports the load outcome.

    The manifest-agnostic public face of :func:`_load_verified_manifest_status`:
    read-once, gpgv against the pinned operator keyring, VALIDSIG primary-fpr
    pinned to ``fingerprint`` (default :data:`OPERATOR_FINGERPRINT`), fail-closed.
    Returns ``(manifest_dict, LOADED)`` on success, ``(None, ABSENT)`` when no
    artifact is present (benign — a dev/from-source box), or ``(None, UNTRUSTED)``
    for any integrity failure (present-but-unverifiable → the caller must fail
    LOUD; tamper). Use this for any NON-never-list signed manifest (e.g. the wiki
    per-page hash manifest) so the gpgv + pin + read-once + ABSENT/UNTRUSTED logic
    has exactly ONE implementation, never a copy.
    """
    return _load_verified_manifest_status(
        manifest_path, sig_path, fingerprint=fingerprint, gpg_verify=gpg_verify
    )


def load_policy_status(
    manifest_path: str = DEFAULT_MANIFEST_PATH,
    sig_path: str = DEFAULT_SIGNATURE_PATH,
    *,
    fingerprint: str = OPERATOR_FINGERPRINT,
    gpg_verify: GpgVerify | None = None,
) -> "tuple[DestructivePolicy | None, PolicyLoad]":
    """Like load_policy, but ALSO returns the PolicyLoad outcome so the caller can
    distinguish a benign ABSENT manifest (quiet floor fallback) from an UNTRUSTED
    one (present but unverifiable — tamper/corruption, must fail LOUD). PI-D
    hardening. Returns (policy_or_None, outcome).
    """
    manifest, outcome = _load_verified_manifest_status(
        manifest_path, sig_path, fingerprint=fingerprint, gpg_verify=gpg_verify
    )
    if manifest is None:
        return None, outcome
    return DestructivePolicy.from_manifest(manifest), outcome


def load_policy(
    manifest_path: str = DEFAULT_MANIFEST_PATH,
    sig_path: str = DEFAULT_SIGNATURE_PATH,
    *,
    fingerprint: str = OPERATOR_FINGERPRINT,
    gpg_verify: GpgVerify | None = None,
) -> DestructivePolicy | None:
    """Load + verify the signed manifest into a DestructivePolicy, or None.

    None means the never-list could not be established from a trusted source —
    the chokepoint enforcement treats that as its (decided) fail-closed
    posture; this function never hands back a policy built from untrusted bytes.
    """
    return load_policy_status(
        manifest_path, sig_path, fingerprint=fingerprint, gpg_verify=gpg_verify
    )[0]
