# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Tests for the privileged model-storage provisioner (model_setup_dispatch).

The dispatcher is root's side of `intergen setup`'s model install: it
RE-verifies a staged file's sha256 against the shipped pin manifest before
installing it into the (root-owned, RO) model store. These tests pin the
fail-closed behaviour (unsafe filename / no pin / sha mismatch / missing file)
and the happy-path install, all against injected tmp paths so nothing touches
/var/lib/intergen and the suite runs unprivileged.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from intergen import model_setup_dispatch as msd


def _write_pins(pins_path: Path, entries: dict[str, str]) -> None:
    """Write a pin manifest in the shipped models-manifest.json schema."""
    pins_path.write_text(json.dumps({
        "version": "0.1",
        "entries": [
            {"name": fn, "filename": fn, "sha256": sha}
            for fn, sha in entries.items()
        ],
    }))


class TestProvision(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.pins = self.root / "models-manifest.json"
        self.store = self.root / "store" / "llm"
        self.manifest = self.root / "store" / "manifest.json"
        self.staging = self.root / "stage"
        self.staging.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _stage(self, filename: str, content: bytes) -> tuple[Path, str]:
        p = self.staging / filename
        p.write_bytes(content)
        return p, hashlib.sha256(content).hexdigest()

    def _call(self, arguments):
        return msd.provision(
            arguments,
            pins_path=self.pins,
            model_dir=self.store,
            manifest_path=self.manifest,
        )

    def test_happy_path_installs_root_owned_perms(self):
        staged, sha = self._stage("test-model.gguf", b"GGUF-fake-weights" * 64)
        _write_pins(self.pins, {"test-model.gguf": sha})
        ok, msg = self._call({"filename": "test-model.gguf",
                              "staging_path": str(staged)})
        self.assertTrue(ok, msg)
        dest = self.store / "test-model.gguf"
        self.assertTrue(dest.is_file())
        self.assertEqual(dest.read_bytes(), staged.read_bytes())
        # Installed file is 0644; the store dir is 0755.
        self.assertEqual(os.stat(dest).st_mode & 0o777, 0o644)
        self.assertEqual(os.stat(self.store).st_mode & 0o777, 0o755)
        # No leftover .incoming temp.
        self.assertFalse((self.store / ".test-model.gguf.incoming").exists())

    def test_sha_mismatch_refuses_and_writes_nothing(self):
        staged, _ = self._stage("test-model.gguf", b"actual-bytes")
        _write_pins(self.pins, {"test-model.gguf": "f" * 64})  # wrong pin
        ok, msg = self._call({"filename": "test-model.gguf",
                              "staging_path": str(staged)})
        self.assertFalse(ok)
        self.assertIn("mismatch", msg.lower())
        self.assertFalse((self.store / "test-model.gguf").exists())

    def test_unpinned_filename_refused(self):
        staged, sha = self._stage("rogue.gguf", b"x")
        _write_pins(self.pins, {"test-model.gguf": sha})  # rogue.gguf absent
        ok, msg = self._call({"filename": "rogue.gguf",
                              "staging_path": str(staged)})
        self.assertFalse(ok)
        self.assertIn("no pin", msg.lower())

    def test_path_traversal_filename_refused(self):
        staged, sha = self._stage("ok.gguf", b"x")
        _write_pins(self.pins, {"../../etc/cron.d/x": sha, "ok.gguf": sha})
        for bad in ("../../etc/cron.d/x", "/etc/passwd", "a/b.gguf", "..", ""):
            ok, msg = self._call({"filename": bad, "staging_path": str(staged)})
            self.assertFalse(ok, bad)

    def test_missing_staged_file_refused(self):
        _write_pins(self.pins, {"test-model.gguf": "a" * 64})
        ok, msg = self._call({"filename": "test-model.gguf",
                              "staging_path": str(self.staging / "absent.gguf")})
        self.assertFalse(ok)
        self.assertIn("not found", msg.lower())

    def test_relative_staging_path_refused(self):
        _write_pins(self.pins, {"test-model.gguf": "a" * 64})
        ok, msg = self._call({"filename": "test-model.gguf",
                              "staging_path": "relative/path.gguf"})
        self.assertFalse(ok)
        self.assertIn("absolute", msg.lower())

    def test_non_string_args_refused(self):
        ok, _ = self._call({"filename": 123, "staging_path": str(self.staging)})
        self.assertFalse(ok)


class TestSystemLicenseAcceptance(unittest.TestCase):
    """The setup write-set includes the system-wide license-acceptance record
    for licenses that require it (the human authenticating the install accepts
    the model's license for the system — same record Forge writes)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.pins = self.root / "models-manifest.json"
        self.store = self.root / "store" / "llm"
        self.manifest = self.root / "store" / "manifest.json"
        self.legal = self.root / "legal"
        self.staging = self.root / "stage"
        self.staging.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _install(self, filename: str):
        staged = self.staging / filename
        staged.write_bytes(b"weights" * 32)
        _write_pins(self.pins, {filename: hashlib.sha256(staged.read_bytes()).hexdigest()})
        return msd.provision(
            {"filename": filename, "staging_path": str(staged)},
            pins_path=self.pins, model_dir=self.store,
            manifest_path=self.manifest, system_legal_dir=self.legal,
            accepted_by="installer-admin",
        )

    def test_qwen_writes_system_acceptance(self):
        # Qwen → Tongyi-Qianwen license requires acceptance → record written.
        # Tier-1 (2B) is now InternVL3.5 (Apache); the 9B remains Qwen (the
        # tier-2 payload is the fine-tuned round-3 build of the same base), so
        # it stays the representative Tongyi-acceptance case.
        ok, msg = self._install("Qwen3.5-9B-intergen-round3-Q4_K_M.gguf")
        self.assertTrue(ok, msg)
        rec_path = self.legal / "Qwen3.5-9B-intergen-round3-Q4_K_M.gguf-accepted.json"
        self.assertTrue(rec_path.is_file())
        rec = json.loads(rec_path.read_text())
        self.assertEqual(rec["license_ref"], "LicenseRef-Tongyi-Qianwen")
        self.assertEqual(rec["scope"], "system")
        self.assertEqual(rec["accepted_by"], "installer-admin")
        self.assertEqual(rec["filename"], "Qwen3.5-9B-intergen-round3-Q4_K_M.gguf")
        self.assertEqual(os.stat(rec_path).st_mode & 0o777, 0o644)

    def test_apache_model_writes_no_acceptance(self):
        # nomic-embed → Apache-2.0 is auto-accepted → no record written.
        ok, msg = self._install("nomic-embed-text-v1.5.Q8_0.gguf")
        self.assertTrue(ok, msg)
        self.assertFalse((self.legal / "nomic-embed-text-v1.5.Q8_0.gguf-accepted.json").exists())
        # ...and no stray legal dir contents.
        self.assertFalse(self.legal.exists() and any(self.legal.iterdir()))


class TestMainArgvAndEnvGate(unittest.TestCase):
    def setUp(self):
        self._saved_uid = os.environ.get("PKEXEC_UID")
        os.environ["PKEXEC_UID"] = "1000"

    def tearDown(self):
        if self._saved_uid is None:
            os.environ.pop("PKEXEC_UID", None)
        else:
            os.environ["PKEXEC_UID"] = self._saved_uid

    def test_wrong_argv_count_is_exit_2(self):
        self.assertEqual(msd.main([]), 2)
        self.assertEqual(msd.main(["a", "b"]), 2)

    def test_missing_pkexec_uid_refused(self):
        os.environ.pop("PKEXEC_UID", None)
        self.assertEqual(msd.main(['{"filename": "x", "staging_path": "/x"}']), 1)

    def test_malformed_json_refused(self):
        self.assertEqual(msd.main(["not-json"]), 1)

    def test_non_object_json_refused(self):
        self.assertEqual(msd.main(["[1, 2, 3]"]), 1)


class TestProvisionMmproj(unittest.TestCase):
    """Paired-mmproj (vision) install — both artifacts ride one pkexec, both
    boundary-re-verified, validate-both-before-install-either (fail-closed)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.pins = self.root / "models-manifest.json"
        self.store = self.root / "store" / "llm"
        self.manifest = self.root / "store" / "manifest.json"
        self.staging = self.root / "stage"
        self.staging.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _stage(self, filename: str, content: bytes) -> tuple[Path, str]:
        p = self.staging / filename
        p.write_bytes(content)
        return p, hashlib.sha256(content).hexdigest()

    def _call(self, arguments):
        return msd.provision(arguments, pins_path=self.pins,
                             model_dir=self.store, manifest_path=self.manifest)

    def test_vision_installs_both_artifacts(self):
        g_staged, g_sha = self._stage("vlm.gguf", b"GGUF" * 100)
        m_staged, m_sha = self._stage("mmproj-vlm.gguf", b"MMPROJ" * 100)
        _write_pins(self.pins, {"vlm.gguf": g_sha, "mmproj-vlm.gguf": m_sha})
        ok, msg = self._call({
            "filename": "vlm.gguf", "staging_path": str(g_staged),
            "mmproj_filename": "mmproj-vlm.gguf",
            "mmproj_staging_path": str(m_staged),
        })
        self.assertTrue(ok, msg)
        self.assertTrue((self.store / "vlm.gguf").is_file())
        self.assertTrue((self.store / "mmproj-vlm.gguf").is_file())
        self.assertIn("projector", msg)
        self.assertEqual(
            (self.store / "mmproj-vlm.gguf").stat().st_mode & 0o777, 0o644)

    def test_mmproj_mismatch_installs_neither(self):
        # GGUF pin correct, mmproj pin wrong → boundary mismatch must abort the
        # WHOLE install (validate-both-before-install) — no GGUF left behind.
        g_staged, g_sha = self._stage("vlm.gguf", b"GGUF" * 100)
        m_staged, _ = self._stage("mmproj-vlm.gguf", b"MMPROJ" * 100)
        _write_pins(self.pins, {"vlm.gguf": g_sha, "mmproj-vlm.gguf": "f" * 64})
        ok, msg = self._call({
            "filename": "vlm.gguf", "staging_path": str(g_staged),
            "mmproj_filename": "mmproj-vlm.gguf",
            "mmproj_staging_path": str(m_staged),
        })
        self.assertFalse(ok)
        self.assertIn("mismatch", msg.lower())
        self.assertFalse((self.store / "vlm.gguf").exists())
        self.assertFalse((self.store / "mmproj-vlm.gguf").exists())

    def test_mmproj_install_failure_removes_primary(self):
        # Both pins VALID (validation passes), but the projector INSTALL fails
        # mid-copy (transient OSError). The primary GGUF already landed; it must
        # be removed so NEITHER artifact remains. Else get_model_for_tier derives
        # downloaded=True from the on-disk GGUF and a daemon without the
        # has_vision-requires-mmproj launch guard would serve it silently
        # text-only — the exact gap. Parity with the sha-mismatch path.
        g_staged, g_sha = self._stage("vlm.gguf", b"GGUF" * 100)
        m_staged, m_sha = self._stage("mmproj-vlm.gguf", b"MMPROJ" * 100)
        _write_pins(self.pins, {"vlm.gguf": g_sha, "mmproj-vlm.gguf": m_sha})

        real_install = msd._install_staged

        def flaky_install(filename, staging_path, model_dir):
            # Primary installs for real; the projector fails to land.
            if filename.startswith("mmproj-"):
                return False, f"provision: install of {filename} failed: boom."
            return real_install(filename, staging_path, model_dir)

        with mock.patch.object(msd, "_install_staged", side_effect=flaky_install):
            ok, msg = self._call({
                "filename": "vlm.gguf", "staging_path": str(g_staged),
                "mmproj_filename": "mmproj-vlm.gguf",
                "mmproj_staging_path": str(m_staged),
            })
        self.assertFalse(ok)
        self.assertIn("failed", msg.lower())
        # The just-installed primary must be rolled back — no launch-eligible
        # projector-less vision GGUF left in the store.
        self.assertFalse((self.store / "vlm.gguf").exists())
        self.assertFalse((self.store / "mmproj-vlm.gguf").exists())

    def test_mmproj_unpinned_refused(self):
        g_staged, g_sha = self._stage("vlm.gguf", b"GGUF" * 100)
        m_staged, _ = self._stage("mmproj-vlm.gguf", b"MMPROJ" * 100)
        _write_pins(self.pins, {"vlm.gguf": g_sha})   # projector NOT pinned
        ok, msg = self._call({
            "filename": "vlm.gguf", "staging_path": str(g_staged),
            "mmproj_filename": "mmproj-vlm.gguf",
            "mmproj_staging_path": str(m_staged),
        })
        self.assertFalse(ok)
        self.assertIn("no pin", msg.lower())
        self.assertFalse((self.store / "vlm.gguf").exists())

    def test_partial_mmproj_args_refused(self):
        g_staged, g_sha = self._stage("vlm.gguf", b"GGUF" * 100)
        _write_pins(self.pins, {"vlm.gguf": g_sha})
        ok, msg = self._call({
            "filename": "vlm.gguf", "staging_path": str(g_staged),
            "mmproj_filename": "mmproj-vlm.gguf",   # staging_path missing
        })
        self.assertFalse(ok)
        self.assertFalse((self.store / "vlm.gguf").exists())

    def test_non_vision_single_file_unchanged(self):
        g_staged, g_sha = self._stage("plain.gguf", b"GGUF" * 100)
        _write_pins(self.pins, {"plain.gguf": g_sha})
        ok, msg = self._call({"filename": "plain.gguf",
                             "staging_path": str(g_staged)})
        self.assertTrue(ok, msg)
        self.assertTrue((self.store / "plain.gguf").is_file())
        self.assertNotIn("projector", msg)


if __name__ == "__main__":
    unittest.main()
