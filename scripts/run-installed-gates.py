#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Run the installed-system gate tier and write a sealed record of what happened.

The tier in tests/installed/ measures properties that only exist once the
software is installed: real file modes under a real home, the real hardened
unit, the real embedding corpus answering real sentences. This runner is what
turns one of those runs into something a later stage can REFUSE on — a record
that names the machine, names the release it measured, carries the complete
output, lists every gate's outcome, and is sealed so it can be shown to be the
record that was written.

TWO REFUSALS, BOTH BEFORE ANY RECORD EXISTS. They are refusals rather than
failures because a record produced under either condition would be worse than no
record: it would look like evidence.

  1. NOT FROM A SOURCE CHECKOUT. Running with a checkout as the working
     directory risks the source tree shadowing the installed package, and a run
     against source says nothing about what a user receives. Checked twice —
     structurally, by looking for a checkout above the working directory, and by
     MEASUREMENT, by asking a subprocess where `intergen` actually resolves from
     the directory the gates will run in. The measurement is the one that
     matters; the structural check is what catches the mistake early and names
     it plainly.
  2. NOT WITH AN EMPTY TIER. If collection finds no gates, every later stage
     would read "0 failed" as success. A run that measured nothing must refuse
     to describe itself as a run.

A FAILING RUN IS NOT A REFUSAL. When the gates run and some of them fail, that
is a result, and the record is written and sealed exactly as a green one is. The
release gate refuses on it. That distinction is the whole point: this runner
reports, the gate decides.

MEASURED vs DECLARED, kept apart in the record. The release number, the
installed manifest digest, the package path and the machine are READ from the
system. The recipe content_hash cannot be recomputed from an installed system —
it describes the source that produced the archive — so when it is supplied it is
recorded under "declared" and never under "measured", and the record says which
is which rather than presenting both as facts of equal standing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2

GATE_ENV = "INTERGENOS_INSTALLED_GATES"
SEAL_NAME = "SHA256SUMS"
PKM_DB = "/var/lib/igos/pkm.db"
_INSTALLED_PREFIXES = ("/usr/lib/", "/usr/lib64/", "/usr/local/lib/")


def refuse(message: str) -> None:
    print(f"[run-installed-gates] REFUSED: {message}", file=sys.stderr)
    print("[run-installed-gates] No record was written. A record produced under "
          "this condition would look like evidence without being any.",
          file=sys.stderr)
    sys.exit(EXIT_REFUSED)


def _checkout_above(start: Path) -> Path | None:
    """The nearest ancestor that is a git working tree, or None."""
    for d in [start, *start.parents]:
        if (d / ".git").exists():
            return d
    return None


def _resolve_intergen_from(cwd: Path) -> str:
    """Where `import intergen` actually lands when run from ``cwd``.

    Measured in a subprocess rather than reasoned about, because the answer
    depends on sys.path construction that this process has already done
    differently for itself.
    """
    probe = subprocess.run(
        [sys.executable, "-c",
         "import intergen, sys; sys.stdout.write(intergen.__file__ or '')"],
        capture_output=True, text=True, cwd=str(cwd), timeout=120)
    if probe.returncode != 0:
        refuse("the installed assistant package could not be imported from the "
               f"directory the gates would run in ({cwd}): "
               f"{probe.stderr.strip() or 'no error text'}")
    return probe.stdout.strip()


def _installed_identity() -> dict:
    """Release and manifest digest of the INSTALLED assistant package."""
    if not Path(PKM_DB).is_file():
        refuse(f"{PKM_DB} is absent, so the installed release cannot be read. "
               f"A record that cannot name the release it measured is not a "
               f"record of anything.")
    try:
        con = sqlite3.connect(f"file:{PKM_DB}?immutable=1", uri=True)
        row = con.execute(
            "SELECT version, release, manifest_sha256, install_date "
            "FROM installed WHERE name = ? AND superseded_by IS NULL",
            ("intergen",)).fetchone()
    except sqlite3.Error as e:
        refuse(f"the package database could not be read ({e}).")
    if row is None:
        refuse("the assistant package is not recorded as installed on this "
               "machine, so there is nothing here to validate.")
    version, release, manifest_sha256, install_date = row
    return {
        "intergen_version": version,
        "intergen_release": int(release),
        "installed_manifest_sha256": manifest_sha256,
        "install_date": install_date,
    }


def _os_release() -> dict[str, str]:
    out: dict[str, str] = {}
    path = Path("/etc/os-release")
    if not path.is_file():
        refuse("/etc/os-release is absent; this machine cannot be identified.")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _parse_junit(path: Path) -> list[dict]:
    """Per-gate outcomes, read from pytest's own report rather than from its
    printed summary — a summary line is a rendering, the report is the data."""
    gates: list[dict] = []
    tree = ET.parse(path)
    for case in tree.iter("testcase"):
        file_part = case.get("file") or case.get("classname") or ""
        test_id = f"{file_part}::{case.get('name')}"
        outcome, reason = "passed", ""
        for child in case:
            if child.tag == "failure":
                outcome = "failed"
                reason = (child.get("message") or "").strip()
            elif child.tag == "error":
                outcome = "error"
                reason = (child.get("message") or "").strip()
            elif child.tag == "skipped":
                outcome = "skipped"
                reason = (child.get("message") or "").strip()
        gates.append({"id": test_id, "outcome": outcome, "reason": reason})
    return gates


def _seal(record_dir: Path) -> str:
    lines = []
    for p in sorted(record_dir.rglob("*")):
        if p.is_file() and p.name != SEAL_NAME:
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            lines.append(f"{digest}  {p.relative_to(record_dir)}\n")
    seal = record_dir / SEAL_NAME
    seal.write_text("".join(lines), encoding="utf-8")
    return hashlib.sha256(seal.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the installed-system gates and seal a run record.")
    parser.add_argument("--output", required=True, type=Path,
                        help="directory to write the run record into")
    parser.add_argument("--tier", type=Path, default=None,
                        help="the installed-gate tier directory "
                             "(default: tests/installed beside this script's repo)")
    parser.add_argument("--declare-content-hash", default=None,
                        help="the candidate recipe content_hash, recorded as "
                             "DECLARED — it cannot be measured from an install")
    args = parser.parse_args(argv)

    cwd = Path.cwd()
    checkout = _checkout_above(cwd)
    if checkout is not None:
        refuse(f"the working directory {cwd} is inside a source checkout "
               f"({checkout}). Run from a directory outside any checkout and "
               f"pass the tier with --tier, so the installed package cannot be "
               f"shadowed by the source tree.")

    tier = args.tier or (Path(__file__).resolve().parents[1] / "tests" / "installed")
    tier = tier.resolve()
    if not tier.is_dir():
        refuse(f"the installed-gate tier is not at {tier}.")

    resolved = _resolve_intergen_from(cwd)
    if not resolved.startswith(_INSTALLED_PREFIXES) or \
            "/site-packages/" not in resolved:
        refuse(f"`import intergen` resolves to {resolved!r}, which is not an "
               f"installed package path — it reads as a source checkout. The "
               f"gates would measure source, not the shipped system.")

    env = dict(os.environ)
    env[GATE_ENV] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    collect = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", str(tier)],
        capture_output=True, text=True, cwd=str(cwd), env=env, timeout=1800)
    collected = sum(1 for line in collect.stdout.splitlines() if "::" in line)
    if collected == 0:
        refuse("the tier collected no gates. Every later stage would read "
               "'0 failed' as success, so a run that measured nothing refuses "
               "to describe itself as a run.\n"
               f"collect-only said:\n{collect.stdout}\n{collect.stderr}")

    out: Path = args.output
    if out.exists():
        refuse(f"{out} already exists. A run record is written once; refusing "
               f"to write over one that may be somebody's evidence.")
    staging = Path(tempfile.mkdtemp(prefix="installed-gate-record-"))
    started = datetime.now(timezone.utc).isoformat()
    junit = staging / "junit.xml"
    capture = staging / "pytest-output.txt"

    invocation = [sys.executable, "-m", "pytest", "-q",
                  f"--junitxml={junit}", str(tier)]
    run = subprocess.run(invocation, capture_output=True, text=True,
                         cwd=str(cwd), env=env, timeout=7200)
    finished = datetime.now(timezone.utc).isoformat()
    capture.write_text(run.stdout + run.stderr, encoding="utf-8")

    if not junit.is_file():
        shutil.rmtree(staging, ignore_errors=True)
        refuse("pytest wrote no report, so no per-gate outcome can be read.")
    gates = _parse_junit(junit)
    if not gates:
        shutil.rmtree(staging, ignore_errors=True)
        refuse("the report contains no gates, so nothing was measured.")

    osr = _os_release()
    identity = _installed_identity()
    outcome = {
        "collected": len(gates),
        "passed": sum(1 for g in gates if g["outcome"] == "passed"),
        "failed": sum(1 for g in gates if g["outcome"] == "failed"),
        "skipped": sum(1 for g in gates if g["outcome"] == "skipped"),
        "errors": sum(1 for g in gates if g["outcome"] == "error"),
        "pytest_returncode": run.returncode,
    }
    record = {
        "record_version": 1,
        "machine": {
            "hostname": socket.gethostname(),
            "os_id": osr.get("ID", ""),
            "os_version_id": osr.get("VERSION_ID", ""),
            "kernel": platform.release(),
        },
        "candidate": {
            "intergen_release": identity["intergen_release"],
            "intergen_content_hash": args.declare_content_hash or "",
            "image_id": osr.get("IMAGE_ID", osr.get("ID", "")),
            "image_build_id": osr.get("IMAGE_VERSION", osr.get("VERSION_ID", "")),
        },
        "measured": {
            "intergen_version": identity["intergen_version"],
            "intergen_release": identity["intergen_release"],
            "installed_manifest_sha256": identity["installed_manifest_sha256"],
            "install_date": identity["install_date"],
            "resolved_package_file": resolved,
        },
        "declared": {
            "intergen_content_hash": args.declare_content_hash or "",
            "note": "a recipe content_hash describes the SOURCE that produced "
                    "the archive and cannot be recomputed from an installed "
                    "system; it is carried here as declared, not measured.",
        },
        "installed_package_path": str(Path(resolved).parent),
        "invocation": {
            "argv": invocation,
            "env": {GATE_ENV: "1"},
            "cwd": str(cwd),
            "tier": str(tier),
        },
        "started_utc": started,
        "finished_utc": finished,
        "outcome": outcome,
        "gates": gates,
        "capture": "pytest-output.txt",
        "junit": "junit.xml",
    }
    (staging / "record.json").write_text(json.dumps(record, indent=2),
                                         encoding="utf-8")
    seal_digest = _seal(staging)

    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(staging), str(out))
    verdict = "GREEN" if outcome["failed"] == 0 and outcome["errors"] == 0 \
        else "RED"
    print(f"[run-installed-gates] record written: {out}")
    print(f"[run-installed-gates] {verdict} — {outcome['collected']} gates: "
          f"{outcome['passed']} passed, {outcome['failed']} failed, "
          f"{outcome['errors']} error, {outcome['skipped']} skipped")
    print(f"[run-installed-gates] release {identity['intergen_release']} on "
          f"{socket.gethostname()}; seal {SEAL_NAME} sha256 {seal_digest}")
    print("[run-installed-gates] This is a report, not a verdict on the "
          "release. scripts/check-release-validation.py decides.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
