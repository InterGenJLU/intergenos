# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Account databases must never be deploy-target bytes (base-files r13).

Layer 2 of the first-install /etc overwrite fix (decided 2026-07-24). Layer 1
is pkm's config protection refusing to deploy over an unattributable /etc file
(tests/pkm/test_configprotect_first_install_clobber.py). This layer removes the
bytes that made the loss possible in the first place: intergenos-base-files
ships passwd/group/shadow/gshadow as REFERENCE data under
/usr/share/intergenos-base-files/account-skel/, and the only path from there to
/etc is a create-only seed. Defense in depth — either layer alone would have
prevented the incident, and each covers the other's failure mode:

  * a config-protection regression cannot re-create the loss, because no
    archive member is aimed at /etc/passwd any more;
  * a packaging mistake that put the files back under etc/ is caught by
    build.sh's fail-closed assertion, and by this suite.

These tests read the recipe tree directly rather than building the package: the
property under test is what the tree DECLARES, which is exactly what a future
edit would silently change.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(
    subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
)
PKG = REPO_ROOT / "packages/core/intergenos-base-files"
FILES = PKG / "files"
SKEL = FILES / "usr/share/intergenos-base-files/account-skel"
SEED = FILES / "usr/lib/intergenos/seed-account-skel.sh"
ACCOUNT_DBS = ("passwd", "group", "shadow", "gshadow")


class PayloadShape(unittest.TestCase):
    """What the package tree ships, and what it must never ship again."""

    def test_no_account_database_under_etc(self):
        for db in ACCOUNT_DBS:
            stray = FILES / "etc" / db
            self.assertFalse(
                stray.exists(),
                f"files/etc/{db} is back in the deployable payload — installing "
                f"this package on a system that never had it would deploy the "
                f"pristine skeleton over live accounts (the 2026-07-23 loss)",
            )

    def test_skeleton_ships_all_four_databases(self):
        for db in ACCOUNT_DBS:
            self.assertTrue((SKEL / db).is_file(), f"account skeleton missing {db}")

    def test_skeleton_is_the_pristine_root_plus_system_set(self):
        # The skeleton's whole hazard is that it is SMALL — root + system
        # accounts. Pin that it carries root and no human account, so a future
        # edit cannot quietly turn it into a system's real database.
        passwd = (SKEL / "passwd").read_text()
        self.assertRegex(passwd, r"^root:x:0:0:")
        # Human accounts occupy the normal login range. 65534 (nobody) is the
        # standard overflow uid and is a system account despite sorting above
        # 1000, so the range is bounded at both ends.
        human_uids = [
            line for line in passwd.splitlines()
            if line.count(":") >= 3 and line.split(":")[2].isdigit()
            and 1000 <= int(line.split(":")[2]) < 65000
        ]
        self.assertEqual(
            human_uids, [], f"skeleton carries human accounts: {human_uids}"
        )

    def test_seed_helper_ships_and_is_executable(self):
        self.assertTrue(SEED.is_file(), "seed-account-skel.sh missing from files/")
        self.assertTrue(
            SEED.stat().st_mode & 0o111,
            "seed-account-skel.sh is not executable — it would not run on target",
        )

    def test_build_halts_if_account_db_returns_to_etc_payload(self):
        # The recipe's own fail-closed guard is the build-time backstop for
        # this class; assert it is present and aimed at all four databases.
        build = (PKG / "build.sh").read_text()
        self.assertIn("DESTDIR}/etc/${_db}", build)
        self.assertIn("for _db in passwd group shadow gshadow", build)

    def test_verify_paths_claims_only_what_the_package_produces(self):
        # Rule 20/21: a verify_paths entry the package no longer produces is a
        # claim without backing. /etc/passwd is now the installer's output.
        yml = (PKG / "package.yml").read_text()
        vp = yml.split("verify_paths:", 1)[1]
        for db in ACCOUNT_DBS:
            self.assertNotIn(
                f"- /etc/{db}\n", vp,
                f"verify_paths still claims /etc/{db}, which the package no "
                f"longer installs",
            )
            self.assertIn(f"account-skel/{db}", vp)

    def test_release_was_bumped_for_the_payload_change(self):
        yml = (PKG / "package.yml").read_text()
        m = re.search(r"^release:\s*(\d+)", yml, re.M)
        self.assertIsNotNone(m)
        self.assertGreaterEqual(
            int(m.group(1)), 13,
            "changed payload must advance release or installed systems never "
            "see it (monotonic-advancement rule)",
        )


class SeedHelperBehaviour(unittest.TestCase):
    """The shipped helper creates only what is missing — never overwrites."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="seed-skel-"))
        dest_skel = self.tmp / "usr/share/intergenos-base-files/account-skel"
        dest_skel.mkdir(parents=True)
        for db in ACCOUNT_DBS:
            shutil.copyfile(SKEL / db, dest_skel / db)
        (self.tmp / "etc").mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self):
        return subprocess.run(
            ["bash", str(SEED), "--root", str(self.tmp)],
            capture_output=True, text=True,
        )

    def test_fresh_root_gets_all_four(self):
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        for db in ACCOUNT_DBS:
            self.assertTrue((self.tmp / "etc" / db).is_file(), f"/etc/{db} not seeded")

    def test_existing_database_is_never_touched(self):
        live = "root:x:0:0::/root:/bin/bash\nerica:x:1000:1000::/home/erica:/bin/bash\n"
        (self.tmp / "etc" / "passwd").write_text(live)
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.tmp / "etc" / "passwd").read_text(), live)
        self.assertIn("left untouched", r.stdout)

    def test_second_run_is_a_no_op(self):
        self._run()
        before = {db: (self.tmp / "etc" / db).read_text() for db in ACCOUNT_DBS}
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        for db in ACCOUNT_DBS:
            self.assertEqual((self.tmp / "etc" / db).read_text(), before[db])

    def test_shadow_files_are_not_world_readable(self):
        self._run()
        for db in ("shadow", "gshadow"):
            mode = (self.tmp / "etc" / db).stat().st_mode & 0o777
            self.assertEqual(mode, 0o640, f"/etc/{db} mode {oct(mode)}")

    def test_missing_skeleton_fails_loudly(self):
        shutil.rmtree(self.tmp / "usr")
        r = self._run()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("FATAL", r.stderr)


if __name__ == "__main__":
    unittest.main()
