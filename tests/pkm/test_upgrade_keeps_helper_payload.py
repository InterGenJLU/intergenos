#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Upgrading a download-helper package keeps the application it installed.

Measured on the reference laptop on 2026-09-03, on a fresh R001.2 install:
`pkm upgrade cuda-toolkit` (13.3.1-1 -> 13.3.1-5) exited 0 in 46 seconds with
"Upgraded cuda-toolkit to 13.3.1". Afterwards /opt/cuda — seven gigabytes the
package's installer script had fetched from NVIDIA and recorded on the same
package — was gone, the footprint manifest under /var/lib/igos/helpers/ was
gone, and the CUDA engine's llama-server no longer found libcudart. The
upgrade path removes every file the package row owns and then installs the
new archive; the download step is only ever asked about on install.

Three things are asserted here:

  1. the remover, asked to keep the helper payload, removes the archive's own
     files and leaves the helper-recorded files and the footprint manifest on
     disk (and, not asked, removes everything — a real removal is unchanged);
  2. the installer re-records a kept payload on a freshly installed row:
     helper-labelled file rows, dependencies, install_method 'helper', the
     release of the new archive kept, a text manifest written;
  3. the upgrade command asks the remover to keep the payload exactly when
     the package is a helper install whose application is present, and
     re-records it after the new archive lands; a helper package whose
     application was never installed is upgraded without starting a download
     and the person is told so.
"""

import argparse
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pkm.cli as cli
from pkm import output
from pkm.database import PackageDB
from pkm.installer import PackageInstaller
from pkm.remover import PackageRemover

NAME = "cuda-toolkit"


def _lay_down(root: Path, rel: str, content: bytes = b"x") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


class _HelperInstallOnDisk(unittest.TestCase):
    """A helper-installed package as the laptop had it: three archive files,
    a payload under opt/cuda recorded as the helper's, and the footprint
    manifest the helper library wrote."""

    ARCHIVE_FILES = ["usr/bin/igos-install-cuda-toolkit",
                     "usr/share/doc/cuda-toolkit/CUDA-TOOLKIT.md",
                     "var/lib/intergen/legal/.keep"]
    PAYLOAD_FILES = ["opt/cuda/bin/nvcc", "opt/cuda/lib64/libcudart.so.13",
                     "etc/ld.so.conf.d/cuda.conf"]

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.root = self.tmp / "root"
        self.root.mkdir()
        self.db = PackageDB(self.tmp / "pkm.db", root=str(self.root))
        for rel in self.ARCHIVE_FILES + self.PAYLOAD_FILES:
            _lay_down(self.root, rel)
        pkg_id = self.db.add_installed(NAME, "13.3.1", release=5,
                                       tier="compute", install_method="helper")
        self.db.add_files(pkg_id, self.ARCHIVE_FILES, source="archive")
        self.db.add_files(pkg_id, self.PAYLOAD_FILES, source="helper")
        self.manifest_dir = self.root / "var/lib/igos/helpers"
        self.manifest_dir.mkdir(parents=True)
        self.manifest = self.manifest_dir / f"{NAME}.manifest"
        self.manifest.write_text(json.dumps({
            "version": 1, "name": NAME, "version_installed": "13.3.1",
            "elf_class": "mixed",
            "files": ["/" + p for p in self.PAYLOAD_FILES],
            "symlinks": [], "depends": ["nvidia", "glibc"],
            "post_install_actions_log": [],
        }))

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def _present(self, rel):
        return (self.root / rel).exists()


class TheRemoverKeepsThePayloadWhenAsked(_HelperInstallOnDisk):

    def test_the_archive_files_go_and_the_payload_stays(self):
        remover = PackageRemover(self.db, root=str(self.root))
        ok, msg = remover.remove(NAME, force=True, run_pre_remove_hook=False,
                                 keep_helper_payload=True)
        self.assertTrue(ok, msg)
        for rel in self.ARCHIVE_FILES:
            self.assertFalse(self._present(rel), f"{rel} should be removed")
        for rel in self.PAYLOAD_FILES:
            self.assertTrue(self._present(rel), f"{rel} was deleted")
        self.assertTrue(self.manifest.is_file(),
                        "the footprint manifest was deleted")
        self.assertIsNone(self.db.get_installed(NAME),
                          "the package record must still be removed")

    def test_a_real_removal_still_removes_everything(self):
        remover = PackageRemover(self.db, root=str(self.root))
        ok, msg = remover.remove(NAME, force=True, run_pre_remove_hook=False)
        self.assertTrue(ok, msg)
        for rel in self.ARCHIVE_FILES + self.PAYLOAD_FILES:
            self.assertFalse(self._present(rel), f"{rel} should be removed")
        self.assertFalse(self.manifest.exists())


class TheInstallerReRecordsAKeptPayload(_HelperInstallOnDisk):

    def _upgrade_the_archive_half(self):
        remover = PackageRemover(self.db, root=str(self.root))
        ok, msg = remover.remove(NAME, force=True, run_pre_remove_hook=False,
                                 keep_helper_payload=True)
        self.assertTrue(ok, msg)
        # The new archive (release 6) lands: its three files and a fresh row.
        for rel in self.ARCHIVE_FILES:
            _lay_down(self.root, rel, b"new")
        pkg_id = self.db.add_installed(NAME, "13.3.1", release=6,
                                       tier="compute", install_method="archive")
        self.db.add_files(pkg_id, self.ARCHIVE_FILES, source="archive")

    def test_the_new_row_reads_like_a_fresh_helper_install(self):
        self._upgrade_the_archive_half()
        inst = PackageInstaller(self.db, root=str(self.root))
        ok, msg = inst.reattach_helper_payload(NAME)
        self.assertTrue(ok, msg)
        self.assertIn("Re-recorded", msg)
        row = self.db.get_installed(NAME)
        self.assertEqual(row["install_method"], "helper")
        self.assertEqual(row["release"], 6, "the new archive's release must survive")
        self.assertEqual(row["payload_version"], "13.3.1")
        files = self.db.get_files(NAME)
        by_path = {f["path"]: f for f in files}
        for rel in self.PAYLOAD_FILES:
            self.assertIn(rel, by_path, f"{rel} not recorded")
            self.assertEqual(by_path[rel]["source"], "helper")
        for rel in self.ARCHIVE_FILES:
            self.assertIn(rel, by_path)
            self.assertNotEqual(by_path[rel]["source"], "helper")
        deps = {d["name"] if isinstance(d, dict) else d[0]
                for d in self.db.get_depends(NAME)}
        self.assertTrue({"nvidia", "glibc"} <= deps, deps)
        self.assertTrue(
            (self.root / "var/lib/igos/packages" / f"{NAME}-13.3.1").is_file(),
            "the text manifest was not written")

    def test_a_stamped_release_in_the_manifest_still_wins(self):
        self._upgrade_the_archive_half()
        m = json.loads(self.manifest.read_text())
        m["release_installed"] = 7
        self.manifest.write_text(json.dumps(m))
        inst = PackageInstaller(self.db, root=str(self.root))
        ok, msg = inst.reattach_helper_payload(NAME)
        self.assertTrue(ok, msg)
        self.assertEqual(self.db.get_installed(NAME)["release"], 7)

    def test_a_missing_manifest_is_reported_not_hidden(self):
        self._upgrade_the_archive_half()
        self.manifest.unlink()
        inst = PackageInstaller(self.db, root=str(self.root))
        ok, msg = inst.reattach_helper_payload(NAME)
        self.assertFalse(ok)
        self.assertIn("sudo pkm install cuda-toolkit", msg)


# ---------------------------------------------------------------------------
# The upgrade command, with its collaborators stood in for.
# ---------------------------------------------------------------------------

class FakeRepo:
    def __init__(self, remote):
        self.remote = remote

    def get_package(self, name):
        return self.remote.get(name)

    def download_package(self, name, reporter=None):
        return True, f"/tmp/{name}.igos.tar.gz"

    def resolve_dependencies(self, name, db):
        return True, [name]


class FakeInstaller:
    def __init__(self):
        self.installed = []
        self.reattached = []

    def install(self, name, archive_path=None, expected_sha256=None,
                install_reason="manual", reporter=None, sidecars_out=None,
                queue=None):
        self.installed.append(name)
        return True, "Installed"

    def reattach_helper_payload(self, name):
        self.reattached.append(name)
        return True, f"Re-recorded {name}"


class FakeRemover:
    calls = []

    def __init__(self, db, root=None):
        self.db = db

    def remove(self, name, force=False, reporter=None, on_file=None,
               run_pre_remove_hook=True, run_post_remove_hook=None,
               keep_helper_payload=False):
        FakeRemover.calls.append({"name": name,
                                  "keep_helper_payload": keep_helper_payload})
        return True, f"Removed {name}"


class TheUpgradeCommandKeepsAndReRecordsThePayload(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.db = PackageDB(self.tmp / "pkm.db", root=str(self.tmp / "root"))
        self.remote = {
            NAME: {"name": NAME, "version": "13.3.1", "release": 6,
                   "sha256": "0" * 64, "size": 10, "depends": []},
        }
        FakeRemover.calls = []

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def _run(self, installer, install_method, payload_present, helper_present=True):
        self.db.add_installed(NAME, "13.3.1", release=5, tier="compute",
                              install_method=install_method)
        args = argparse.Namespace(
            packages=[NAME], upgrade_all=False, allow_downgrade=False,
            ignore_holds=False, upgrade_security_only=False,
            upgrade_allow_kernel_replace=False, assume_yes=True,
            upgrade_dry_run=False, quiet=False, verbose=False,
        )
        buf = io.StringIO()
        prior = output.process_level()
        output.set_process_level(output.NORMAL)
        output._process_reporter.stream = buf
        output._process_reporter.err_stream = buf
        try:
            with redirect_stdout(buf), \
                 patch.object(cli, "RepoManager", lambda *a, **k: FakeRepo(self.remote)), \
                 patch.object(cli, "PackageInstaller", lambda *a, **k: installer), \
                 patch("pkm.remover.PackageRemover", FakeRemover), \
                 patch.object(cli, "_confirm_upgrade", lambda _a: True), \
                 patch.object(cli, "_save_rollback_archive", lambda *a, **k: None), \
                 patch.object(cli, "refresh_available_updates_after_transaction", lambda db, **k: None), \
                 patch.object(cli, "_print_transaction_next_steps", lambda *a, **k: None), \
                 patch.object(cli, "helper_is_present", lambda n: helper_present), \
                 patch.object(cli, "helper_payload_present", lambda n: payload_present), \
                 patch("pkm.pretxn.run_pre_transaction_hook", lambda *a, **k: None):
                rc = cli.cmd_upgrade(self.db, args)
        finally:
            output.set_process_level(prior)
            output._process_reporter.stream = None
            output._process_reporter.err_stream = None
        return rc, buf.getvalue()

    def test_a_present_application_is_kept_and_re_recorded(self):
        installer = FakeInstaller()
        rc, out = self._run(installer, install_method="helper", payload_present=True)
        self.assertEqual(rc, 0, out)
        self.assertEqual(FakeRemover.calls[0]["keep_helper_payload"], True)
        self.assertEqual(installer.installed, [NAME])
        self.assertEqual(installer.reattached, [NAME],
                         "the kept payload was not re-recorded on the new row")
        self.assertIn("Upgraded cuda-toolkit", out)

    def test_an_application_never_installed_is_not_downloaded(self):
        installer = FakeInstaller()
        rc, out = self._run(installer, install_method="archive", payload_present=False)
        self.assertEqual(rc, 0, out)
        self.assertEqual(FakeRemover.calls[0]["keep_helper_payload"], False)
        self.assertEqual(installer.reattached, [])
        self.assertIn("only the installer was upgraded", out)
        self.assertIn("sudo pkm install cuda-toolkit", out)

    def test_an_ordinary_package_is_untouched_by_this(self):
        installer = FakeInstaller()
        rc, out = self._run(installer, install_method="archive",
                            payload_present=False, helper_present=False)
        self.assertEqual(rc, 0, out)
        self.assertEqual(FakeRemover.calls[0]["keep_helper_payload"], False)
        self.assertEqual(installer.reattached, [])
        self.assertNotIn("only the installer was upgraded", out)

    def test_a_failed_re_record_fails_the_transaction(self):
        installer = FakeInstaller()
        installer.reattach_helper_payload = lambda n: (False, "the footprint manifest for cuda-toolkit could not be read")
        rc, out = self._run(installer, install_method="helper", payload_present=True)
        self.assertEqual(rc, 1, out)
        self.assertIn("could not be read", " ".join(out.split()))


if __name__ == "__main__":
    unittest.main()
