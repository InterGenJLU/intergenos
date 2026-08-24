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

WHAT A SEAL PROVES HERE, stated plainly: SHA256SUMS makes a record
tamper-EVIDENT against accident, drift and partial writes. It is not a
signature, so it does not stand against someone who can write to the record
directory and re-seal it. Signing a record with the release key is a separate
step and is named as an open recommendation rather than implied by the word
"sealed".
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

EXIT_VALIDATED = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2

SEAL_NAME = "SHA256SUMS"
RECORD_NAME = "record.json"

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

    present = {
        str(p.relative_to(record_dir))
        for p in record_dir.rglob("*")
        if p.is_file() and p.name != SEAL_NAME
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
