"""Tests for installer/backend/integrity.py — install-time integrity verification.

Covers (per design doc §9.1):
- Manifest signature validation: valid sig passes, bad sig hard-fails (no override path).
- SHA-mismatch detection: synthetic mismatched archive triggers warning_callback.
- Acknowledgment phrase: correct phrase grants override; any other input aborts.
- Per-mismatch isolation: 3 mismatches require 3 separate acks; one wrong aborts all.
- Audit log: hash chain holds across multiple entries; deleted middle entry breaks chain.
- Genesis entry: log creation when absent.
- Target log copy: cleanup phase places log at target's /var/log.

Signature-verification tests mock subprocess.run rather than exercising real
GPG — keeps test runtime tight and avoids GPG availability assumptions in CI.
A separate integration test (out of scope for this unit suite) exercises the
real GPG path end-to-end.
"""

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from installer.backend.integrity import (
    INTEGRITY_WARNING_TEMPLATE,
    OVERRIDE_PHRASE_FORMAT,
    VerifyResult,
    _audit_log_append,
    _hash_chain_entry,
    _last_chain_hash,
    copy_audit_log_to_target,
    expected_override_phrase,
    normalize_package_name,
    parse_manifest,
    sha256_file,
    verify_archives,
    verify_manifest_signature,
)


def _write_archive(path: Path, content: bytes) -> str:
    """Create a fake .igos.tar.gz at path with given content; return its sha256."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def _write_manifest(path: Path, entries: dict[str, str], header: bool = True) -> None:
    """Write a BSD-style manifest at path. entries maps relative-path → sha256."""
    lines = []
    if header:
        lines.extend([
            "# InterGenOS archive integrity manifest",
            "# Build: test-fixture",
            "# Manifest-version: 1",
        ])
    for rel, sha in entries.items():
        lines.append(f"SHA256 ({rel}) = {sha}")
    if header:
        lines.append("# End of manifest.")
    path.write_text("\n".join(lines) + "\n")


class TestNameNormalization(unittest.TestCase):
    def test_alnum_unchanged(self):
        self.assertEqual(normalize_package_name("glibc"), "glibc")

    def test_dash_to_underscore(self):
        self.assertEqual(normalize_package_name("gnome-shell"), "gnome_shell")

    def test_plus_to_underscore(self):
        self.assertEqual(normalize_package_name("gtk+3"), "gtk_3")

    def test_slash_to_underscore(self):
        self.assertEqual(normalize_package_name("core/glibc"), "core_glibc")

    def test_runs_collapse(self):
        # "a---b" collapses to "a_b" — multiple non-alnum chars become one underscore
        self.assertEqual(normalize_package_name("a---b"), "a_b")

    def test_override_phrase_uses_normalized(self):
        phrase = expected_override_phrase("gnome-shell")
        self.assertEqual(phrase, "OVERRIDE_HASH_MISMATCH_gnome_shell")


class TestShaFile(unittest.TestCase):
    def test_known_hash_of_empty(self):
        # Test vector: sha256(b"") — well-known constant any sha256sum tool produces.
        EXPECTED_EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"  # sha256
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"")
            p = Path(tmp.name)
        try:
            self.assertEqual(sha256_file(p), EXPECTED_EMPTY_SHA256)
        finally:
            p.unlink()

    def test_known_hash_of_text(self):
        # Test vector: sha256(b"hello\n") — well-known constant any sha256sum tool produces.
        EXPECTED_HELLO_SHA256 = "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"  # sha256
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"hello\n")
            p = Path(tmp.name)
        try:
            self.assertEqual(sha256_file(p), EXPECTED_HELLO_SHA256)
        finally:
            p.unlink()


class TestManifestParse(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_parses_simple(self):
        m = self.tmp / "manifest.txt"
        _write_manifest(m, {"a/b.tar.gz": "a" * 64, "c/d.tar.gz": "b" * 64})
        parsed = parse_manifest(m)
        self.assertEqual(parsed, {"a/b.tar.gz": "a" * 64, "c/d.tar.gz": "b" * 64})

    def test_ignores_signature_block(self):
        m = self.tmp / "manifest.txt"
        m.write_text(
            "# header\n"
            f"SHA256 (foo.tar.gz) = {'a' * 64}\n"
            "-----BEGIN PGP SIGNATURE-----\n"
            "garbage that should be skipped\n"
            "-----END PGP SIGNATURE-----\n"
        )
        parsed = parse_manifest(m)
        self.assertEqual(parsed, {"foo.tar.gz": "a" * 64})

    def test_handles_clearsigned_header(self):
        m = self.tmp / "manifest.txt"
        m.write_text(
            "-----BEGIN PGP SIGNED MESSAGE-----\n"
            "Hash: SHA256\n"
            "\n"
            "# header\n"
            f"SHA256 (x.tar.gz) = {'1' * 64}\n"
            "-----BEGIN PGP SIGNATURE-----\n"
            "sig data\n"
            "-----END PGP SIGNATURE-----\n"
        )
        parsed = parse_manifest(m)
        self.assertEqual(parsed, {"x.tar.gz": "1" * 64})

    def test_lowercases_sha(self):
        m = self.tmp / "manifest.txt"
        # capital hex
        m.write_text(f"SHA256 (foo.tar.gz) = {'A' * 64}\n")
        parsed = parse_manifest(m)
        self.assertEqual(parsed["foo.tar.gz"], "a" * 64)

    def test_malformed_line_raises(self):
        m = self.tmp / "manifest.txt"
        m.write_text("this is not a sha256 line\n")
        with self.assertRaises(ValueError):
            parse_manifest(m)


class TestSignatureVerification(unittest.TestCase):
    """verify_manifest_signature() shells out to gpg; we mock subprocess."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.manifest = self.tmp / "manifest.txt"
        self.manifest.write_text("# fake manifest\n")
        self.pubkey = self.tmp / "pubkey.gpg"
        self.pubkey.write_bytes(b"fake-keyring")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    @patch("installer.backend.integrity.subprocess.run")
    def test_valid_signature_returns_true(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        self.assertTrue(verify_manifest_signature(self.manifest, self.pubkey))
        # Ensure we passed --no-default-keyring + --keyring to bind verification
        # exclusively to the supplied key.
        args = mock_run.call_args[0][0]
        self.assertIn("--no-default-keyring", args)
        self.assertIn("--keyring", args)
        self.assertIn(str(self.pubkey), args)

    @patch("installer.backend.integrity.subprocess.run")
    def test_bad_signature_returns_false(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        self.assertFalse(verify_manifest_signature(self.manifest, self.pubkey))

    @patch("installer.backend.integrity.subprocess.run")
    def test_gpg_missing_returns_false(self, mock_run):
        mock_run.side_effect = FileNotFoundError("gpg not on PATH")
        self.assertFalse(verify_manifest_signature(self.manifest, self.pubkey))

    @patch("installer.backend.integrity.subprocess.run")
    def test_timeout_returns_false(self, mock_run):
        import subprocess as sp
        mock_run.side_effect = sp.TimeoutExpired(cmd=["gpg"], timeout=30)
        self.assertFalse(verify_manifest_signature(self.manifest, self.pubkey))


class TestAuditLog(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.log = self.tmp / "audit.log"

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_genesis_when_absent(self):
        self.assertEqual(_last_chain_hash(self.log), "GENESIS")

    def test_first_entry_uses_genesis_prev(self):
        _audit_log_append(self.log, {"event": "verify_started", "x": 1})
        with open(self.log) as f:
            entry = json.loads(f.readline())
        self.assertEqual(entry["prev"], "GENESIS")
        self.assertEqual(entry["event"], "verify_started")
        self.assertEqual(entry["v"], 1)
        self.assertIn("ts", entry)
        self.assertIn("entry_sha256", entry)

    def test_chain_links_correctly(self):
        _audit_log_append(self.log, {"event": "a"})
        _audit_log_append(self.log, {"event": "b"})
        _audit_log_append(self.log, {"event": "c"})
        with open(self.log) as f:
            entries = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0]["prev"], "GENESIS")
        self.assertEqual(entries[1]["prev"], entries[0]["entry_sha256"])
        self.assertEqual(entries[2]["prev"], entries[1]["entry_sha256"])

    def test_hash_chain_detects_silent_deletion(self):
        # Write 3 entries, delete the middle one, verify chain breaks.
        _audit_log_append(self.log, {"event": "a"})
        _audit_log_append(self.log, {"event": "b"})
        _audit_log_append(self.log, {"event": "c"})
        with open(self.log) as f:
            entries = [json.loads(line) for line in f if line.strip()]
        # Tamper: delete entry [1].
        with open(self.log, "w") as f:
            f.write(json.dumps(entries[0], sort_keys=True) + "\n")
            f.write(json.dumps(entries[2], sort_keys=True) + "\n")
        # Now re-read and verify entries[2].prev no longer matches the
        # entry preceding it on disk (which is entries[0]).
        with open(self.log) as f:
            tampered = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(tampered), 2)
        # entries[2].prev still claims to chain off entries[1] (the deleted
        # one), so it does NOT chain off the new predecessor entries[0].
        # That's the detection: prev != predecessor's entry_sha256.
        self.assertNotEqual(tampered[1]["prev"], tampered[0]["entry_sha256"])

    def test_recompute_hash_matches_stored(self):
        _audit_log_append(self.log, {"event": "x", "package": "foo"})
        with open(self.log) as f:
            entry = json.loads(f.readline())
        # Reconstruct the hash from the entry's business fields + prev,
        # confirm it matches the stored entry_sha256.
        body = {k: v for k, v in entry.items() if k != "entry_sha256"}
        recomputed = _hash_chain_entry(body, entry["prev"])
        self.assertEqual(recomputed, entry["entry_sha256"])


class TestVerifyArchivesHappyPath(unittest.TestCase):
    """All archives match manifest → success, no callback invocations."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.archive_dir = self.tmp / "archives"
        self.archive_dir.mkdir()
        self.audit_log = self.tmp / "audit.log"
        self.manifest_path = self.tmp / "manifest.txt"
        self.pubkey_path = self.tmp / "pubkey.gpg"
        self.pubkey_path.write_bytes(b"fake")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    @patch("installer.backend.integrity.verify_manifest_signature")
    def test_all_match_returns_success(self, mock_sig):
        mock_sig.return_value = True
        sha_a = _write_archive(self.archive_dir / "core/a.igos.tar.gz", b"alpha")
        sha_b = _write_archive(self.archive_dir / "core/b.igos.tar.gz", b"bravo")
        _write_manifest(self.manifest_path, {
            "core/a.igos.tar.gz": sha_a,
            "core/b.igos.tar.gz": sha_b,
        })

        warned = []
        acked = []
        result = verify_archives(
            archive_dir=self.archive_dir,
            manifest_path=self.manifest_path,
            public_key_path=self.pubkey_path,
            warning_callback=lambda *a: warned.append(a),
            ack_callback=lambda *a: (acked.append(a), True)[1],
            audit_log_path=self.audit_log,
        )
        self.assertTrue(result.success)
        self.assertEqual(result.overrides_granted, 0)
        self.assertIsNone(result.aborted_at)
        self.assertEqual(warned, [])
        self.assertEqual(acked, [])
        # Audit log should have verify_started + verify_completed (2 entries).
        with open(self.audit_log) as f:
            entries = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["event"], "verify_started")
        self.assertEqual(entries[1]["event"], "verify_completed")


class TestVerifyArchivesMismatchOverride(unittest.TestCase):
    """Mismatched archive + user grants override → success with override count."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.archive_dir = self.tmp / "archives"
        self.archive_dir.mkdir()
        self.audit_log = self.tmp / "audit.log"
        self.manifest_path = self.tmp / "manifest.txt"
        self.pubkey_path = self.tmp / "pubkey.gpg"
        self.pubkey_path.write_bytes(b"fake")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    @patch("installer.backend.integrity.verify_manifest_signature")
    def test_single_mismatch_overridden(self, mock_sig):
        mock_sig.return_value = True
        # Real archive content sha != manifest claim.
        actual_sha = _write_archive(
            self.archive_dir / "core/glibc.igos.tar.gz", b"actual-content"
        )
        claimed_sha = "f" * 64
        _write_manifest(self.manifest_path, {"core/glibc.igos.tar.gz": claimed_sha})

        warnings_received = []
        result = verify_archives(
            archive_dir=self.archive_dir,
            manifest_path=self.manifest_path,
            public_key_path=self.pubkey_path,
            warning_callback=lambda name, exp, act: warnings_received.append(
                (name, exp, act)
            ),
            ack_callback=lambda name: True,  # user grants override
            audit_log_path=self.audit_log,
        )
        self.assertTrue(result.success)
        self.assertEqual(result.overrides_granted, 1)
        self.assertEqual(len(warnings_received), 1)
        name, exp, act = warnings_received[0]
        self.assertEqual(name, "core/glibc.igos.tar.gz")
        self.assertEqual(exp, claimed_sha)
        self.assertEqual(act, actual_sha)
        # Audit log: verify_started + override + verify_completed = 3.
        with open(self.audit_log) as f:
            entries = [json.loads(line) for line in f if line.strip()]
        events = [e["event"] for e in entries]
        self.assertEqual(events, ["verify_started", "override", "verify_completed"])

    @patch("installer.backend.integrity.verify_manifest_signature")
    def test_mismatch_declined_aborts(self, mock_sig):
        mock_sig.return_value = True
        _write_archive(self.archive_dir / "core/foo.igos.tar.gz", b"bad")
        _write_manifest(self.manifest_path, {"core/foo.igos.tar.gz": "0" * 64})
        result = verify_archives(
            archive_dir=self.archive_dir,
            manifest_path=self.manifest_path,
            public_key_path=self.pubkey_path,
            warning_callback=lambda *a: None,
            ack_callback=lambda name: False,  # user declines
            audit_log_path=self.audit_log,
        )
        self.assertFalse(result.success)
        self.assertEqual(result.aborted_at, "core/foo.igos.tar.gz")
        self.assertEqual(result.overrides_granted, 0)
        with open(self.audit_log) as f:
            entries = [json.loads(line) for line in f if line.strip()]
        events = [e["event"] for e in entries]
        self.assertEqual(events, ["verify_started", "abort"])


class TestVerifyArchivesPerMismatchIsolation(unittest.TestCase):
    """Per design doc §6.4: multiple mismatches require multiple separate acks.

    No bulk override exists. Three mismatched archives → ack_callback invoked
    three times. One declined → abort even if prior two were granted.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.archive_dir = self.tmp / "archives"
        self.archive_dir.mkdir()
        self.audit_log = self.tmp / "audit.log"
        self.manifest_path = self.tmp / "manifest.txt"
        self.pubkey_path = self.tmp / "pubkey.gpg"
        self.pubkey_path.write_bytes(b"fake")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    @patch("installer.backend.integrity.verify_manifest_signature")
    def test_three_mismatches_require_three_acks(self, mock_sig):
        mock_sig.return_value = True
        for n in ("a", "b", "c"):
            _write_archive(self.archive_dir / f"core/{n}.igos.tar.gz", n.encode())
        _write_manifest(self.manifest_path, {
            f"core/{n}.igos.tar.gz": "0" * 64 for n in ("a", "b", "c")
        })

        ack_invocations = []
        result = verify_archives(
            archive_dir=self.archive_dir,
            manifest_path=self.manifest_path,
            public_key_path=self.pubkey_path,
            warning_callback=lambda *a: None,
            ack_callback=lambda name: (ack_invocations.append(name), True)[1],
            audit_log_path=self.audit_log,
        )
        self.assertTrue(result.success)
        self.assertEqual(result.overrides_granted, 3)
        self.assertEqual(len(ack_invocations), 3)
        # Each invocation got a different package name (no bulk).
        self.assertEqual(set(ack_invocations), {
            "core/a.igos.tar.gz",
            "core/b.igos.tar.gz",
            "core/c.igos.tar.gz",
        })

    @patch("installer.backend.integrity.verify_manifest_signature")
    def test_one_decline_aborts_even_after_prior_grants(self, mock_sig):
        mock_sig.return_value = True
        for n in ("a", "b", "c"):
            _write_archive(self.archive_dir / f"core/{n}.igos.tar.gz", n.encode())
        _write_manifest(self.manifest_path, {
            f"core/{n}.igos.tar.gz": "0" * 64 for n in ("a", "b", "c")
        })

        # User grants first two, declines third.
        decisions = {
            "core/a.igos.tar.gz": True,
            "core/b.igos.tar.gz": True,
            "core/c.igos.tar.gz": False,
        }
        result = verify_archives(
            archive_dir=self.archive_dir,
            manifest_path=self.manifest_path,
            public_key_path=self.pubkey_path,
            warning_callback=lambda *a: None,
            ack_callback=lambda name: decisions[name],
            audit_log_path=self.audit_log,
        )
        self.assertFalse(result.success)
        self.assertEqual(result.overrides_granted, 2)
        self.assertEqual(result.aborted_at, "core/c.igos.tar.gz")
        with open(self.audit_log) as f:
            entries = [json.loads(line) for line in f if line.strip()]
        events = [e["event"] for e in entries]
        self.assertEqual(events, [
            "verify_started",
            "override",  # a granted
            "override",  # b granted
            "abort",     # c declined
        ])


class TestVerifyArchivesSignatureFailure(unittest.TestCase):
    """Signature failure is non-overridable — no callback invoked, no audit log."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.archive_dir = self.tmp / "archives"
        self.archive_dir.mkdir()
        self.audit_log = self.tmp / "audit.log"
        self.manifest_path = self.tmp / "manifest.txt"
        self.manifest_path.write_text("# fake\n")
        self.pubkey_path = self.tmp / "pubkey.gpg"
        self.pubkey_path.write_bytes(b"fake")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    @patch("installer.backend.integrity.verify_manifest_signature")
    def test_bad_sig_returns_error_no_callbacks(self, mock_sig):
        mock_sig.return_value = False
        warned = []
        acked = []
        result = verify_archives(
            archive_dir=self.archive_dir,
            manifest_path=self.manifest_path,
            public_key_path=self.pubkey_path,
            warning_callback=lambda *a: warned.append(a),
            ack_callback=lambda *a: (acked.append(a), True)[1],
            audit_log_path=self.audit_log,
        )
        self.assertFalse(result.success)
        self.assertIn("signature", result.error.lower())
        self.assertEqual(warned, [])
        self.assertEqual(acked, [])
        # Audit log NOT written for signature failure (no user act to record).
        self.assertFalse(self.audit_log.exists())


class TestVerifyArchivesMissingFromManifest(unittest.TestCase):
    """Archive in archive_dir but not in manifest → treated as mismatch."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.archive_dir = self.tmp / "archives"
        self.archive_dir.mkdir()
        self.audit_log = self.tmp / "audit.log"
        self.manifest_path = self.tmp / "manifest.txt"
        self.pubkey_path = self.tmp / "pubkey.gpg"
        self.pubkey_path.write_bytes(b"fake")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    @patch("installer.backend.integrity.verify_manifest_signature")
    def test_extra_archive_triggers_mismatch(self, mock_sig):
        mock_sig.return_value = True
        # Empty manifest (no entries) but archive present in archive_dir.
        _write_archive(self.archive_dir / "extra/sneaky.igos.tar.gz", b"???")
        _write_manifest(self.manifest_path, {})

        warned = []
        result = verify_archives(
            archive_dir=self.archive_dir,
            manifest_path=self.manifest_path,
            public_key_path=self.pubkey_path,
            warning_callback=lambda name, exp, act: warned.append((name, exp, act)),
            ack_callback=lambda name: False,  # user declines extra archive
            audit_log_path=self.audit_log,
        )
        self.assertFalse(result.success)
        self.assertEqual(result.aborted_at, "extra/sneaky.igos.tar.gz")
        self.assertEqual(len(warned), 1)
        name, exp, act = warned[0]
        self.assertEqual(name, "extra/sneaky.igos.tar.gz")
        self.assertEqual(exp, "<not in manifest>")


class TestCopyAuditLog(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_copies_log_to_target_var_log(self):
        log = self.tmp / "audit.log"
        log.write_text('{"event":"verify_started"}\n')
        target = self.tmp / "target"
        target.mkdir()
        copy_audit_log_to_target(log, target)
        copied = target / "var" / "log" / "igos-integrity-override.log"
        self.assertTrue(copied.exists())
        self.assertEqual(copied.read_text(), '{"event":"verify_started"}\n')

    def test_no_log_to_copy_is_noop(self):
        log = self.tmp / "missing.log"
        target = self.tmp / "target"
        target.mkdir()
        copy_audit_log_to_target(log, target)  # should not raise
        copied = target / "var" / "log" / "igos-integrity-override.log"
        self.assertFalse(copied.exists())

    def test_creates_parent_dirs(self):
        log = self.tmp / "audit.log"
        log.write_text("data\n")
        target = self.tmp / "fresh-target"
        target.mkdir()
        # No /var or /var/log exists yet at target.
        copy_audit_log_to_target(log, target)
        self.assertTrue((target / "var" / "log" / "igos-integrity-override.log").exists())


class TestWarningTemplate(unittest.TestCase):
    """The warning template must mention the master fingerprint and security
    contact (these are the user's only independent verification channels).
    """

    def test_master_fingerprint_present(self):
        # As published in docs/signing-key.md
        self.assertIn("5597 A3E0 587B 2530 06D0  DD7B 8C50 8261 8208 3050",
                      INTEGRITY_WARNING_TEMPLATE)

    def test_security_contact_present(self):
        self.assertIn("security@intergenstudios.com", INTEGRITY_WARNING_TEMPLATE)

    def test_audit_log_path_present(self):
        self.assertIn("/var/log/igos-integrity-override.log", INTEGRITY_WARNING_TEMPLATE)

    def test_canonical_publication_pointer_present(self):
        self.assertIn("intergenstudios.com/signing-key", INTEGRITY_WARNING_TEMPLATE)
        self.assertIn("docs/signing-key.md", INTEGRITY_WARNING_TEMPLATE)


if __name__ == "__main__":
    unittest.main()
