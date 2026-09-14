# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A corrective same-version republish is admitted only through a signed record.

The checker (``scripts/check-corrective-record.py``) is driven directly with an
ephemeral release key so its accept path and every refusal are exercised for
real; the publisher (``scripts/publish-repo.sh``) is driven as in the signing-
hold cases (only ``ssh`` stood in) to prove the wiring: the plain advancement
gate still refuses a same-version byte change, the option is refused with
``--skip-sign`` and with a relative or unsigned record, and a record signed by
any key other than the pinned release key is refused by the real publisher
before anything is generated or signed.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from tests.preflight import test_publish_sign_hold as _sign_hold

RELEASE_FINGERPRINT = _sign_hold.FINGERPRINT

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "scripts" / "check-corrective-record.py"
PUBLISH = REPO_ROOT / "scripts" / "publish-repo.sh"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_archive(path: Path, name: str, version: str, release: int, payload: bytes) -> bytes:
    """Write a package archive whose bytes depend on payload; return its bytes."""
    info_data = (f"pkgname = {name}\npkgver = {version}\npkgrel = {release}\n"
                 "pkgdesc = fixture\ntier = extra\nlicense = MIT\n").encode()
    buffer = BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        info = tarfile.TarInfo("./.PKGINFO")
        info.size = len(info_data)
        info.mtime = 0
        tar.addfile(info, BytesIO(info_data))
        member = tarfile.TarInfo(f"./usr/share/{name}/payload")
        member.size = len(payload)
        member.mtime = 0
        tar.addfile(member, BytesIO(payload))
    path.write_bytes(buffer.getvalue())
    return buffer.getvalue()


def write_index(path: Path, rows: dict) -> None:
    doc = {"version": 1, "arch": "x86_64", "package_count": len(rows), "packages": rows}
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(doc, handle)


class EphemeralKey:
    """One throwaway signing key for the whole module."""

    def __init__(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="corrective-record-key.")
        self.home = Path(self.tmp.name) / "gnupg"
        self.home.mkdir(mode=0o700)
        subprocess.run(
            ["gpg", "--homedir", str(self.home), "--batch", "--passphrase", "",
             "--quick-gen-key", "corrective-record-test@example.invalid", "ed25519", "sign", "0"],
            check=True, capture_output=True, timeout=120)
        listing = subprocess.run(
            ["gpg", "--homedir", str(self.home), "--list-keys", "--with-colons"],
            check=True, capture_output=True, text=True, timeout=60).stdout
        self.fingerprint = next(line.split(":")[9] for line in listing.splitlines()
                                if line.startswith("fpr:"))

    def sign(self, record: Path) -> Path:
        signature = record.with_name(record.name + ".asc")
        subprocess.run(
            ["gpg", "--homedir", str(self.home), "--batch", "--yes", "--armor",
             "--detach-sign", "-u", self.fingerprint, "-o", str(signature), str(record)],
            check=True, capture_output=True, timeout=60)
        return signature

    def close(self) -> None:
        subprocess.run(["gpgconf", "--homedir", str(self.home), "--kill", "all"],
                       capture_output=True, timeout=60, check=False)
        self.tmp.cleanup()


KEY: EphemeralKey | None = None


def setUpModule() -> None:
    global KEY
    KEY = EphemeralKey()


def tearDownModule() -> None:
    if KEY is not None:
        KEY.close()


class CorrectiveRecordCheckerTest(unittest.TestCase):
    """The checker against a staged set with one same-version replacement."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="corrective-record-")
        self.root = Path(self._tmp.name)
        self.archives = self.root / "archives"
        self.archives.mkdir()
        # demo: same version-release, different bytes (the correction)
        self.served_demo = sha256_bytes(make_archive(self.root / "demo-served.igos.tar.gz", "demo", "2.0", 1, b"wrong bytes"))
        self.staged_demo = sha256_bytes(make_archive(self.archives / "demo-2.0.igos.tar.gz", "demo", "2.0", 1, b"corrected bytes"))
        # other: advances normally 1.0-1 -> 1.0-2 (not an exception)
        served_other = sha256_bytes(make_archive(self.root / "other-served.igos.tar.gz", "other", "1.0", 1, b"a"))
        make_archive(self.archives / "other-1.0.igos.tar.gz", "other", "1.0", 2, b"b")
        # same: unchanged bytes
        same_bytes = make_archive(self.archives / "same-3.1.igos.tar.gz", "same", "3.1", 4, b"same")
        self.served_index = self.root / "served.db"
        write_index(self.served_index, {
            "demo": {"version": "2.0", "release": 1, "sha256": self.served_demo, "filename": "demo-2.0.igos.tar.gz"},
            "other": {"version": "1.0", "release": 1, "sha256": served_other, "filename": "other-1.0.igos.tar.gz"},
            "same": {"version": "3.1", "release": 4, "sha256": sha256_bytes(same_bytes), "filename": "same-3.1.igos.tar.gz"},
        })
        self.served_digest = sha256_bytes(self.served_index.read_bytes())
        self.record = self.root / "record.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def exact_record(self) -> dict:
        return {
            "schema": 1,
            "incident": "a-HUB-04",
            "reason": "the served archive carried the wrong source bytes under a correct .PKGINFO",
            "served_index_sha256": self.served_digest,
            "exceptions": [{
                "name": "demo", "version": "2.0", "release": 1,
                "served_sha256": self.served_demo,
                "replacement_sha256": self.staged_demo,
            }],
        }

    def write_signed(self, doc: dict) -> None:
        self.record.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        assert KEY is not None
        KEY.sign(self.record)

    def run_checker(self, fingerprint: str | None = None, generated: Path | None = None,
                    record: Path | None = None) -> subprocess.CompletedProcess:
        assert KEY is not None
        command = [sys.executable, str(CHECKER),
                   "--record", str(record or self.record),
                   "--served-index", str(self.served_index),
                   "--archive-dir", str(self.archives),
                   "--fingerprint", fingerprint or KEY.fingerprint]
        if generated is not None:
            command += ["--generated-index", str(generated)]
        env = os.environ.copy()
        env["GNUPGHOME"] = str(KEY.home)
        return subprocess.run(command, cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120)

    def assert_refused(self, proc: subprocess.CompletedProcess, fragment: str) -> None:
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("REFUSED:", proc.stderr)
        self.assertIn(fragment, proc.stderr)

    def test_exact_signed_record_is_accepted_and_states_the_consequence(self):
        self.write_signed(self.exact_record())
        proc = self.run_checker()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ACCEPTED: incident a-HUB-04, 1 same-version replacement(s)", proc.stdout)
        self.assertIn(f"served index sha256 {self.served_digest}", proc.stdout)
        self.assertIn("demo 2.0-1:", proc.stdout)
        self.assertIn("will NOT receive the", proc.stdout)
        self.assertNotIn("other", proc.stdout.split("CONSEQUENCE")[0].split("reason:")[1])

    def test_generated_index_rows_are_bound_to_the_replacement(self):
        self.write_signed(self.exact_record())
        sys.path.insert(0, str(REPO_ROOT))
        from pkm.repo import generate_index
        generated = Path(generate_index(self.archives, arch="x86_64", output=self.root / "generated.db"))
        proc = self.run_checker(generated=generated)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("generated index rows carry the replacement digests", proc.stdout)
        # A generated index whose demo row does not carry the replacement is refused.
        with gzip.open(generated, "rt", encoding="utf-8") as handle:
            doc = json.load(handle)
        doc["packages"]["demo"]["sha256"] = self.served_demo
        tampered = self.root / "tampered.db"
        with gzip.open(tampered, "wt", encoding="utf-8") as handle:
            json.dump(doc, handle)
        self.assert_refused(self.run_checker(generated=tampered), "generated index row digest")

    def test_signature_absent_tampered_or_from_another_key_is_refused(self):
        doc = self.exact_record()
        self.record.write_text(json.dumps(doc) + "\n", encoding="utf-8")
        self.assert_refused(self.run_checker(), "record signature is absent")
        self.write_signed(doc)
        self.assert_refused(self.run_checker(fingerprint=RELEASE_FINGERPRINT), "is not the pinned release key")
        # tamper after signing: the reason text changes, the signature no longer verifies
        doc["reason"] = "a different reason"
        self.record.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        self.assert_refused(self.run_checker(), "record signature does not verify")

    def test_served_index_digest_mismatch_is_refused(self):
        doc = self.exact_record()
        doc["served_index_sha256"] = "0" * 64
        self.write_signed(doc)
        self.assert_refused(self.run_checker(), "binds a different served state")

    def test_set_equality_is_required(self):
        # omits demo, names the normally-advancing package instead
        doc = self.exact_record()
        doc["exceptions"][0]["name"] = "other"
        self.write_signed(doc)
        proc = self.run_checker()
        self.assert_refused(proc, "omits non-monotonic staged package(s): demo")
        # names demo AND the normally-advancing package
        doc = self.exact_record()
        doc["exceptions"].append({"name": "other", "version": "1.0", "release": 2,
                                  "served_sha256": "1" * 64, "replacement_sha256": "2" * 64})
        self.write_signed(doc)
        self.assert_refused(self.run_checker(), "not non-monotonic staged changes: other")
        # names a package that is not staged at all
        doc = self.exact_record()
        doc["exceptions"].append({"name": "ghost", "version": "1.0", "release": 1,
                                  "served_sha256": "1" * 64, "replacement_sha256": "2" * 64})
        self.write_signed(doc)
        self.assert_refused(self.run_checker(), "not non-monotonic staged changes: ghost")

    def test_every_recorded_field_must_match_the_derivation(self):
        for field, value, fragment in (
            ("replacement_sha256", "f" * 64, "is not the staged archive's"),
            ("served_sha256", "e" * 64, "is not the served index row's"),
            ("release", 2, "record says 2.0-2, staged is 2.0-1"),
            ("version", "2.1", "record says 2.1-1, staged is 2.0-1"),
        ):
            doc = self.exact_record()
            doc["exceptions"][0][field] = value
            self.write_signed(doc)
            self.assert_refused(self.run_checker(), fragment)

    def test_a_record_with_nothing_to_authorize_is_refused(self):
        # make the staged demo byte-identical to the served entry
        make_archive(self.archives / "demo-2.0.igos.tar.gz", "demo", "2.0", 1, b"wrong bytes")
        self.write_signed(self.exact_record())
        self.assert_refused(self.run_checker(), "nothing to authorize")

    def test_schema_is_strict(self):
        cases = []
        doc = self.exact_record(); doc["note"] = "extra"; cases.append((doc, "record keys must be exactly"))
        doc = self.exact_record(); doc["schema"] = 2; cases.append((doc, "record schema must be 1"))
        doc = self.exact_record(); doc["reason"] = "x" * 501; cases.append((doc, "at most 500 characters"))
        doc = self.exact_record(); doc["reason"] = "two\nlines"; cases.append((doc, "one non-empty line"))
        doc = self.exact_record(); doc["incident"] = "bad id!"; cases.append((doc, "record incident must be"))
        doc = self.exact_record(); doc["exceptions"] = []; cases.append((doc, "non-empty list"))
        doc = self.exact_record(); doc["exceptions"][0]["replacement_sha256"] = self.served_demo
        cases.append((doc, "nothing is corrected"))
        doc = self.exact_record(); doc["exceptions"][0]["release"] = "1"; cases.append((doc, "positive integer"))
        doc = self.exact_record(); doc["exceptions"][0]["served_sha256"] = self.served_demo.upper()
        cases.append((doc, "lower-case sha256"))
        for doc, fragment in cases:
            with self.subTest(fragment=fragment):
                self.write_signed(doc)
                self.assert_refused(self.run_checker(), fragment)

    def test_relative_record_path_is_refused(self):
        self.write_signed(self.exact_record())
        proc = self.run_checker(record=Path("record.json"))
        self.assert_refused(proc, "record path must be absolute")


class CorrectiveRecordPublisherTest(_sign_hold.PublishSignHoldTest):
    """The real publisher: the plain gate, the option's refusals, the pinned key."""

    def setUp(self) -> None:
        super().setUp()
        # Replace the fixture's staged demo (2.0-1) with a same-version-different-bytes
        # correction of the served demo, so the advancement gate has something to refuse.
        self.live_index = self.remote / "InterGenOS.db"
        served = make_archive(self.root / "demo-served.igos.tar.gz", "demo", "2.0", 1, b"wrong bytes")
        staged = make_archive(self.archives / "demo-2.0.igos.tar.gz", "demo", "2.0", 1, b"corrected bytes")
        self.manifest.write_text(f"{sha256_bytes(staged)}  demo-2.0.igos.tar.gz\n", encoding="utf-8")
        write_index(self.live_index, {"demo": {"version": "2.0", "release": 1,
                                               "sha256": sha256_bytes(served),
                                               "filename": "demo-2.0.igos.tar.gz"}})
        self.record = self.root / "record.json"
        self.record.write_text(json.dumps({
            "schema": 1, "incident": "fixture-01",
            "reason": "fixture correction",
            "served_index_sha256": sha256_bytes(self.live_index.read_bytes()),
            "exceptions": [{"name": "demo", "version": "2.0", "release": 1,
                            "served_sha256": sha256_bytes(served),
                            "replacement_sha256": sha256_bytes(staged)}],
        }, indent=1) + "\n", encoding="utf-8")

    def _publish(self, extra: list[str], env_extra: dict | None = None) -> subprocess.CompletedProcess:
        command = self._command() + extra
        env = self._environment()
        if env_extra:
            env.update(env_extra)
        return subprocess.run(command, cwd=REPO_ROOT, env=env, capture_output=True,
                              text=True, timeout=120)

    def test_the_plain_gate_still_refuses_a_same_version_byte_change(self):
        proc = self._publish([])
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 1, out)
        self.assertIn("do NOT strictly advance", out)
        self.assertIn("demo: staged 2.0-1 is not newer than live 2.0-1", out)
        self.assertNotIn("SIGNING HOLD", out)

    def test_the_option_is_refused_with_skip_sign_and_with_bad_paths(self):
        assert KEY is not None
        KEY.sign(self.record)
        proc = self._publish(["--corrective-republish-record", str(self.record), "--skip-sign"])
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 1, out)
        self.assertIn("cannot be combined with --skip-sign", out)
        self.assertNotIn("Checking SSH connectivity", out)
        proc = self._publish(["--corrective-republish-record", "record.json"])
        self.assertIn("must be an absolute path", proc.stdout + proc.stderr)
        self.assertEqual(proc.returncode, 1)
        (self.root / "record.json.asc").unlink()
        proc = self._publish(["--corrective-republish-record", str(self.record)])
        self.assertIn("needs both", proc.stdout + proc.stderr)
        self.assertEqual(proc.returncode, 1)

    def test_a_record_signed_by_another_key_is_refused_by_the_pinned_fingerprint(self):
        assert KEY is not None
        KEY.sign(self.record)
        proc = self._publish(["--corrective-republish-record", str(self.record)],
                             {"GNUPGHOME": str(KEY.home)})
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 1, out)
        self.assertIn("corrective republish requested", out)
        self.assertIn(f"is not the pinned release key {RELEASE_FINGERPRINT}", out)
        self.assertIn("does not authorize the staged set", out)
        self.assertNotIn("Generating InterGenOS.db", out)
        self.assertNotIn("SIGNING HOLD", out)
        self.assertFalse((self.archives / "InterGenOS.db").exists())

    # The fixture base class's own cases are not re-run here.
    test_any_word_other_than_sign_aborts_and_keeps_staging = None
    test_sign_reaches_the_real_key_check_and_fails_without_a_key = None
    test_skip_sign_reuses_the_signature_without_entering_the_hold = None
    test_a_new_index_requires_both_document_truth_inputs = None


if __name__ == "__main__":
    unittest.main()
