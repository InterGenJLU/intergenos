#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Verify a signed corrective-republish record against the staged archives.

The mirror publisher refuses a staged archive whose bytes differ from the
served entry unless its (version, release) is strictly newer (the advancement
gate).  A CORRECTIVE republish — replacing served bytes under the SAME
version-release because the served archive was wrong — is the one designed
exception, and it is admitted only through a signed record that names exactly
what changes and why:

  record.json      schema 1 (below), signed as record.json.asc (detached,
                   armored) by the release key the publisher signs with.
  record.json.asc  must verify, and the VALIDSIG primary-key fingerprint must
                   equal the pinned release-key fingerprint.

Schema 1 (every key required, no others accepted):
  {
    "schema": 1,
    "incident": "<identifier: letters, digits, . _ ->",
    "reason": "<one bounded line, at most 500 characters>",
    "served_index_sha256": "<sha256 of the served InterGenOS.db this record binds to>",
    "exceptions": [
      {"name": "...", "version": "...", "release": <int>,
       "served_sha256": "<sha256 of the archive the index serves now>",
       "replacement_sha256": "<sha256 of the staged archive that replaces it>"}
    ]
  }

What is re-derived here and must match the record exactly:
  * the served index digest;
  * the NON-MONOTONIC set — every staged archive whose bytes differ from the
    served entry and whose (version, release) is not strictly newer, derived
    with pkm's own version comparison exactly as the advancement gate does;
  * per package: the served digest (from the served index row), the
    replacement digest (from the staged archive bytes), version and release.
  A record naming a package that advances normally, an unchanged package, a
  package that is not staged, or missing one of the non-monotonic packages is
  refused.  An empty non-monotonic set is refused: a record with nothing to
  authorize must not ride along.
  * With --generated-index, every exception's row in the generated index must
    carry the replacement digest at the recorded version-release (the index is
    not byte-stable — it carries a generation time — so its rows are bound
    rather than its digest; the transparency-log entry binds the final index
    digest to this record).

Exit status: 0 accepted · 2 refused (reason on stderr) · 1 usage/input error.
"""

from __future__ import annotations

import argparse
import glob
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from pkm.version import VersionParseError, compare  # noqa: E402

RECORD_MAX_BYTES = 64 * 1024
REASON_MAX_CHARS = 500
INCIDENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOP_KEYS = {"schema", "incident", "reason", "served_index_sha256", "exceptions"}
EXC_KEYS = {"name", "version", "release", "served_sha256", "replacement_sha256"}


def refuse(message: str) -> "None":
    print(f"REFUSED: {message}", file=sys.stderr)
    raise SystemExit(2)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_pkginfo(archive: Path) -> dict:
    with tarfile.open(archive, "r:gz") as tar:
        for candidate in ("./.PKGINFO", ".PKGINFO"):
            try:
                member = tar.getmember(candidate)
            except KeyError:
                continue
            fields = {}
            for line in tar.extractfile(member).read().decode().splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    fields[key.strip()] = value.strip()
            return fields
    return {}


def verify_signature(record: Path, signature: Path, fingerprint: str) -> str:
    """Return the fingerprint that matched the pin; refuse on any failure.

    The publisher pins the fingerprint of the key it SIGNS with, which is a
    signing SUBKEY of the release key (the card's [S1] slot); a caller may pin
    the primary instead.  gpg's VALIDSIG line carries both — the signing-key
    fingerprint first and the primary-key fingerprint last — so the pin is
    accepted when it equals either one, and the match is named in the output.
    Found by the real-key firing of 2026-09-14: a record signed on the token
    verified with primary 5597… while the publisher pinned the subkey D7AA…."""
    try:
        proc = subprocess.run(
            ["gpg", "--batch", "--no-tty", "--status-fd", "1", "--verify",
             str(signature), str(record)],
            capture_output=True, text=True, timeout=60, check=False)
    except FileNotFoundError:
        refuse("gpg is not available; the record signature cannot be verified")
    except subprocess.TimeoutExpired:
        refuse("gpg timed out verifying the record signature")
    signing_key = primary = None
    for line in proc.stdout.splitlines():
        if line.startswith("[GNUPG:] VALIDSIG "):
            fields = line.split()
            signing_key, primary = fields[2], fields[-1]
    if proc.returncode != 0:
        tail = (proc.stderr.strip().splitlines() or ["no output"])[-1]
        refuse(f"record signature does not verify (gpg rc={proc.returncode}: {tail})")
    if primary is None or signing_key is None:
        refuse("gpg exited 0 but emitted no VALIDSIG status line")
    pin = fingerprint.upper()
    if signing_key.upper() == pin:
        return f"signing key {signing_key} (the pinned key)"
    if primary.upper() == pin:
        return f"signing key {signing_key} under the pinned primary key {primary}"
    refuse(f"record signature is valid but neither its signing key {signing_key} nor its "
           f"primary key {primary} is the pinned release key {fingerprint}")


def load_record(path: Path) -> dict:
    if not path.is_absolute():
        refuse(f"record path must be absolute: {path}")
    if path.is_symlink() or not path.is_file():
        refuse(f"record is not a regular file: {path}")
    if path.stat().st_size > RECORD_MAX_BYTES:
        refuse(f"record exceeds {RECORD_MAX_BYTES} bytes: {path}")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        refuse(f"record is not valid UTF-8 JSON: {exc}")
    if not isinstance(doc, dict):
        refuse("record top level is not an object")
    keys = set(doc)
    if keys != TOP_KEYS:
        refuse(f"record keys must be exactly {sorted(TOP_KEYS)}; got {sorted(keys)}")
    if doc["schema"] != 1:
        refuse(f"record schema must be 1; got {doc['schema']!r}")
    if not isinstance(doc["incident"], str) or not INCIDENT_RE.match(doc["incident"]):
        refuse("record incident must be 1-64 characters of letters, digits, . _ -")
    reason = doc["reason"]
    if (not isinstance(reason, str) or not reason.strip()
            or len(reason) > REASON_MAX_CHARS or "\n" in reason or "\r" in reason):
        refuse(f"record reason must be one non-empty line of at most {REASON_MAX_CHARS} characters")
    if not isinstance(doc["served_index_sha256"], str) or not SHA256_RE.match(doc["served_index_sha256"]):
        refuse("record served_index_sha256 must be a lower-case sha256 hex digest")
    exceptions = doc["exceptions"]
    if not isinstance(exceptions, list) or not exceptions:
        refuse("record exceptions must be a non-empty list")
    names = set()
    for entry in exceptions:
        if not isinstance(entry, dict) or set(entry) != EXC_KEYS:
            refuse(f"each exception must have exactly the keys {sorted(EXC_KEYS)}")
        if not isinstance(entry["name"], str) or not entry["name"]:
            refuse("exception name must be a non-empty string")
        if entry["name"] in names:
            refuse(f"exception {entry['name']} is listed more than once")
        names.add(entry["name"])
        if not isinstance(entry["version"], str) or not entry["version"]:
            refuse(f"exception {entry['name']}: version must be a non-empty string")
        if isinstance(entry["release"], bool) or not isinstance(entry["release"], int) or entry["release"] < 1:
            refuse(f"exception {entry['name']}: release must be a positive integer")
        for key in ("served_sha256", "replacement_sha256"):
            if not isinstance(entry[key], str) or not SHA256_RE.match(entry[key]):
                refuse(f"exception {entry['name']}: {key} must be a lower-case sha256 hex digest")
        if entry["served_sha256"] == entry["replacement_sha256"]:
            refuse(f"exception {entry['name']}: served and replacement digests are equal — nothing is corrected")
    return doc


def load_index(path: Path) -> dict:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError) as exc:
        refuse(f"index {path} could not be read as gzipped JSON: {exc}")
    packages = doc.get("packages") if isinstance(doc, dict) else None
    if not isinstance(packages, dict):
        refuse(f"index {path} has no packages mapping")
    return packages


def non_monotonic_set(archive_dir: Path, served: dict) -> dict:
    """{name: {version, release, served_sha256, replacement_sha256}} for every
    staged archive whose bytes differ from the served entry without a strictly
    newer (version, release) — the advancement gate's own rule."""
    found = {}
    for arc in sorted(glob.glob(os.path.join(archive_dir, "*.igos.tar.gz"))):
        info = read_pkginfo(Path(arc))
        name, ver, rel = info.get("pkgname"), info.get("pkgver"), info.get("pkgrel")
        if not name or name not in served:
            continue
        row = served[name]
        staged_sha = sha256_file(Path(arc))
        if row.get("sha256") == staged_sha:
            continue
        staged = {"version": ver, "release": rel}
        live = {"version": row.get("version"), "release": row.get("release", 1)}
        try:
            advances = compare(staged, live) > 0
        except VersionParseError as exc:
            refuse(f"{name}: staged version-release cannot be compared: {exc}")
        if advances:
            continue
        found[name] = {
            "version": ver,
            "release": rel,
            "served_sha256": row.get("sha256"),
            "replacement_sha256": staged_sha,
        }
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--record", required=True, help="absolute path of record.json (record.json.asc beside it)")
    parser.add_argument("--served-index", required=True, help="the served InterGenOS.db this publish replaces")
    parser.add_argument("--archive-dir", required=True, help="the staged archive directory")
    parser.add_argument("--fingerprint", required=True, help="the pinned release-key primary fingerprint")
    parser.add_argument("--generated-index", default=None,
                        help="after index generation: the generated InterGenOS.db whose rows must carry the replacement digests")
    args = parser.parse_args()

    record_path = Path(args.record)
    doc = load_record(record_path)
    signature = record_path.with_name(record_path.name + ".asc")
    if signature.is_symlink() or not signature.is_file():
        refuse(f"record signature is absent: {signature}")
    signer = verify_signature(record_path, signature, args.fingerprint)

    served_path = Path(args.served_index)
    if not served_path.is_file():
        refuse(f"served index is absent: {served_path}")
    served_digest = sha256_file(served_path)
    if served_digest != doc["served_index_sha256"]:
        refuse(f"served index digest {served_digest} is not the record's {doc['served_index_sha256']}; "
               "the record binds a different served state")
    served = load_index(served_path)

    archive_dir = Path(args.archive_dir)
    if not archive_dir.is_dir():
        refuse(f"archive directory is absent: {archive_dir}")
    derived = non_monotonic_set(archive_dir, served)
    if not derived:
        refuse("no staged archive replaces served bytes at a same-or-older version-release; "
               "the record has nothing to authorize and must not ride along")

    recorded = {entry["name"]: entry for entry in doc["exceptions"]}
    missing = sorted(set(derived) - set(recorded))
    extra = sorted(set(recorded) - set(derived))
    if missing:
        refuse("the record omits non-monotonic staged package(s): " + ", ".join(missing))
    if extra:
        refuse("the record names package(s) that are not non-monotonic staged changes: " + ", ".join(extra))
    for name in sorted(derived):
        want, have = derived[name], recorded[name]
        if str(have["version"]) != str(want["version"]) or int(have["release"]) != int(want["release"]):
            refuse(f"{name}: record says {have['version']}-{have['release']}, staged is {want['version']}-{want['release']}")
        if have["served_sha256"] != want["served_sha256"]:
            refuse(f"{name}: record served_sha256 {have['served_sha256']} is not the served index row's {want['served_sha256']}")
        if have["replacement_sha256"] != want["replacement_sha256"]:
            refuse(f"{name}: record replacement_sha256 {have['replacement_sha256']} is not the staged archive's {want['replacement_sha256']}")

    if args.generated_index:
        generated = load_index(Path(args.generated_index))
        for name in sorted(derived):
            row = generated.get(name)
            if row is None:
                refuse(f"{name}: absent from the generated index")
            if row.get("sha256") != derived[name]["replacement_sha256"]:
                refuse(f"{name}: generated index row digest {row.get('sha256')} is not the replacement {derived[name]['replacement_sha256']}")
            if str(row.get("version")) != str(derived[name]["version"]) or str(row.get("release", 1)) != str(derived[name]["release"]):
                refuse(f"{name}: generated index row is {row.get('version')}-{row.get('release', 1)}, "
                       f"the record binds {derived[name]['version']}-{derived[name]['release']}")

    print(f"corrective-republish record ACCEPTED: incident {doc['incident']}, "
          f"{len(derived)} same-version replacement(s), signed by {signer}")
    print(f"  served index sha256 {served_digest}")
    print(f"  reason: {doc['reason']}")
    for name in sorted(derived):
        entry = derived[name]
        print(f"  {name} {entry['version']}-{entry['release']}: "
              f"{entry['served_sha256'][:16]}… -> {entry['replacement_sha256'][:16]}…")
    print("  CONSEQUENCE: clients already at these version-release pairs will NOT receive the")
    print("  corrected bytes through `pkm upgrade`; only a fresh fetch (install or reinstall) does.")
    if args.generated_index:
        print("  generated index rows carry the replacement digests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
