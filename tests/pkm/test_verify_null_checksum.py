# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Strict verify must not pass a NULL-checksum regular file on existence.

verify_package's per-file loop skipped the content check whenever the
recorded checksum was NULL — under --strict a regular file with no hash
reference passed on existence alone, indistinguishable from a
content-verified file. Such rows must surface as "unverifiable": the
check that was expected could not run.

Scope pins:
  - regular non-config, non-exempt file with NULL hash -> unverifiable,
    EXIT_UNDETERMINED (strict); fast mode unaffected. The code was
    EXIT_MODIFIED until 2026-08-03: a file with no recorded hash has not
    been found faulty, it has not been checked, and reporting the two the
    same way told users a healthy system had failed verification. It now
    shares EXIT_UNDETERMINED with files this process cannot read.
  - config files and symlinks with NULL hash -> NOT flagged (no content
    expectation by design).
  - reconcile_checksums_from_live backfills the hash -> verify returns
    to clean (the designed remediation path).
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pkm.database import PackageDB
from pkm.verifier import PackageVerifier, EXIT_OK, EXIT_UNDETERMINED


class VerifyNullChecksumTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "root"
        (self.root / "usr" / "bin").mkdir(parents=True)
        (self.root / "etc").mkdir()
        self.db = PackageDB(self.tmp / "t.db", root=str(self.root))
        self.verifier = PackageVerifier(self.db)

    def tearDown(self):
        self.db.close()

    def _add_pkg_with_null_hash_row(self, name, relpath, create=True,
                                    content=b"payload"):
        """Register a file row with checksum NULL.

        add_files computes a hash only when the file exists at add time,
        so registering while the file is absent and creating it after
        yields the NULL-checksum row shape found on real systems.
        """
        pkg_id = self.db.add_installed(name, "1.0", release=1, tier="core")
        self.db.add_files(pkg_id, [relpath])
        if create:
            p = self.root / relpath
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
        return pkg_id

    def test_null_hash_regular_file_is_unverifiable_under_strict(self):
        self._add_pkg_with_null_hash_row("alpha", "usr/bin/alpha")
        result = self.verifier.verify("alpha", mode="strict")
        self.assertEqual(result["unverifiable"], ["usr/bin/alpha"])
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["modified"], [])
        self.assertEqual(result["exit_code"], EXIT_UNDETERMINED)

    def test_fast_mode_does_not_flag_null_hash(self):
        self._add_pkg_with_null_hash_row("alpha", "usr/bin/alpha")
        result = self.verifier.verify("alpha", mode="fast")
        self.assertEqual(result["unverifiable"], [])
        self.assertEqual(result["exit_code"], EXIT_OK)

    def test_null_hash_config_file_not_flagged(self):
        # Config files are content-exempt by design (PKM-E3) — a NULL
        # hash on etc/* carries no content expectation.
        self._add_pkg_with_null_hash_row("beta", "etc/beta.conf")
        result = self.verifier.verify("beta", mode="strict")
        self.assertEqual(result["unverifiable"], [])
        self.assertEqual(result["exit_code"], EXIT_OK)

    def test_null_hash_symlink_not_flagged(self):
        # Symlinks are existence-checked only: no link-target hash is
        # recorded, and the target's bytes belong to the target's row.
        pkg_id = self.db.add_installed("gamma", "1.0", release=1, tier="core")
        self.db.add_files(pkg_id, ["usr/bin/gamma-link"])
        target = self.root / "usr" / "bin" / "gamma-target"
        target.write_bytes(b"target-bytes")
        (self.root / "usr" / "bin" / "gamma-link").symlink_to(target)
        result = self.verifier.verify("gamma", mode="strict")
        self.assertEqual(result["unverifiable"], [])
        self.assertEqual(result["exit_code"], EXIT_OK)

    def test_reconcile_backfills_and_verify_goes_clean(self):
        self._add_pkg_with_null_hash_row("delta", "usr/bin/delta")
        result = self.verifier.verify("delta", mode="strict")
        self.assertEqual(result["exit_code"], EXIT_UNDETERMINED)

        updated = self.db.reconcile_checksums_from_live(
            paths=["usr/bin/delta"])
        self.assertEqual(updated, 1)

        result = self.verifier.verify("delta", mode="strict")
        self.assertEqual(result["unverifiable"], [])
        self.assertEqual(result["exit_code"], EXIT_OK)


if __name__ == "__main__":
    unittest.main()
