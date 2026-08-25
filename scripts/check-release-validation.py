#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Refuse a promotion, an image or a mirror publish that has not been validated.

The installed-system gate tier measures what a source-tree test cannot: the
properties that only exist once the software is installed and running on real
hardware. This script is what makes running it mandatory rather than advisable.
It answers one question, and it answers it about a NAMED candidate:

    has this exact release been run through the installed-system gates, on a
    real installed machine, with nothing failing and nothing silently skipped?

THE POSTURE, stated once and applied everywhere below. A missing record, an
unreadable record, a record about something else, a record that cannot be shown
to be intact — every one of them is NOT VALIDATED. None of them is validated.
The difference matters because the failure this gate exists to prevent is not a
forged record; it is a release going out while everyone assumes somebody else
ran the checks.

THERE IS NO OVERRIDE FLAG, and that is a decision rather than an omission. A
flag that lets a release past this gate would be used the first time a release
was urgent, which is exactly the release that most needs the checks.

EXIT CODES ARE DISTINCT ON PURPOSE:
    0  validated  — a sealed, matching, green record exists
    1  REFUSED    — not validated, for a reason printed above the exit
    2  usage      — the command line was wrong; nothing was decided
A refusal and a usage error must never share a code, or a caller cannot tell
"this release is not validated" from "I typed the command wrong", and a pipeline
that cannot tell them apart will eventually treat the second as the first.

WHAT A SEAL PROVES, AND WHAT THE SIGNATURE ADDS. SHA256SUMS makes a record
tamper-EVIDENT against accident, drift and partial writes. It is not a
signature: it does not stand against anyone who can write to the record
directory, because they can re-seal it. That is not hypothetical — this gate
was once satisfied by a synthetic green record minted by hand, sealed,
internally consistent, and about nothing at all.

Since 2026-08-25 the seal itself must carry a detached OpenPGP signature made by
the release key on the operator's hardware token, and this gate verifies it
before it reads anything else. That is what turns the record from a
self-consistent artifact into the operator's attestation: anyone can still MAKE
a record, but only the operator can make one this gate accepts.

Verification mirrors check-wiki-manifest.py exactly, for the same reasons:
gpgv with an explicit --keyring, never `gpg --verify` (gpg 2.5.x with
use-keyboxd silently ignores --keyring, which would verify against the caller's
own keyring instead of the root's), and the VALIDSIG primary-key fingerprint
must equal the pinned fingerprint. The keyring is only the vehicle; trust
derives from the in-code pin.

TWO TIERS OF FAILURE, and only one of them can ever be relaxed:
  * PRESENT but unverifiable — a bad signature, the wrong key, a signature
    lifted from another record: REFUSED in every mode, always. Something is
    wrong with THIS record and no marker waves that through.
  * ABSENT — no signature at all: REFUSED on a release path; downgraded to a
    LOUD warning under UNSIGNED_TEST=1, the established dev/test-image marker,
    which is how an unsigned development image is built without weakening what
    a release requires. Never silent: a downgrade nobody can see in the output
    is indistinguishable from a verified record.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

EXIT_VALIDATED = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2

SEAL_NAME = "SHA256SUMS"
SIG_NAME = "SHA256SUMS.asc"
RECORD_NAME = "record.json"

# Lockstep with check-wiki-manifest.py's OPERATOR_FINGERPRINT and the runtime's
# pin in intergen/destructive_policy.py. Re-declared rather than imported so the
# gate runs on a bare build host. Two gates disagreeing about the operator's key
# is a condition nobody would notice until a release.
OPERATOR_FINGERPRINT = "5597A3E0587B253006D0DD7B8C50826182083050"

# The root's own shipped trust keyring, the same one the wiki-manifest chain
# verifies against.
DEFAULT_KEYRING = Path("/etc/pkm/trusted.gpg")

# An installed package lives under the system library tree. A path anywhere else
# — a home directory, a build tree, a worktree — describes source, and a record
# about source says nothing about what a user would receive.
_INSTALLED_PREFIXES = ("/usr/lib/", "/usr/lib64/", "/usr/local/lib/")


def refuse(message: str) -> None:
    """Print one reason and exit REFUSED. Every refusal path goes through here,
    so a refusal cannot accidentally acquire a different exit code."""
    print(f"[check-release-validation] REFUSED: {message}", file=sys.stderr)
    print("[check-release-validation] This release is NOT VALIDATED. A "
          "promotion, an installation image or a mirror publish of it is "
          "refused.", file=sys.stderr)
    sys.exit(EXIT_REFUSED)


def _read_expected_skips(path: Path) -> dict[str, str]:
    """Test ids a human declared may skip, with the reason they gave.

    FAIL-CLOSED: an absent or unreadable allowlist refuses. An undefined
    allowlist must never mean "anything may skip" — that is the same trap the
    public-language gate's private term list closes by failing loudly instead of
    scanning nothing.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        refuse(f"the expected-skip allowlist at {path} could not be read ({e}). "
               f"An allowlist that cannot be read is not an empty allowlist.")
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        test_id, _, reason = line.partition("  ")
        out[test_id.strip()] = reason.strip()
    return out


def _gpgv_verify(sig: Path, data: Path, keyring: Path,
                 fingerprint: str) -> "tuple[bool, str]":
    """Verify `data` against detached `sig` with gpgv; enforce the primary-key
    fingerprint from the VALIDSIG status line. Returns (ok, detail).

    gpgv, not `gpg --verify`: gpg 2.5.x with use-keyboxd silently IGNORES
    --keyring, which would verify against whatever keyring the caller happens to
    have — a check that passes for the wrong reason is worse than no check.
    """
    cmd = ["gpgv", "--keyring", str(keyring), "--status-fd", "1",
           str(sig), str(data)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return False, "gpgv is not on PATH, so the signature cannot be checked"
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
        return False, (f"the signature is valid but its primary key fingerprint "
                       f"is {validsig_primary}, not the pinned {fingerprint}")
    return True, validsig_primary


def _verify_signature(record_dir: Path, keyring: Path, fingerprint: str) -> None:
    """The seal must be signed by the release key. Checked before anything else.

    Reading the record before establishing who vouches for it would mean acting
    on unattested content, so this runs first — ahead of the seal, ahead of the
    identity match, ahead of the outcomes.
    """
    seal = record_dir / SEAL_NAME
    sig = record_dir / SIG_NAME
    unsigned_test = os.environ.get("UNSIGNED_TEST") == "1"

    if not sig.is_file():
        if unsigned_test:
            # Loud, on both streams' worth of attention, and naming the marker.
            # A downgrade nobody can see is the failure this branch exists to
            # avoid, not the one it creates.
            print(f"[check-release-validation] WARN: the record at {record_dir} "
                  f"carries NO SIGNATURE ({SIG_NAME} is absent). Accepting it "
                  f"ONLY because UNSIGNED_TEST=1 is set. This record is NOT the "
                  f"operator's attestation and must never be used to justify a "
                  f"release.", file=sys.stderr)
            return
        refuse(f"the record at {record_dir} carries no signature ({SIG_NAME} is "
               f"absent). A seal shows the record did not change; it does not "
               f"show who stands behind it, and anyone able to write this "
               f"directory can re-seal it. The operator signs the seal with the "
               f"release key:\n"
               f"    bash scripts/sign-with-gpg.sh --file {seal}\n"
               f"An unsigned record is not a validated record.")

    if not keyring.is_file():
        # Never downgraded, in any mode: the signature is PRESENT, so this is
        # not the absent case. Without the keyring there is no verification at
        # all, and no verification is not validation.
        refuse(f"the record is signed but the trust keyring at {keyring} is "
               f"absent, so the signature cannot be checked. An unverifiable "
               f"signature is not a verified one.")

    ok, detail = _gpgv_verify(sig, seal, keyring, fingerprint)
    if not ok:
        # PRESENT but bad — refused in every mode, UNSIGNED_TEST included.
        refuse(f"the record's signature does not verify against the pinned "
               f"release key: {detail}. UNSIGNED_TEST does not excuse this: a "
               f"signature that is present and wrong means something is wrong "
               f"with this record, not that it was never signed.")
    # flush=True is load-bearing, not tidiness. Refusals go to stderr, which is
    # unbuffered; this goes to stdout, which is block-buffered when redirected to
    # a file. Without the flush, a capture of a refused-but-signed record shows
    # the verification AFTER the refusal it preceded, and a reader reconstructing
    # what happened from that capture reads the sequence backwards.
    print(f"[check-release-validation] signature verified: {SIG_NAME} over "
          f"{SEAL_NAME}, primary key {detail}", flush=True)


def _verify_seal(record_dir: Path) -> None:
    """Every file present is listed, every listed file is present, all digests
    match. Both directions are checked: a seal that only verifies the files it
    knows about does not notice a record growing a second capture that tells a
    different story."""
    seal = record_dir / SEAL_NAME
    if not seal.is_file():
        refuse(f"the record at {record_dir} carries no {SEAL_NAME}, so it "
               f"cannot be shown to be the record that was written. An unsealed "
               f"record is not a validated record.")
    listed: dict[str, str] = {}
    for line in seal.read_text(encoding="utf-8").splitlines():
        line = line.rstrip("\n")
        if not line.strip():
            continue
        digest, _, name = line.partition("  ")
        if not digest or not name:
            refuse(f"{SEAL_NAME} has a line this gate cannot parse: {line!r}")
        listed[name.strip()] = digest.strip()

    # The signature sidecar is excluded, and cannot be otherwise: it signs
    # SHA256SUMS, so it comes into being after SHA256SUMS is final and can never
    # be listed inside it. Without this exclusion a signed record would fail its
    # own seal check — refused for being signed.
    present = {
        str(p.relative_to(record_dir))
        for p in record_dir.rglob("*")
        if p.is_file() and p.name not in (SEAL_NAME, SIG_NAME)
    }
    missing = sorted(set(listed) - present)
    if missing:
        refuse(f"the seal lists files the record does not contain: {missing}")
    extra = sorted(present - set(listed))
    if extra:
        refuse(f"the record contains files the seal does not cover: {extra}. "
               f"A file added after sealing is not part of what was verified.")
    for name, expected in sorted(listed.items()):
        actual = hashlib.sha256((record_dir / name).read_bytes()).hexdigest()
        if actual != expected:
            refuse(f"the seal does not match {name}: recorded {expected}, "
                   f"computed {actual}. The record changed after it was sealed.")


def _load_record(record_dir: Path) -> dict:
    path = record_dir / RECORD_NAME
    if not path.is_file():
        refuse(f"{path} is absent, so there is no run record to read.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        refuse(f"{path} could not be read as a run record ({e}).")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refuse a release that has no green installed-gate run.")
    parser.add_argument("--record", required=True, type=Path,
                        help="the run record directory to evaluate")
    parser.add_argument("--candidate-release", required=True, type=int,
                        help="the release number being promoted/imaged/published")
    parser.add_argument("--candidate-content-hash", required=True,
                        help="the candidate's recorded content_hash")
    parser.add_argument(
        "--keyring", type=Path, default=DEFAULT_KEYRING,
        help="trust keyring the signature is verified against "
             f"(default: {DEFAULT_KEYRING})")
    parser.add_argument(
        "--fingerprint", default=OPERATOR_FINGERPRINT,
        help="pinned primary-key fingerprint the signature must carry "
             "(default: the project release key)")
    parser.add_argument(
        "--expected-skips", type=Path,
        default=Path(__file__).resolve().parent / "data"
        / "installed-gate-expected-skips.txt",
        help="allowlist of test ids that may skip, with reasons")
    args = parser.parse_args(argv)

    record_dir: Path = args.record
    if not record_dir.is_dir():
        refuse(f"there is no run record at {record_dir}. The installed-system "
               f"gates have not been run for this candidate, or their record "
               f"was not carried here. Absence is not validation.")

    _verify_signature(record_dir, args.keyring, args.fingerprint)
    _verify_seal(record_dir)
    record = _load_record(record_dir)

    machine = record.get("machine") or {}
    hostname = str(machine.get("hostname") or "").strip()
    if not hostname:
        refuse("the record does not name the machine it was produced on. A "
               "verdict from an unnamed machine cannot be re-run or disputed.")
    if machine.get("os_id") != "intergenos":
        refuse(f"the record was produced on a machine identifying as "
               f"{machine.get('os_id')!r}, not an installed InterGenOS system.")

    pkg_path = str(record.get("installed_package_path") or "")
    if not pkg_path.startswith(_INSTALLED_PREFIXES) or \
            "/site-packages/" not in pkg_path:
        refuse(f"the record was produced against {pkg_path!r}, which is not an "
               f"installed package path — it reads as a source checkout. A run "
               f"against source says nothing about what a user receives.")

    candidate = record.get("candidate") or {}
    rec_release = candidate.get("intergen_release")
    rec_hash = str(candidate.get("intergen_content_hash") or "")
    if rec_release != args.candidate_release:
        refuse(f"the record validates release {rec_release}, but the candidate "
               f"is release {args.candidate_release}. A record for another "
               f"release is not a record for this one.")
    if rec_hash != args.candidate_content_hash:
        refuse(f"the record validates content_hash {rec_hash!r}, but the "
               f"candidate's is {args.candidate_content_hash!r}. Same release "
               f"number, different bytes.")

    gates = record.get("gates") or []
    if not gates:
        refuse("the record contains no gates at all. Nothing was measured, so "
               "nothing passed — a run that collected zero tests is the most "
               "dangerous green there is.")

    failed = [g for g in gates if g.get("outcome") in ("failed", "error")]
    if failed:
        names = ", ".join(str(g.get("id")) for g in failed[:10])
        refuse(f"{len(failed)} gate(s) did not pass: {names}"
               f"{' …' if len(failed) > 10 else ''}")

    allowed = _read_expected_skips(args.expected_skips)
    skipped = [g for g in gates if g.get("outcome") == "skipped"]
    undeclared = [g for g in skipped if str(g.get("id")) not in allowed]
    if undeclared:
        names = ", ".join(str(g.get("id")) for g in undeclared[:10])
        refuse(f"{len(undeclared)} gate(s) were skipped without being declared "
               f"in {args.expected_skips}: {names}. A skip is 'not measured', "
               f"and 'not measured' is not 'passed'.")

    print(f"[check-release-validation] VALIDATED: release "
          f"{args.candidate_release} (content_hash {args.candidate_content_hash}) "
          f"— {len(gates)} installed-system gates run on {hostname}, "
          f"{len(gates) - len(skipped)} passed, {len(skipped)} declared skip(s), "
          f"0 failed. Record {record_dir} seal verified.")
    return EXIT_VALIDATED


if __name__ == "__main__":
    sys.exit(main())
