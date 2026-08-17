# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""End-to-end regression fixture for the first-install /etc overwrite class.

The incident (root-caused 2026-07-23): pkm configprotect treated "no recorded
baseline" as "unedited, safe to deploy". On a box where the owning package had
never been installed, the pristine skeleton intergenos-base-files ships was
deployed OVER the live account databases, wiping every real account row.

tests/pkm/test_configprotect.py::TestNoBaselineLiveExists pins the PLANNING
half — prepare_config_protection returns the path in "protect". This module
pins the half that actually saved the bytes: that the plan, composed into the
deploy exactly the way pkm.installer composes it (installer.py's
`deploy_excludes = _ARCHIVE_METADATA_FILES | set(config_plan["protect"])`,
handed to _safe_extract_tar), leaves the live file byte-identical. A planning
list nothing consumes protects nothing, and the planning test alone cannot
tell the difference.

The archive here carries the real shape: a pristine skeleton /etc/passwd (root
+ system accounts only) landing on a live /etc/passwd that has real accounts,
with no config_files row recorded for it.
"""
from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path

from pkm.configprotect import (
    materialize_pkmnew_sidecars,
    prepare_config_protection,
    summary_lines,
)
from pkm.database import PackageDB
from pkm.installer import _ARCHIVE_METADATA_FILES, _safe_extract_tar

# The pristine skeleton as base-files ships it: root + system accounts only.
SKELETON_PASSWD = "root:x:0:0::/root:/bin/bash\nbin:x:1:1::/dev/null:/bin/false\n"

# The live database on a box in service: the skeleton PLUS real accounts. This
# is what the incident destroyed.
LIVE_PASSWD = (
    "root:x:0:0::/root:/bin/bash\n"
    "bin:x:1:1::/dev/null:/bin/false\n"
    "erica:x:1000:1000::/home/erica:/bin/bash\n"
    "christopher:x:1001:1001::/home/christopher:/bin/bash\n"
)


class FirstInstallClobberRegression(unittest.TestCase):
    """No recorded baseline + live file differs from payload → live SURVIVES."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pkm-clobber-"))
        self.live_root = self.tmp / "root"
        (self.live_root / "etc").mkdir(parents=True)
        self.staging = self.tmp / "staging"
        (self.staging / "etc").mkdir(parents=True)
        self.db = PackageDB(self.tmp / "test.db", root=str(self.live_root))

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _build_archive(self, rel: str, content: str) -> Path:
        """Stage `content` at `rel` and pack an archive carrying exactly it."""
        staged = self.staging / rel
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_text(content)
        archive = self.tmp / "payload.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            tf.add(str(staged), arcname=rel)
        return archive

    def test_live_account_db_survives_first_install_of_owning_package(self):
        rel = "etc/passwd"
        archive = self._build_archive(rel, SKELETON_PASSWD)
        live = self.live_root / rel
        live.write_text(LIVE_PASSWD)

        # Precondition: this is genuinely the incident's state — the package
        # has never been installed here, so nothing is recorded for the path.
        self.assertIsNone(self.db.get_original_checksum(rel))

        plan = prepare_config_protection(
            self.staging, [rel], self.live_root, self.db
        )
        self.assertEqual(plan["protect"], [rel])

        # Compose + run the deploy exactly as pkm.installer does.
        deploy_excludes = _ARCHIVE_METADATA_FILES | set(plan["protect"])
        ok, err = _safe_extract_tar(
            archive, self.live_root, exclude_paths=deploy_excludes
        )
        self.assertTrue(ok, f"deploy failed: {err}")

        # THE ASSERTION THE INCIDENT FAILED: live bytes are untouched.
        self.assertEqual(live.read_text(), LIVE_PASSWD)
        self.assertIn("erica", live.read_text())
        self.assertIn("christopher", live.read_text())

        # The incoming stock is delivered beside it, not over it.
        written = materialize_pkmnew_sidecars(plan["pkmnew_writes"])
        sidecar = self.live_root / "etc" / "passwd.pkmnew"
        self.assertEqual(written, [str(sidecar)])
        self.assertEqual(sidecar.read_text(), SKELETON_PASSWD)

        # And the outcome is reported loudly rather than passing in silence.
        summary = summary_lines(written)
        self.assertIn("passwd.pkmnew", summary)
        self.assertIn("KEPT", summary)

        # No recorded baseline was invented for content we cannot attribute.
        self.assertEqual(plan["update_baselines"], {})

    def test_deploy_still_lands_when_no_live_file_exists(self):
        """The create path is untouched: a fresh target still gets the file.

        Forge's PHASE_USERS runs `useradd --root` after PHASE_PACKAGES and
        before PHASE_HOOKS, so an account database that never arrives is an
        install failure. Protecting the overwrite case must not break create.
        """
        rel = "etc/passwd"
        archive = self._build_archive(rel, SKELETON_PASSWD)
        live = self.live_root / rel
        self.assertFalse(live.exists())

        plan = prepare_config_protection(
            self.staging, [rel], self.live_root, self.db
        )
        self.assertEqual(plan["protect"], [])

        deploy_excludes = _ARCHIVE_METADATA_FILES | set(plan["protect"])
        ok, err = _safe_extract_tar(
            archive, self.live_root, exclude_paths=deploy_excludes
        )
        self.assertTrue(ok, f"deploy failed: {err}")
        self.assertEqual(live.read_text(), SKELETON_PASSWD)


class AdviceIsReviewThenMerge(unittest.TestCase):
    """No block may print a bare `mv <path>.pkmnew <path>` accept step.

    A protected file is protected because its live content could not be
    attributed to us. A blind move discards exactly what the protect arm
    preserved — on the four boxes carrying account-DB sidecars from the
    upgrade path, following that advice would have re-created the incident by
    hand. Review-then-merge is the only honest instruction.
    """

    def test_regular_sidecar_advice_has_no_blind_move(self):
        s = summary_lines(["/etc/sudoers.pkmnew"])
        self.assertNotIn("mv <path>.pkmnew <path>", s)
        self.assertNotIn("mv /etc/sudoers.pkmnew", s)
        self.assertIn("diff <path> <path>.pkmnew", s)
        self.assertIn("pkm refresh-baseline", s)
        self.assertIn("KEPT", s)

    def test_account_sidecar_advice_refuses_the_move(self):
        s = summary_lines(["/etc/shadow.pkmnew"])
        self.assertNotIn("mv <path>.pkmnew <path>", s)
        self.assertIn("do NOT", s)
        self.assertIn("erase every real account", s)

    def test_mixed_batch_keeps_both_blocks_move_free(self):
        s = summary_lines(["/etc/sudoers.pkmnew", "/etc/group.pkmnew"])
        self.assertNotIn("mv <path>.pkmnew <path>", s)
        self.assertIn("/etc/sudoers.pkmnew", s)
        self.assertIn("/etc/group.pkmnew", s)


if __name__ == "__main__":
    unittest.main()
