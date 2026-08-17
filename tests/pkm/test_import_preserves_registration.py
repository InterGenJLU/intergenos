#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""`pkm import` refreshes a registration; it must never downgrade or launder one.

import_manifests is the registration path with the least information. A text
manifest states a name, a version, a release, a description, a build date, a
size and a file list — and nothing else. Every other column on an installed row
was written by a path that could see more: the archive installer reads tier,
license, archive_path, install_reason and reboot_required out of the archive's
.PKGINFO, the source builder writes tier and license from the recipe, and both
record runtime dependencies.

That asymmetry is safe only if a re-register CARRIES what it cannot restate.
When it did not, one `pkm import` — which the build fires corpus-wide after
every bash-tier package, and which a user can run at any time — reset tier and
license to NULL, install_method to 'source', archive_path to NULL,
install_reason to 'manual' and reboot_required to 0, and deleted every depends
row without replacement. Measured 2026-07-30 against a real installed system's
database and its own manifests: openssh, sudo and linux-kernel each lost tier,
install_method, archive_path and description, linux-kernel's reboot_required
went 1 -> 0 (silencing the F28 reboot banner for the one package that needs
it), and 4 depends rows vanished.

Three further properties are asserted here because each was a distinct way the
same call could damage state it was only supposed to refresh:

  - config-protect baselines survive. The old code routed through
    remove_installed, which deletes config_files rows explicitly, and then
    re-recorded each baseline from whatever was on disk. A user-edited /etc
    file therefore read as unedited afterwards, so the next upgrade
    overwrote it with stock and wrote no .pkmnew sidecar. Measured on the same
    real database: two of eight openssh config paths — both D-007 sshd
    hardening drop-ins — were re-baselined to live content.
  - the manifest's own hashes are what get recorded. Dropping them made
    add_files fall through to hashing the live file, which turns a
    corpus-wide import into an unscoped reconcile: content that had diverged
    from its recorded bytes was written in as the new truth and verify
    reported clean.
  - an unreadable /etc file does not abort the run. The config branch had no
    exception handling where the regular-file branch has it, so an
    unprivileged `pkm import` died on the first 0600 conffile — after the
    package it was processing had already been deregistered.
"""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from pkm.database import PackageDB, _sha256


class ImportPreservesRegistrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "root"
        self.mdir = Path(self.tmp.name) / "manifests"
        self.root.mkdir()
        self.mdir.mkdir()
        self.db = PackageDB(str(Path(self.tmp.name) / "pkm.db"),
                            root=str(self.root))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    # -- helpers ---------------------------------------------------------

    def _live(self, rel, content):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return p

    def _manifest(self, name, version, entries, release=None):
        """Write a text manifest. `entries` is a list of raw FILE LIST lines."""
        lines = [f"PACKAGE NAME: {name}-{version}",
                 f"PACKAGE VERSION: {version}"]
        if release is not None:
            lines.append(f"PACKAGE RELEASE: {release}")
        lines += ["UNCOMPRESSED SIZE: 1K (1024 bytes)",
                  "BUILD DATE: 2026-07-30T00:00:00Z",
                  "BUILD SYSTEM: InterGenOS pkm",
                  "DESCRIPTION:",
                  f"{name}: the real description",
                  "",
                  "FILE LIST:"]
        lines += entries
        (self.mdir / f"{name}-{version}").write_text("\n".join(lines) + "\n")

    def _row(self, name):
        return self.db.get_installed(name)

    # -- the carry rule --------------------------------------------------

    def test_re_register_carries_every_column_the_manifest_cannot_state(self):
        rel = "usr/bin/demo"
        self._live(rel, "payload\n")
        pkg_id = self.db.add_installed(
            name="demo", version="1.0", release=4, tier="core",
            description="from the archive", license_="GPL-3.0-or-later",
            install_method="archive", archive_path="/var/lib/igos/a.tar.gz",
            install_reason="dependency", reboot_required=1,
        )
        self.db.add_files(pkg_id, [rel], hashes={rel: _sha256(str(self.root / rel))})
        self.db.add_depends(pkg_id, [("libc", "runtime"), ("zlib", "runtime")])
        self.db.set_held("demo", True)
        self.db.mark_degraded("demo", "depmod")

        self._manifest("demo", "1.0", [rel], release=4)
        self.assertEqual(self.db.import_manifests(self.mdir), 1)

        row = self._row("demo")
        self.assertEqual(row["tier"], "core")
        self.assertEqual(row["license"], "GPL-3.0-or-later")
        self.assertEqual(row["install_method"], "archive")
        self.assertEqual(row["archive_path"], "/var/lib/igos/a.tar.gz")
        self.assertEqual(row["install_reason"], "dependency")
        self.assertEqual(row["reboot_required"], 1)
        self.assertEqual(row["release"], 4)
        # held and degraded have no add_installed parameter, so the upsert
        # reset them too — releasing a hold and clearing a failed-hook marker
        # with no message.
        self.assertEqual(row["held"], 1)
        self.assertEqual(row["degraded"], "depmod")
        deps = {d["name"] for d in self.db.get_depends("demo")}
        self.assertEqual(deps, {"libc", "zlib"})

    def test_first_registration_of_an_unknown_package_uses_the_defaults(self):
        """The carry rule must not invent values for a package never seen."""
        rel = "usr/bin/fresh"
        self._live(rel, "x\n")
        self._manifest("fresh", "2.0", [rel])
        self.assertEqual(self.db.import_manifests(self.mdir), 1)
        row = self._row("fresh")
        self.assertIsNone(row["tier"])
        self.assertIsNone(row["license"])
        self.assertEqual(row["install_method"], "source")
        self.assertEqual(row["install_reason"], "manual")
        self.assertEqual(row["reboot_required"], 0)
        self.assertEqual(row["release"], 1)

    def test_description_prefers_the_manifest_then_the_row(self):
        rel = "usr/bin/demo"
        self._live(rel, "x\n")
        pkg_id = self.db.add_installed(name="demo", version="1.0", release=1,
                                       description="from the archive")
        self.db.add_files(pkg_id, [rel])
        self._manifest("demo", "1.0", [rel], release=1)
        self.db.import_manifests(self.mdir)
        self.assertEqual(self._row("demo")["description"],
                         "the real description")

    # -- config-protect baselines ---------------------------------------

    def test_a_user_edited_config_keeps_its_original_baseline(self):
        """The measured leg: import must not re-baseline to live content."""
        rel = "etc/demo/demo.conf"
        self._live(rel, "stock\n")
        stock_sha = _sha256(str(self.root / rel))
        pkg_id = self.db.add_installed(name="demo", version="1.0", release=1,
                                       tier="core", install_method="archive")
        self.db.add_files(pkg_id, [rel], hashes={rel: stock_sha})
        self.assertEqual(self.db.get_original_checksum(rel), stock_sha)

        # The user edits the file after install.
        self._live(rel, "user made this change\n")
        edited_sha = _sha256(str(self.root / rel))
        self.assertNotEqual(edited_sha, stock_sha)

        self._manifest("demo", "1.0", [f"{rel} sha256:{stock_sha}"], release=1)
        self.db.import_manifests(self.mdir)

        # Still the stock baseline: the next upgrade must be able to SEE the
        # edit and protect it. A baseline equal to edited_sha would classify
        # the file as unedited and overwrite the user's content silently.
        self.assertEqual(self.db.get_original_checksum(rel), stock_sha)

    def test_baseline_survives_a_version_bump_re_register(self):
        rel = "etc/demo/demo.conf"
        self._live(rel, "stock\n")
        stock_sha = _sha256(str(self.root / rel))
        pkg_id = self.db.add_installed(name="demo", version="1.0", release=1)
        self.db.add_files(pkg_id, [rel], hashes={rel: stock_sha})
        self._live(rel, "edited\n")

        self._manifest("demo", "2.0", [rel], release=1)
        self.db.import_manifests(self.mdir)
        self.assertEqual(self._row("demo")["version"], "2.0")
        self.assertEqual(self.db.get_original_checksum(rel), stock_sha)

    # -- hashes ----------------------------------------------------------

    def test_recorded_checksum_comes_from_the_manifest_not_the_live_file(self):
        rel = "usr/bin/demo"
        self._live(rel, "the recorded bytes\n")
        recorded = _sha256(str(self.root / rel))
        # The live file diverges from what the manifest records — a rebuild,
        # a hook rewrite, or tampering. import must not bless it.
        self._live(rel, "something else entirely\n")
        live = _sha256(str(self.root / rel))
        self.assertNotEqual(recorded, live)

        self._manifest("demo", "1.0", [f"{rel} sha256:{recorded}"], release=1)
        self.db.import_manifests(self.mdir)

        con = sqlite3.connect(str(self.db.db_path))
        got = con.execute("SELECT checksum FROM files WHERE path = ?",
                          (rel,)).fetchone()[0]
        con.close()
        self.assertEqual(got, recorded)
        self.assertNotEqual(got, live)
        # And verify now reports the divergence instead of hiding it.
        self.assertIn(rel, self.db.verify_package("demo")["modified"])

    def test_an_entry_without_a_hash_still_falls_back_to_the_live_read(self):
        """Legacy manifests carry no annotation; they must still register."""
        rel = "usr/bin/legacy"
        self._live(rel, "bytes\n")
        self._manifest("legacy", "1.0", [rel])
        self.db.import_manifests(self.mdir)
        con = sqlite3.connect(str(self.db.db_path))
        got = con.execute("SELECT checksum FROM files WHERE path = ?",
                          (rel,)).fetchone()[0]
        con.close()
        self.assertEqual(got, _sha256(str(self.root / rel)))

    # -- unreadable config + transaction ---------------------------------

    @unittest.skipIf(os.geteuid() == 0, "root can read a 0600 file")
    def test_an_unreadable_config_file_does_not_abort_the_run(self):
        """The measured leg: an unprivileged import died on /etc/sudoers.dist."""
        unreadable = "etc/locked.conf"
        p = self._live(unreadable, "secret\n")
        os.chmod(p, 0o000)
        readable = "usr/bin/after"
        self._live(readable, "x\n")
        # Sorted order puts "aaa-1.0" before "zzz-1.0", so the package after
        # the unreadable one proves the run continued rather than dying.
        self._manifest("aaa", "1.0", [unreadable])
        self._manifest("zzz", "1.0", [readable])

        self.assertEqual(self.db.import_manifests(self.mdir), 2)
        self.assertIsNotNone(self._row("aaa"))
        self.assertIsNotNone(self._row("zzz"))
        # The baseline is recorded as unknown rather than as a wrong value.
        self.assertIsNone(self.db.get_original_checksum(unreadable))

    def test_a_failure_mid_package_leaves_the_previous_row_intact(self):
        """The re-register is transactional: no half-deregistered package."""
        rel = "usr/bin/demo"
        self._live(rel, "x\n")
        pkg_id = self.db.add_installed(name="demo", version="1.0", release=3,
                                       tier="core", install_method="archive")
        self.db.add_files(pkg_id, [rel])
        self._manifest("demo", "2.0", [rel], release=1)

        real_add_files = self.db.add_files

        def boom(*a, **kw):
            raise sqlite3.IntegrityError("simulated failure after the upsert")

        self.db.add_files = boom
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.import_manifests(self.mdir)
        self.db.add_files = real_add_files

        row = self._row("demo")
        self.assertIsNotNone(row, "the package must not be left deregistered")
        self.assertEqual(row["version"], "1.0")
        self.assertEqual(row["release"], 3)
        self.assertEqual(row["tier"], "core")
        self.assertEqual(len(self.db.get_files("demo")), 1)

    # -- provenance ------------------------------------------------------

    def test_a_stamped_row_is_a_true_no_op(self):
        rel = "usr/bin/demo"
        self._live(rel, "x\n")
        self._manifest("demo", "1.0", [rel], release=1)
        self.assertEqual(self.db.import_manifests(self.mdir), 1)
        # Second pass: the stored hash now matches, so nothing re-registers.
        self.assertEqual(self.db.import_manifests(self.mdir), 0)

    def test_set_manifest_sha256_makes_a_writer_row_a_no_op(self):
        """What the archive and source writers now do at their manifest write."""
        rel = "usr/bin/demo"
        self._live(rel, "x\n")
        pkg_id = self.db.add_installed(name="demo", version="1.0", release=2,
                                       tier="core", install_method="archive")
        self.db.add_files(pkg_id, [rel])
        self._manifest("demo", "1.0", [rel], release=2)
        stamped = _sha256(str(self.mdir / "demo-1.0"))
        self.assertEqual(self.db.set_manifest_sha256("demo", stamped), 1)
        self.assertEqual(self.db.import_manifests(self.mdir), 0)
        self.assertEqual(self._row("demo")["install_method"], "archive")


if __name__ == "__main__":
    unittest.main()
