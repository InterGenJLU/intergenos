"""GATE 14 — every manifest entry marked expected has an archive.

WHY THIS GATE EXISTS. The installer's integrity verifier walks the archives it FINDS
and checks each one against the signed manifest. It never asks the opposite question:
does every entry the manifest promises actually have an archive? A package that was
supposed to ship and did not is therefore invisible to the verifier — the install
completes, the audit log records success, and the missing software is discovered later
by whoever needed it.

The evidence that this is not hypothetical is on this machine's own install record: the
integrity audit log reports a manifest of 1145 entries and 861 archives checked. The
verifier reported success. Nothing in that record distinguishes 284 entries that were
never meant to ship on this media from 284 packages that went missing.

HOW THIS GATE MEASURES IT. It calls the SHIPPED verifier against a small archive
directory and a manifest that promises one archive which is not there. Only the
manifest SIGNATURE check is replaced — a synthetic manifest cannot carry the release
signature, and the signature is not the property under test. The walk, the hashing, the
cross-reference, the callbacks and the audit log are all the shipped code. The test
records that substitution here rather than leaving a reader to infer it.

EXPECTED TO FAIL ON R001.1 AS SHIPPED.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest


def _make_archive(path: Path, content: bytes) -> str:
    """Write a real .igos.tar.gz and return its sha256."""
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo("payload")
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    raw = buf.getvalue()
    path.write_bytes(gzip.compress(raw))
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def shipped_verifier():
    try:
        from installer.backend import integrity
    except ImportError as exc:
        pytest.fail(
            "The shipped installer's integrity module is not importable on this "
            f"installed system ({exc}), so the install verifier cannot be exercised.")
    return integrity


def test_the_verifier_fails_when_a_promised_archive_is_absent(shipped_verifier, tmp_path):
    integrity = shipped_verifier

    archive_dir = tmp_path / "archives"
    present = archive_dir / "core" / "present-1.0-1.igos.tar.gz"
    present_sha = _make_archive(present, b"this archive shipped")

    absent_rel = "core/absent-1.0-1.igos.tar.gz"
    absent_sha = hashlib.sha256(b"this archive did not ship").hexdigest()

    manifest = tmp_path / "manifest.txt"
    manifest.write_text(
        "# synthetic manifest for the installed-system gate tier\n"
        f"SHA256 (core/present-1.0-1.igos.tar.gz) = {present_sha}\n"
        f"SHA256 ({absent_rel}) = {absent_sha}\n",
        encoding="utf-8",
    )

    audit_log = tmp_path / "integrity-audit.jsonl"
    warnings, acks = [], []

    real_verify_signature = integrity.verify_manifest_signature
    integrity.verify_manifest_signature = lambda *a, **k: True
    try:
        result = integrity.verify_archives(
            archive_dir=archive_dir,
            manifest_path=manifest,
            public_key_path=tmp_path / "unused.gpg",
            warning_callback=lambda pkg, exp, act: warnings.append((pkg, exp, act)),
            ack_callback=lambda pkg: (acks.append(pkg), False)[1],
            audit_log_path=audit_log,
        )
    finally:
        integrity.verify_manifest_signature = real_verify_signature

    events = []
    if audit_log.exists():
        for line in audit_log.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))

    entry_counts = [e.get("manifest_entry_count") for e in events
                    if e.get("event") == "verify_started"]
    checked_counts = [e.get("archives_checked") for e in events
                      if e.get("event") == "verify_completed"]

    assert not result.success, (
        "\nThe install verifier reported SUCCESS while an archive the signed manifest "
        "promises was absent.\n"
        f"  manifest entries          : {entry_counts}\n"
        f"  archives actually checked : {checked_counts}\n"
        f"  the promised archive that is not there: {absent_rel}\n"
        f"  warnings raised to the frontend: {len(warnings)}\n"
        f"  override prompts shown to the user: {len(acks)}\n"
        "The verifier iterates the archives it finds. An entry with no archive is "
        "never visited, so a package that was supposed to ship and did not passes "
        "verification silently and is recorded in the audit log as a completed check.\n"
        "This is the same shape as this machine's own install record: 1145 manifest "
        "entries, 861 archives checked, verification reported successful."
    )


#: Any one of these on the terminal event tells a reader how many entries the
#: manifest promised and did not deliver. More than one spelling is accepted
#: deliberately: the gate is asserting that the gap is RECORDED, not dictating
#: which word the recorder uses for it.
GAP_FIELDS = ("expected_entries_missing", "missing_archives",
              "manifest_entries_without_archive")


def test_the_audit_record_distinguishes_an_expected_entry_from_a_checked_one(
        shipped_verifier, tmp_path):
    """The record must let a reader tell a short install from a complete one.

    Separate from the test above because it is what a fix has to produce, not just
    what the current code fails to do: the completion event needs the expected set,
    not only the count of files that happened to be present.
    """
    integrity = shipped_verifier

    archive_dir = tmp_path / "archives2"
    present = archive_dir / "core" / "present-1.0-1.igos.tar.gz"
    present_sha = _make_archive(present, b"this archive shipped")

    manifest = tmp_path / "manifest2.txt"
    manifest.write_text(
        f"SHA256 (core/present-1.0-1.igos.tar.gz) = {present_sha}\n"
        f"SHA256 (core/absent-2.0-1.igos.tar.gz) = {'0' * 64}\n",
        encoding="utf-8",
    )
    audit_log = tmp_path / "integrity-audit2.jsonl"

    real_verify_signature = integrity.verify_manifest_signature
    integrity.verify_manifest_signature = lambda *a, **k: True
    try:
        integrity.verify_archives(
            archive_dir=archive_dir, manifest_path=manifest,
            public_key_path=tmp_path / "unused.gpg",
            warning_callback=lambda *a: None, ack_callback=lambda p: False,
            audit_log_path=audit_log,
        )
    finally:
        integrity.verify_manifest_signature = real_verify_signature

    events = [json.loads(ln) for ln in
              audit_log.read_text(encoding="utf-8").splitlines() if ln.strip()]

    # Whatever the verifier decides to do about the gap, it ends the run with a
    # terminal event, and THAT event is what a reader of the install record has.
    # Both spellings are read: "verify_completed" when the run finished, "abort"
    # when it stopped because of the gap.
    terminal = [e for e in events
                if e.get("event") in ("verify_completed", "abort")]

    # RE-KEYED 2026-08-24. The earlier form asserted
    #   not (completed and missing_field)
    # over the "verify_completed" events alone. MEASURED against the current
    # tree, which aborts on a short manifest and writes no completion event at
    # all: that expression is `not ([] and ...)`, which is True, so the gate went
    # GREEN having examined no terminal record whatsoever. A gate that passes
    # because the thing it inspects is absent is the exact silent-green this tier
    # exists to end, and it was sitting inside the tier. Requiring a terminal
    # event first is what makes the second assertion capable of failing.
    assert terminal, (
        "\nThe verifier wrote no terminal event for this run, so the install "
        "record cannot be read for the outcome at all.\n"
        f"  events written: {[e.get('event') for e in events]}\n"
        "A run that ends without recording how it ended leaves a reader — "
        "including the post-install evaluation — with nothing to refuse on."
    )

    without_gap = [e for e in terminal
                   if not any(k in e for k in GAP_FIELDS)]

    assert not without_gap, (
        "\nThe verifier's terminal record cannot distinguish a complete install "
        "from a short one.\n"
        f"  terminal event as written: {without_gap}\n"
        f"  any one of these fields would carry the gap: {sorted(GAP_FIELDS)}\n"
        "It records how many archives were checked and never how many the "
        "manifest expected but did not find. A reader of the install record — "
        "including the post-install evaluation — has no field to read the gap "
        "from."
    )
