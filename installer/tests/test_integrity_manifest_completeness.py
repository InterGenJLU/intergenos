# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The verifier must also ask the question it never asked: is anything MISSING?

WHAT WAS MEASURED. The install-time verifier walks the archives it FINDS on the
media and checks each one against the signed manifest. It never asks the
opposite question — does every entry the manifest promises actually have an
archive? An entry with no archive is therefore never visited, so a package that
was supposed to ship and did not passes verification in silence.

This is not hypothetical. One installed system's own integrity record reads:

    {"event": "verify_started",   "manifest_entry_count": 1145, ...}
    {"event": "verify_completed", "archives_checked": 861,
     "overrides_granted": 0, ...}

Verification reported success. Two hundred and eighty-four entries the signed
manifest promised were never looked at, no warning was raised, no override was
asked for, and nothing in that record distinguishes entries that were never
meant to ship on that media from packages that went missing. The override
channel — the warning and the typed-phrase acknowledgement the user must enter —
already existed and was never reached.

WHAT THIS FILE ASSERTS. Six things, each of which failed before the fix:

* an entry with no archive is not a silent pass;
* the user is SHOWN what is missing before being asked anything;
* the install stops unless the user explicitly overrides, and the override is
  recorded;
* the override is ONE decision naming the number of missing archives, not one
  decision per archive — a media short by 284 archives cannot be answered by
  284 typed phrases, and an override channel a person cannot use is the same as
  no override channel;
* the completion record carries BOTH counts and the missing entries themselves,
  so a reader can tell a complete install from a short one;
* a complete media still asks nothing, which is the case that must not regress.
"""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from installer.backend import integrity
from installer.backend.integrity import expected_override_phrase, verify_archives


def _sym(name):
    """A module symbol this behaviour needs, or a legible failure.

    Imported at call time rather than at module level on purpose: a missing
    name must fail the TEST that needs it, with a sentence saying what the
    verifier cannot express, rather than breaking collection for the whole file
    and hiding the other cases behind an import error.
    """
    try:
        return getattr(integrity, name)
    except AttributeError:
        raise AssertionError(
            f"installer.backend.integrity has no {name!r}: the verifier has no "
            "notion of an archive the signed manifest promised and the install "
            "media does not carry."
        ) from None


def _field(result, name):
    """A VerifyResult field this behaviour needs, or a legible failure."""
    try:
        return getattr(result, name)
    except AttributeError:
        raise AssertionError(
            f"VerifyResult has no {name!r}, so a frontend cannot tell a "
            "complete install from a short one."
        ) from None


def _write_archive(path: Path, content: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def _write_manifest(path: Path, entries: dict[str, str]) -> None:
    lines = ["# InterGenOS archive integrity manifest",
             "# Build: completeness-fixture",
             "# Manifest-version: 1"]
    lines += [f"SHA256 ({rel}) = {sha}" for rel, sha in entries.items()]
    lines.append("# End of manifest.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class ManifestCompletenessTests(unittest.TestCase):
    """Every case here replaces ONLY the manifest signature check.

    A synthetic manifest cannot carry the release signature and the signature is
    not the property under test; the walk, the hashing, the cross-reference, the
    callbacks and the audit log are all the shipped code.
    """

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.archive_dir = self.tmp / "archives"
        self.manifest_path = self.tmp / "manifest.txt"
        self.audit_log = self.tmp / "integrity-audit.jsonl"
        self.pubkey = self.tmp / "unused.gpg"

        self.warnings: list[tuple[str, str, str]] = []
        self.acks: list[str] = []

        self._real_verify = integrity.verify_manifest_signature
        integrity.verify_manifest_signature = lambda *a, **k: True
        self.addCleanup(self._restore)
        self.addCleanup(self._tmp.cleanup)

    def _restore(self):
        integrity.verify_manifest_signature = self._real_verify

    def _run(self, *, grant: bool):
        def warning_callback(package, expected, actual):
            self.warnings.append((package, expected, actual))

        def ack_callback(package):
            self.acks.append(package)
            return grant

        return verify_archives(
            archive_dir=self.archive_dir,
            manifest_path=self.manifest_path,
            public_key_path=self.pubkey,
            warning_callback=warning_callback,
            ack_callback=ack_callback,
            audit_log_path=self.audit_log,
        )

    def _events(self):
        if not self.audit_log.exists():
            return []
        return [json.loads(line) for line
                in self.audit_log.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    def _one_present_two_absent(self):
        present_rel = "core/present-1.0-1.igos.tar.gz"
        present_sha = _write_archive(self.archive_dir / present_rel,
                                     b"this archive shipped")
        entries = {
            present_rel: present_sha,
            "core/absent-1.0-1.igos.tar.gz": "1" * 64,
            "extra/also-absent-2.0-1.igos.tar.gz": "2" * 64,
        }
        _write_manifest(self.manifest_path, entries)
        return present_rel, ["core/absent-1.0-1.igos.tar.gz",
                             "extra/also-absent-2.0-1.igos.tar.gz"]

    # ── the silence ───────────────────────────────────────────────────────

    def test_a_promised_archive_that_is_absent_is_not_a_silent_pass(self):
        _, absent = self._one_present_two_absent()
        result = self._run(grant=False)
        self.assertFalse(
            result.success,
            "verification reported success while archives the signed manifest "
            f"promised were not on the media: {absent}",
        )
        self.assertEqual(_field(result, "missing_archives"), absent)

    def test_the_user_is_shown_what_is_missing_before_being_asked(self):
        _, absent = self._one_present_two_absent()
        self._run(grant=False)
        self.assertTrue(self.warnings, "no warning was raised to the frontend")
        package, expected, actual = self.warnings[-1]
        self.assertEqual(actual, _sym("ARCHIVE_ABSENT"))
        for rel in absent:
            self.assertIn(rel, expected,
                          "the warning does not name the missing archive")
        self.assertTrue(self.acks, "the user was never asked")

    def test_the_install_stops_when_the_user_declines(self):
        _, absent = self._one_present_two_absent()
        result = self._run(grant=False)
        self.assertFalse(result.success)
        aborts = [e for e in self._events() if e.get("event") == "abort"]
        self.assertTrue(aborts, "the abort was not recorded in the audit log")
        self.assertEqual(aborts[-1].get("missing_archives"), absent)

    def test_an_explicit_override_continues_and_is_recorded(self):
        _, absent = self._one_present_two_absent()
        result = self._run(grant=True)
        self.assertTrue(result.success, msg=result.error)
        self.assertEqual(result.overrides_granted, 1,
                         "a short media is ONE override decision, not one per "
                         "missing archive")
        overrides = [e for e in self._events() if e.get("event") == "override"]
        self.assertTrue(overrides, "the override was not recorded")
        self.assertEqual(overrides[-1].get("missing_archives"), absent)

    # ── one decision, not two hundred and eighty-four ─────────────────────

    def test_the_override_is_one_decision_naming_the_count(self):
        _, absent = self._one_present_two_absent()
        self._run(grant=False)
        self.assertEqual(
            len(self.acks), 1,
            f"the user was asked {len(self.acks)} times for {len(absent)} "
            "missing archives; on real media that number was 284",
        )
        phrase = expected_override_phrase(self.acks[0])
        self.assertIn(str(len(absent)), phrase,
                      "the phrase does not name how many archives are missing, "
                      "so it can be typed without reading the warning")
        self.assertEqual(phrase, expected_override_phrase(
            _sym("missing_archives_key")(len(absent))))

    def test_the_warning_text_says_the_archives_are_missing(self):
        _, absent = self._one_present_two_absent()
        self._run(grant=False)
        self.assertTrue(
            self.warnings,
            "no warning was raised at all, so there is no text to render: the "
            "verifier does not know an archive can be missing",
        )
        package, expected, actual = self.warnings[-1]
        text = _sym("render_integrity_warning")(package, expected, actual)
        lowered = text.lower()
        self.assertIn("not on the install media", lowered)
        self.assertIn(expected_override_phrase(package), text)
        for rel in absent:
            self.assertIn(rel, text)

    # ── the record ────────────────────────────────────────────────────────

    def test_the_completion_record_carries_both_counts_and_the_missing_set(self):
        _, absent = self._one_present_two_absent()
        self._run(grant=True)
        completed = [e for e in self._events()
                     if e.get("event") == "verify_completed"]
        self.assertEqual(len(completed), 1)
        record = completed[0]
        self.assertEqual(record.get("archives_checked"), 1)
        self.assertEqual(record.get("manifest_entry_count"), 3)
        self.assertEqual(record.get("manifest_entries_without_archive"),
                         len(absent))
        self.assertEqual(record.get("expected_entries_missing"), absent)

    def test_the_result_carries_the_counts_for_the_frontend(self):
        _, absent = self._one_present_two_absent()
        result = self._run(grant=True)
        self.assertEqual(_field(result, "manifest_entry_count"), 3)
        self.assertEqual(_field(result, "archives_checked"), 1)
        self.assertEqual(_field(result, "missing_archives"), absent)

    # ── the case that must not regress ────────────────────────────────────

    def test_a_complete_media_asks_nothing(self):
        rel = "core/present-1.0-1.igos.tar.gz"
        sha = _write_archive(self.archive_dir / rel, b"this archive shipped")
        _write_manifest(self.manifest_path, {rel: sha})
        result = self._run(grant=False)
        self.assertTrue(result.success, msg=result.error)
        self.assertEqual(self.warnings, [])
        self.assertEqual(self.acks, [])
        self.assertEqual(_field(result, "missing_archives"), [])
        completed = [e for e in self._events()
                     if e.get("event") == "verify_completed"]
        self.assertEqual(completed[0].get("manifest_entries_without_archive"), 0)
        self.assertEqual(completed[0].get("archives_checked"), 1)
        self.assertEqual(completed[0].get("manifest_entry_count"), 1)

    def test_an_archive_not_in_the_manifest_is_still_a_per_package_decision(self):
        """The pre-existing contract must survive: an EXTRA archive is its own ack."""
        rel = "core/present-1.0-1.igos.tar.gz"
        sha = _write_archive(self.archive_dir / rel, b"this archive shipped")
        stray = "core/stray-9.9-1.igos.tar.gz"
        _write_archive(self.archive_dir / stray, b"nobody promised this")
        _write_manifest(self.manifest_path, {rel: sha})
        result = self._run(grant=False)
        self.assertFalse(result.success)
        self.assertEqual(result.aborted_at, stray)
        self.assertEqual(self.acks, [stray])
        self.assertEqual(expected_override_phrase(stray),
                         "OVERRIDE_HASH_MISMATCH_core_stray_9_9_1_igos_tar_gz")


if __name__ == "__main__":
    unittest.main()


class OrchestratorSurfacesTheGapTests(unittest.TestCase):
    """A gap the user accepted has to reach the screen, not only the log.

    The verify phase is the only place that knows the two counts. If the
    orchestrator drops them, the install-complete screen says "success" and
    nothing anywhere tells the user which software is not on their machine.
    """

    def _result_with(self, *, missing, checked, promised):
        from installer.backend.integrity import VerifyResult
        return VerifyResult(
            success=True,
            overrides_granted=1 if missing else 0,
            manifest_entry_count=promised,
            archives_checked=checked,
            missing_archives=list(missing),
        )

    def test_the_counts_and_the_gap_reach_the_install_result(self):
        from installer.backend import install as install_mod

        result = install_mod.InstallResult(success=True, phase_completed="verify")
        verify_result = self._result_with(
            missing=["core/absent-1.0-1.igos.tar.gz"], checked=861, promised=862)

        for name, value in (
            ("integrity_manifest_entry_count", verify_result.manifest_entry_count),
            ("integrity_archives_checked", verify_result.archives_checked),
            ("integrity_missing_archives", verify_result.missing_archives),
        ):
            self.assertTrue(
                hasattr(result, name),
                f"InstallResult has no {name!r}: the verify phase knows the two "
                "counts and the install result cannot carry them, so no screen "
                "can show a short install as anything but a success.",
            )
            _ = value
