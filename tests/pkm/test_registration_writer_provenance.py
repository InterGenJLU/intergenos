#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Every path that writes a row AND its manifest records the manifest's hash.

Component A keys re-registration on installed.manifest_sha256: import_manifests
re-registers whenever the manifest on disk hashes differently from the stored
value, and treats NULL as "provenance unproven", meaning re-register once. That
rule is correct, and it was harmless only for as long as a re-register was
lossless — which it was not.

No writer stamped the column. Measured 2026-07-30 on a real installed system:
972 of 972 rows carried manifest_sha256 NULL, so the first `pkm import` anyone
ran would re-register the entire install set and, at the time, drop tier,
license, archive_path, install_method, install_reason, reboot_required and
every depends row along the way. Both halves are fixed — the re-register now
carries what it cannot restate, and the writers stamp the column so an
unchanged package is a genuine no-op rather than a re-register that merely
happens to be harmless now.

Also covered here:

  - the archive writer's manifest carries the package's REAL description. It
    used to state a fixed placeholder, and import parses that line straight
    back into installed.description, so a re-register replaced the archive's
    description with the placeholder the installer had invented.
  - the helper path writes a text manifest at all. It was the one install path
    that wrote none, leaving a helper-installed package absent from
    /var/lib/igos/packages entirely, and it recorded no content hashes, so
    `pkm verify` on a proprietary app could confirm existence and nothing more.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pkm.database import PackageDB, _parse_manifest, _sha256
from pkm.installer import PackageInstaller


class ArchiveWriterProvenanceTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.root = self.tmp / "root"
        self.root.mkdir()
        self.db = PackageDB(self.tmp / "pkm.db", root=str(self.root))
        self.inst = PackageInstaller(self.db, root=str(self.root))

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def _payload(self, rel="usr/bin/demo", content=b"payload\n"):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        return rel, p

    def test_write_manifest_returns_the_hash_of_the_bytes_it_wrote(self):
        rel, _ = self._payload()
        got = self.inst._write_manifest("demo", "1.0", [rel], release=2)
        path = self.root / "var/lib/igos/packages/demo-1.0"
        self.assertEqual(got, _sha256(str(path)))

    def test_the_manifest_carries_the_real_description(self):
        rel, _ = self._payload()
        self.inst._write_manifest("demo", "1.0", [rel], release=2,
                                  description="a real package description")
        text = (self.root / "var/lib/igos/packages/demo-1.0").read_text()
        self.assertIn("demo: a real package description", text)
        self.assertNotIn("(installed via pkm)", text)
        self.assertEqual(_parse_manifest(text)["description"],
                         "a real package description")

    def test_without_a_description_the_placeholder_stays(self):
        """Honest about knowing nothing rather than inventing something."""
        rel, _ = self._payload()
        self.inst._write_manifest("demo", "1.0", [rel])
        text = (self.root / "var/lib/igos/packages/demo-1.0").read_text()
        self.assertIn("(installed via pkm)", text)

    def test_a_stamped_row_survives_a_corpus_wide_import_untouched(self):
        """The end-to-end property the stamp exists for."""
        rel, p = self._payload()
        pkg_id = self.db.add_installed(
            name="demo", version="1.0", release=5, tier="extra",
            license_="MIT", install_method="archive",
            archive_path="/var/lib/igos/archives/demo-1.0.igos.tar.gz",
            install_reason="dependency", reboot_required=1,
        )
        self.db.add_files(pkg_id, [rel], hashes={rel: _sha256(str(p))})
        self.db.add_depends(pkg_id, [("libc", "runtime")])
        sha = self.inst._write_manifest(
            "demo", "1.0", [rel], hashes={rel: _sha256(str(p))},
            release=5, description="a real package description")
        self.db.set_manifest_sha256("demo", sha)

        mdir = self.root / "var/lib/igos/packages"
        self.assertEqual(self.db.import_manifests(mdir), 0)
        row = self.db.get_installed("demo")
        self.assertEqual(row["tier"], "extra")
        self.assertEqual(row["install_method"], "archive")
        self.assertEqual(row["reboot_required"], 1)
        self.assertEqual(len(self.db.get_depends("demo")), 1)


class HelperWriterProvenanceTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.root = self.tmp / "root"
        self.root.mkdir()
        self.db = PackageDB(self.tmp / "pkm.db", root=str(self.root))

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def _deposit(self, rel, content=b"binary\n"):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        return p

    def _run(self, manifest):
        inst = PackageInstaller(self.db, root=str(self.root))
        ok_proc = MagicMock(returncode=0)
        with patch("pkm.installer.subprocess.run", return_value=ok_proc), \
             patch("pkm.installer._read_helper_manifest",
                   return_value=(manifest, None)):
            return inst._run_helper("vscode", self.tmp / "igos-install-vscode")

    def test_the_helper_path_writes_a_text_manifest(self):
        p = self._deposit("usr/share/code/code")
        ok, msg, _ = self._run({
            "version_installed": "1.96.2", "release_installed": 2,
            "files": ["/usr/share/code/code"], "symlinks": [], "depends": [],
        })
        self.assertTrue(ok, msg)
        path = self.root / "var/lib/igos/packages/vscode-1.96.2"
        self.assertTrue(path.exists(), "no text manifest was written")
        parsed = _parse_manifest(path.read_text())
        self.assertEqual(parsed["name"], "vscode")
        self.assertEqual(parsed["release"], 2)
        self.assertIn("usr/share/code/code", parsed["files"])
        self.assertEqual(parsed["file_hashes"]["usr/share/code/code"],
                         _sha256(str(p)))

    def test_the_helper_path_records_content_hashes_on_the_rows(self):
        p = self._deposit("usr/share/code/code")
        ok, msg, _ = self._run({
            "version_installed": "1.96.2",
            "files": ["/usr/share/code/code"], "symlinks": [], "depends": [],
        })
        self.assertTrue(ok, msg)
        got = self.db.get_file_checksums("vscode")
        self.assertEqual(got["usr/share/code/code"], _sha256(str(p)))
        # With a hash on record, a later change is detectable.
        p.write_bytes(b"tampered\n")
        self.assertIn("usr/share/code/code",
                      self.db.verify_package("vscode")["modified"])

    def test_the_manifest_is_a_no_op_for_a_later_import(self):
        self._deposit("usr/share/code/code")
        self._run({"version_installed": "1.96.2",
                   "files": ["/usr/share/code/code"], "symlinks": [],
                   "depends": []})
        mdir = self.root / "var/lib/igos/packages"
        self.assertEqual(self.db.import_manifests(mdir), 0)
        self.assertEqual(self.db.get_installed("vscode")["install_method"],
                         "helper")

    def test_a_merged_row_manifests_its_whole_footprint(self):
        """The infra files installed first must not fall out of the manifest.

        `pkm install <app>` installs the app's infra archive and then merges
        the helper footprint into that same row, so the row owns both sets
        while the helper reports only its own half. A manifest built from the
        helper's half alone would understate what the package owns, and the
        next import would re-register the row from that understatement.
        """
        self._deposit("usr/bin/igos-install-vscode")
        self._deposit("usr/share/code/code")
        infra_id = self.db.add_installed("vscode", "0", tier="extra")
        self.db.add_files(infra_id, ["usr/bin/igos-install-vscode"])

        ok, msg, _ = self._run({
            "version_installed": "1.96.2",
            "files": ["/usr/share/code/code"], "symlinks": [], "depends": [],
        })
        self.assertTrue(ok, msg)
        parsed = _parse_manifest(
            (self.root / "var/lib/igos/packages/vscode-1.96.2").read_text())
        self.assertIn("usr/bin/igos-install-vscode", parsed["files"])
        self.assertIn("usr/share/code/code", parsed["files"])


if __name__ == "__main__":
    unittest.main()
