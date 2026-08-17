# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Forge seeds the target's account databases — create-only, before useradd.

intergenos-base-files r13 stopped shipping passwd/group/shadow/gshadow as /etc
payload (decided 2026-07-24, after the pristine skeleton was deployed over live
account databases on a first install and every real account row was lost). The
databases now ship as reference data under /usr/share and reach /etc only by an
explicit create-only step — which means the install pipeline has to perform that
step, or a fresh target ends up with no account databases at all.

Ordering is the load-bearing part and is asserted here as a property of the
phase list, not of a comment: the config phase runs after the package phase that
populates the target and before the users phase whose `useradd --root` needs the
databases present. A package post-install hook could not serve — hooks run four
phases later.
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from installer.backend import config, install  # noqa: E402

SKELETON_PASSWD = "root:x:0:0::/root:/bin/bash\nbin:x:1:1::/dev/null:/bin/false\n"
SKELETON_GROUP = "root:x:0:\nwheel:x:97:\n"
SKELETON_SHADOW = "root:!:0:0:99999:7:::\n"
SKELETON_GSHADOW = "root:::\n"
SKELETON = {
    "passwd": SKELETON_PASSWD,
    "group": SKELETON_GROUP,
    "shadow": SKELETON_SHADOW,
    "gshadow": SKELETON_GSHADOW,
}


class SeedAccountDatabases(unittest.TestCase):
    def setUp(self):
        self.target = Path(tempfile.mkdtemp(prefix="forge-seed-"))
        skel = self.target / config.ACCOUNT_SKEL_REL
        skel.mkdir(parents=True)
        for name, content in SKELETON.items():
            (skel / name).write_text(content)

    def tearDown(self):
        shutil.rmtree(self.target, ignore_errors=True)

    def test_fresh_target_receives_all_four(self):
        result = config.seed_account_databases(self.target)
        self.assertEqual(sorted(result["seeded"]), sorted(config.ACCOUNT_DATABASES))
        self.assertEqual(result["kept"], [])
        for name, content in SKELETON.items():
            self.assertEqual((self.target / "etc" / name).read_text(), content)

    def test_existing_database_is_kept_verbatim(self):
        # The incident's shape, at the installer layer: a populated target must
        # never have its account state replaced by the skeleton.
        live = (
            "root:x:0:0::/root:/bin/bash\n"
            "erica:x:1000:1000::/home/erica:/bin/bash\n"
        )
        etc = self.target / "etc"
        etc.mkdir()
        (etc / "passwd").write_text(live)

        result = config.seed_account_databases(self.target)

        self.assertEqual((etc / "passwd").read_text(), live)
        self.assertIn("passwd", result["kept"])
        self.assertNotIn("passwd", result["seeded"])
        # The other three were absent and are created.
        self.assertEqual(sorted(result["seeded"]), ["group", "gshadow", "shadow"])

    def test_shadow_databases_are_not_world_readable(self):
        config.seed_account_databases(self.target)
        for name in ("shadow", "gshadow"):
            mode = (self.target / "etc" / name).stat().st_mode & 0o777
            self.assertEqual(mode, 0o640, f"/etc/{name} mode {oct(mode)}")
        for name in ("passwd", "group"):
            mode = (self.target / "etc" / name).stat().st_mode & 0o777
            self.assertEqual(mode, 0o644, f"/etc/{name} mode {oct(mode)}")

    def test_absent_skeleton_raises_rather_than_silently_skipping(self):
        shutil.rmtree(self.target / "usr")
        with self.assertRaises(FileNotFoundError):
            config.seed_account_databases(self.target)

    def test_incomplete_skeleton_raises(self):
        (self.target / config.ACCOUNT_SKEL_REL / "gshadow").unlink()
        with self.assertRaises(FileNotFoundError):
            config.seed_account_databases(self.target)

    def test_is_idempotent(self):
        config.seed_account_databases(self.target)
        first = {n: (self.target / "etc" / n).read_text() for n in SKELETON}
        result = config.seed_account_databases(self.target)
        self.assertEqual(result["seeded"], [])
        self.assertEqual(sorted(result["kept"]), sorted(config.ACCOUNT_DATABASES))
        for name, content in first.items():
            self.assertEqual((self.target / "etc" / name).read_text(), content)


class SeedOrderingContract(unittest.TestCase):
    """The seed must land between target population and account creation."""

    def test_config_phase_runs_after_packages_and_before_users(self):
        order = install.PHASE_ORDER
        self.assertLess(
            order.index(install.PHASE_PACKAGES), order.index(install.PHASE_CONFIG)
        )
        self.assertLess(
            order.index(install.PHASE_CONFIG), order.index(install.PHASE_USERS)
        )

    def test_hooks_phase_would_be_too_late(self):
        # Pins why the seed is not a package post-install hook.
        order = install.PHASE_ORDER
        self.assertGreater(
            order.index(install.PHASE_HOOKS), order.index(install.PHASE_USERS)
        )

    def test_generate_all_seeds_before_anything_else_touches_etc(self):
        src = Path(config.__file__).read_text()
        body = src.split("def generate_all(", 1)[1]
        self.assertIn("seed_account_databases(target)", body)
        seed_at = body.index("seed_account_databases(target)")
        fstab_at = body.index("generate_fstab(target, partitions)")
        self.assertLess(seed_at, fstab_at)


if __name__ == "__main__":
    unittest.main()
