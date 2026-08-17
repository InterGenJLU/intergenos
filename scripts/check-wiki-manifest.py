#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""check-wiki-manifest.py — fail-closed shipped-book == signed-manifest gate.

Verifies, against a built root (the build chroot or an installed system), that
the wiki documentation the image ships is exactly the set of pages the release
manifest was signed over — BEFORE the archive-manifest signing pause, so a
stale or unverifiable wiki signature is refused at build time instead of being
discovered as a cite-time refusal on the installed system.

The verification chain mirrors the runtime consumer (intergen/wiki_citations.py
via intergen/destructive_policy.py) exactly, so gate-green implies cite-green:

  1. ``pages-manifest.json`` + detached ``pages-manifest.json.asc`` are loaded
     as a pair from the shipped doc root and verified with ``gpgv --keyring``
     against the root's own shipped trust keyring (/etc/pkm/trusted.gpg).
     ``gpg --verify`` is deliberately NOT used: gpg 2.5.x + use-keyboxd
     silently ignores ``--keyring``.
  2. The VALIDSIG primary-key fingerprint from gpgv's status output must equal
     the pinned operator fingerprint. The keyring is only the verification
     vehicle; trust derives from the in-code pin.
  3. Every page listed in the manifest must exist under the doc root and hash
     (sha256) to its pinned value, and every rendered ``*.html`` under the doc
     root must be listed in the manifest — an unlisted page is drift the
     signature does not cover.

Failure semantics (fail-closed, mirroring the runtime's two-tier behavior):
  * PRESENT but unverifiable / mismatched (bad signature, wrong key, tampered
    or drifted pages, corrupt JSON): always exit 1, in every mode.
  * ABSENT wiki (no doc root, or manifest pair missing): exit 1 on a release
    firing; downgraded to a loud WARN + exit 0 only under UNSIGNED_TEST=1
    (the established dev/test-ISO marker) — a dev image without wiki inputs
    simply ships citations-off, which is the runtime's quiet-ABSENT path.

Fingerprint pin: keep in lockstep with OPERATOR_FINGERPRINT in
intergen/destructive_policy.py (the runtime's pin). The gate re-declares it
rather than importing intergen so it runs on a bare build host.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

# Lockstep with intergen/destructive_policy.py OPERATOR_FINGERPRINT.
OPERATOR_FINGERPRINT = "5597A3E0587B253006D0DD7B8C50826182083050"

DOC_REL = "usr/share/doc/intergenos/wiki"
KEYRING_REL = "etc/pkm/trusted.gpg"
MANIFEST_NAME = "pages-manifest.json"
SIG_SUFFIX = ".asc"


def log(msg: str) -> None:
    print(f"[check-wiki-manifest] {msg}")


def fail(msg: str) -> None:
    print(f"[check-wiki-manifest] FAIL: {msg}")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def gpgv_verify(sig: Path, data: Path, keyring: Path, fingerprint: str) -> "tuple[bool, str]":
    """Verify data against its detached sig with gpgv; enforce the primary-key
    fingerprint from the VALIDSIG status line. Returns (ok, detail)."""
    cmd = [
        "gpgv", "--keyring", str(keyring),
        "--status-fd", "1",
        str(sig), str(data),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return False, "gpgv not found on PATH"
    except subprocess.TimeoutExpired:
        return False, "gpgv timed out"
    validsig_primary = None
    for line in proc.stdout.splitlines():
        # [GNUPG:] VALIDSIG <sig-fpr> <date> ... <primary-key-fpr>
        if line.startswith("[GNUPG:] VALIDSIG "):
            fields = line.split()
            if len(fields) >= 4:
                validsig_primary = fields[-1]
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return False, f"gpgv rc={proc.returncode}: {detail[-1] if detail else 'no output'}"
    if validsig_primary is None:
        return False, "gpgv exited 0 but emitted no VALIDSIG status line"
    if validsig_primary != fingerprint:
        return False, (f"signature valid but primary key fingerprint "
                       f"{validsig_primary} != pinned {fingerprint}")
    return True, validsig_primary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True,
                    help="Built root to verify (the build chroot, e.g. /mnt/igos, "
                         "or an installed system's /)")
    ap.add_argument("--fingerprint", default=OPERATOR_FINGERPRINT,
                    help="Override the pinned operator fingerprint (tests only)")
    ap.add_argument("--keyring", default=None,
                    help="Override the trust keyring path (default: <root>/etc/pkm/trusted.gpg)")
    args = ap.parse_args()

    root = Path(args.root)
    doc_root = root / DOC_REL
    keyring = Path(args.keyring) if args.keyring else root / KEYRING_REL
    unsigned_test = os.environ.get("UNSIGNED_TEST", "") == "1"

    def absent(msg: str) -> int:
        if unsigned_test:
            log(f"WARN (UNSIGNED_TEST=1, dev image): {msg} — shipping citations-off")
            return 0
        fail(f"{msg} — a release image must ship the signed wiki book")
        return 1

    if not doc_root.is_dir():
        return absent(f"wiki doc root absent: {doc_root}")

    manifest_path = doc_root / MANIFEST_NAME
    sig_path = Path(str(manifest_path) + SIG_SUFFIX)
    if not manifest_path.is_file() or not sig_path.is_file():
        return absent(f"signed manifest pair incomplete under {doc_root} "
                      f"(need {MANIFEST_NAME} + {MANIFEST_NAME}{SIG_SUFFIX})")

    # Present from here on: every failure is fatal in every mode.
    if not keyring.is_file():
        fail(f"trust keyring absent: {keyring} — cannot verify the shipped signature")
        return 1

    ok, detail = gpgv_verify(sig_path, manifest_path, keyring, args.fingerprint)
    if not ok:
        fail(f"pages-manifest signature verification failed: {detail}")
        return 1

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        pages = manifest["pages"]
        if not isinstance(pages, dict) or not pages:
            raise ValueError("manifest 'pages' is not a non-empty object")
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        fail(f"signed manifest unreadable/malformed: {exc}")
        return 1

    mismatched, missing = [], []
    for rel, pinned in sorted(pages.items()):
        page = doc_root / rel
        if not page.is_file():
            missing.append(rel)
            continue
        actual = sha256_file(page)
        if actual != str(pinned):
            mismatched.append(f"{rel} (shipped {actual[:16]}… != pinned {str(pinned)[:16]}…)")

    listed = set(pages.keys())
    unlisted = sorted(
        str(p.relative_to(doc_root))
        for p in doc_root.rglob("*.html")
        if str(p.relative_to(doc_root)) not in listed
    )

    if missing or mismatched or unlisted:
        for rel in missing:
            fail(f"manifest page MISSING from shipped book: {rel}")
        for entry in mismatched:
            fail(f"shipped page does not match signed manifest: {entry}")
        for rel in unlisted:
            fail(f"shipped page NOT COVERED by the signed manifest: {rel}")
        fail(f"shipped book != signed manifest "
             f"({len(missing)} missing, {len(mismatched)} mismatched, {len(unlisted)} unlisted) "
             f"— regenerate with scripts/build-wiki-page-manifest.py and re-sign "
             f"(sign-with-gpg.sh) BEFORE the wiki tarball regenerates")
        return 1

    log(f"PASS: {len(pages)} shipped pages match the signed manifest; "
        f"signature Good, primary key {args.fingerprint}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
