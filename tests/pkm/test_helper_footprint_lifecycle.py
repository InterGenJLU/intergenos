#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Helper-payload lifecycle: replace the footprint, and stop forcing the
untracked install path.

Four defects, all on the download-helper install path, all measured on
installed systems before being fixed here.

1. INGESTION ADDED INSTEAD OF REPLACING. Re-running a helper recorded the new
   payload's files beside the previous payload's rows. Files the new build
   dropped stayed in the database with the old build's checksums, so
   `pkm verify` reported a correct install as damaged, once per dropped file;
   and every path present in both builds gained a SECOND row, because the
   files table has no UNIQUE(package_id, path) for INSERT OR REPLACE to
   resolve against. Two different wrong end-states were observed across
   machines running the same update — one with the merged manifest and the
   ghost rows, one where no manifest for the new build was written at all —
   so the coverage here asserts the whole post-ingestion state rather than
   any single symptom.

2. THE SUPERSEDED TEXT MANIFEST WAS LEFT ON DISK. /var/lib/igos/packages then
   held two manifests for one package, and `pkm import` re-registers from
   every file it finds there in filename order — so which build the row ends
   up claiming depended on how two version strings sorted.

3. THE NON-INTERACTIVE REFUSAL IGNORED AN EXISTING ACCEPTANCE RECORD. pkm
   refused any headless install of a proprietary payload on a bare
   stdin.isatty() test, including on a machine whose acceptance record was
   already on disk. The only remaining headless path was running the install
   helper directly — the one path pkm cannot ingest, which is what produced
   defect 1's stale databases in the first place.

4. THE PAYLOAD BUILD WAS RECORDED ONLY IN `version`, the column that also
   carries the stub package's own identity, so any later re-registration from
   package metadata replaced the payload build silently.

Every test below fails on the tree these fixes landed against.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pkm.installer  # noqa: E402
from pkm.database import PackageDB  # noqa: E402
from pkm.installer import PackageInstaller  # noqa: E402


def acceptance_record_exists(*args, **kwargs):
    """Reach the real function through the module.

    Imported by name at module scope, its absence would be an ImportError at
    COLLECTION time, which aborts the whole file and reports one error instead
    of showing which behaviours are missing. Resolved per call, each test
    fails on its own statement and the failure names what is not there.
    """
    fn = getattr(pkm.installer, "acceptance_record_exists", None)
    if fn is None:
        raise AssertionError(
            "pkm.installer.acceptance_record_exists does not exist: the "
            "non-interactive gate cannot consult an acceptance record"
        )
    return fn(*args, **kwargs)

HELPER_LIB = REPO_ROOT / "packages/core/intergenos-helper-lib/helper-lib.sh"


class _HelperIngestBase(unittest.TestCase):
    """A temp root with a package DB, and a way to ingest a payload."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.root = self.tmp / "root"
        self.root.mkdir()
        self.db = PackageDB(self.tmp / "pkm.db", root=str(self.root))

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def _deposit(self, rel_paths, content="payload"):
        """Put real bytes on disk so ingestion records real checksums."""
        for rel in rel_paths:
            path = self.root / rel.lstrip("/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{content}:{rel}")

    def _ingest(self, version, files, name="vscode"):
        """Run one helper ingestion for `name` at payload build `version`."""
        self._deposit(files)
        manifest = {
            "version": 1, "name": name, "version_installed": version,
            "files": list(files), "symlinks": [], "depends": [],
            "post_install_actions_log": [],
        }
        inst = PackageInstaller(self.db, root=str(self.root))
        ok_proc = MagicMock(returncode=0)
        with patch("pkm.installer.subprocess.run", return_value=ok_proc), \
             patch("pkm.installer._read_helper_manifest",
                   return_value=(manifest, None)):
            ok, msg, declined = inst._run_helper(
                name, self.tmp / f"igos-install-{name}")
        self.assertTrue(ok, msg)
        return msg

    def _rows(self, name="vscode"):
        pkg_id = self.db.get_installed(name)["id"]
        return self.db.conn.execute(
            "SELECT path, source FROM files WHERE package_id = ?", (pkg_id,)
        ).fetchall()

    def _manifest_dir(self):
        return self.root / "var" / "lib" / "igos" / "packages"


class PayloadFootprintReplacedTest(_HelperIngestBase):

    def test_paths_the_new_build_dropped_are_no_longer_owned(self):
        # The measured shape: 1.131.0 shipped ten animation assets that
        # 1.132.0 does not. They stayed recorded, and verify called them
        # missing on a correct install.
        self._ingest("1.131.0", [
            "/opt/vscode/code",
            "/opt/vscode/resources/app/media/buddy-idle.gif",
            "/opt/vscode/resources/app/media/buddy-love.gif",
        ])
        self._ingest("1.132.0", [
            "/opt/vscode/code",
            "/opt/vscode/resources/app/media/buddy-idle.png",
        ])
        owned = {r[0] for r in self._rows()}
        self.assertNotIn("opt/vscode/resources/app/media/buddy-idle.gif", owned)
        self.assertNotIn("opt/vscode/resources/app/media/buddy-love.gif", owned)
        self.assertEqual(owned, {
            "opt/vscode/code",
            "opt/vscode/resources/app/media/buddy-idle.png",
        })

    def test_shared_paths_do_not_accumulate_duplicate_rows(self):
        for _ in range(3):
            self._ingest("1.132.0", ["/opt/vscode/code", "/opt/vscode/bin/code"])
        paths = [r[0] for r in self._rows()]
        self.assertEqual(sorted(paths), ["opt/vscode/bin/code", "opt/vscode/code"])
        self.assertEqual(len(paths), len(set(paths)), f"duplicate rows: {paths}")

    def test_archive_rows_survive_a_payload_replacement(self):
        # The stub package owns the helper binary and the vendor keyring under
        # the SAME package name. Replacing the payload must not take them.
        pkg_id = self.db.add_installed("vscode", "1.0", tier="extra")
        self.db.add_files(
            pkg_id, ["usr/bin/igos-install-vscode", "usr/share/igos/keyring.gpg"],
            source="archive")
        self._ingest("1.131.0", ["/opt/vscode/code"])
        self._ingest("1.132.0", ["/opt/vscode/code"])
        owned = {r[0] for r in self._rows()}
        self.assertIn("usr/bin/igos-install-vscode", owned)
        self.assertIn("usr/share/igos/keyring.gpg", owned)

    def test_unlabelled_legacy_rows_are_not_deleted(self):
        # Rows written before the source column existed carry NULL. They are
        # not provably payload rows, so replacement leaves them alone rather
        # than deleting ownership it cannot prove.
        pkg_id = self.db.add_installed("vscode", "1.0", tier="extra")
        self.db.conn.execute(
            "INSERT INTO files (package_id, path, is_dir, is_config, checksum) "
            "VALUES (?, ?, 0, 0, NULL)", (pkg_id, "opt/vscode/legacy-note"))
        self.db.conn.commit()
        self._ingest("1.132.0", ["/opt/vscode/code"])
        self.assertIn("opt/vscode/legacy-note", {r[0] for r in self._rows()})

    def test_verify_is_clean_after_a_payload_that_drops_files(self):
        # The end-to-end statement of defect 1, through the real verifier.
        from pkm.verifier import PackageVerifier
        self._ingest("1.131.0", ["/opt/vscode/code", "/opt/vscode/old-asset.gif"])
        os.remove(self.root / "opt/vscode/old-asset.gif")
        self._ingest("1.132.0", ["/opt/vscode/code"])
        result = PackageVerifier(self.db).verify("vscode")
        self.assertFalse(result["missing"],
                         f"verify reported missing files: {result['missing']}")


class ManifestSupersessionTest(_HelperIngestBase):

    def test_new_build_writes_its_manifest_and_the_old_one_is_gone(self):
        self._ingest("1.131.0", ["/opt/vscode/code"])
        self.assertTrue((self._manifest_dir() / "vscode-1.131.0").is_file())
        self._ingest("1.132.0", ["/opt/vscode/code"])
        present = sorted(p.name for p in self._manifest_dir().iterdir())
        self.assertIn("vscode-1.132.0", present)
        self.assertNotIn("vscode-1.131.0", present)

    def test_a_different_package_sharing_the_name_prefix_is_untouched(self):
        # Ownership is decided by reading the manifest, not by the filename —
        # "vscode-insiders-1.0" starts with "vscode-" and is NOT this package.
        self._ingest("1.131.0", ["/opt/vscode/code"])
        other = self._manifest_dir() / "vscode-insiders-1.0"
        other.write_text(
            "PACKAGE NAME: vscode-insiders-1.0\n"
            "PACKAGE VERSION: 1.0\n"
            "FILE LIST:\n"
            "opt/vscode-insiders/code\n"
        )
        self._ingest("1.132.0", ["/opt/vscode/code"])
        self.assertTrue(other.is_file(), "a different package's manifest was deleted")


class PayloadVersionTruthTest(_HelperIngestBase):

    def test_payload_build_is_recorded_in_its_own_column(self):
        pkg_id = self.db.add_installed("vscode", "1.0", tier="extra")
        self.assertIsNone(self.db.get_installed("vscode")["payload_version"])
        self._ingest("1.132.0-1785860022", ["/opt/vscode/code"])
        row = self.db.get_installed("vscode")
        self.assertEqual(row["id"], pkg_id)
        self.assertEqual(row["payload_version"], "1.132.0-1785860022")

    def test_payload_build_survives_a_reregistration_from_stub_metadata(self):
        # The failure this column exists to stop: something re-registers the
        # row from the stub's own metadata and the payload answer disappears.
        self._ingest("1.132.0-1785860022", ["/opt/vscode/code"])
        self.db.conn.execute(
            "UPDATE installed SET version = '1.0' WHERE name = 'vscode'")
        self.db.conn.commit()
        self.assertEqual(
            self.db.get_installed("vscode")["payload_version"],
            "1.132.0-1785860022")

    def test_info_prints_the_payload_build(self):
        import argparse
        import io
        from contextlib import redirect_stdout
        import pkm.cli as cli
        self._ingest("1.132.0-1785860022", ["/opt/vscode/code"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.cmd_info(self.db, argparse.Namespace(package="vscode"))
        self.assertIn("payload_version", buf.getvalue())
        self.assertIn("1.132.0-1785860022", buf.getvalue())


class AcceptanceRecordGateTest(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.legal = Path(self._td.name) / "legal"
        self.legal.mkdir()

    def tearDown(self):
        self._td.cleanup()

    def test_record_present_is_detected(self):
        (self.legal / "vscode-1.0-accepted.json").write_text("{}")
        self.assertTrue(acceptance_record_exists("vscode", record_dir=self.legal))

    def test_record_absent_is_detected(self):
        self.assertFalse(acceptance_record_exists("vscode", record_dir=self.legal))

    def test_another_packages_record_does_not_count(self):
        (self.legal / "chrome-1.0-accepted.json").write_text("{}")
        self.assertFalse(acceptance_record_exists("vscode", record_dir=self.legal))

    def test_a_future_acceptance_schema_still_counts(self):
        (self.legal / "vscode-2.0-accepted.json").write_text("{}")
        self.assertTrue(acceptance_record_exists("vscode", record_dir=self.legal))

    def test_missing_directory_is_not_acceptance(self):
        self.assertFalse(
            acceptance_record_exists("vscode", record_dir=self.legal / "nope"))


class HeadlessProprietaryInstallTest(unittest.TestCase):
    """The gate itself: refuse without a record, proceed with one."""

    def _call(self, *, isatty, has_record):
        import pkm.cli as cli
        self.assertTrue(
            hasattr(cli, "acceptance_record_exists"),
            "pkm.cli does not consult an acceptance record at all",
        )
        db = MagicMock()
        db.get_installed.return_value = None
        installer = MagicMock()
        installer._find_archive.return_value = None
        repo = MagicMock()
        repo.get_package.return_value = None
        reporter = MagicMock()
        stdin = MagicMock()
        stdin.isatty.return_value = isatty
        with patch("pkm.cli.sys.stdin", stdin), \
             patch("pkm.cli.payload_installed", return_value=False), \
             patch("pkm.cli.acceptance_record_exists", return_value=has_record):
            try:
                cli._proprietary_install(db, installer, repo, reporter,
                                         "vscode", "MS-EULA", replace=True)
            except SystemExit as exc:
                return "exit", exc.code, reporter
        return "returned", None, reporter

    def test_headless_without_a_record_still_refuses(self):
        outcome, code, reporter = self._call(isatty=False, has_record=False)
        self.assertEqual(outcome, "exit")
        self.assertEqual(code, 1)
        said = " ".join(str(c.args[0]) for c in reporter.error.call_args_list
                        if c.args)
        self.assertIn("acceptance record", said)

    def test_headless_with_a_record_gets_past_the_gate(self):
        outcome, code, reporter = self._call(isatty=False, has_record=True)
        # It must not exit at the gate. Whatever happens afterwards, the
        # refusal is what this asserts against.
        errors = " ".join(str(c.args[0]) for c in reporter.error.call_args_list
                          if c.args)
        self.assertNotIn("interactive terminal", errors)
        infos = " ".join(str(c.args[0]) for c in reporter.info.call_args_list
                         if c.args)
        self.assertNotIn("Installation cancelled", infos)
        self.assertIn("acceptance record", infos)


class DirectInvocationAdvisoryTest(unittest.TestCase):
    """The advisory has to print on the path that actually happens."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _init_helper(self, env_extra):
        env = dict(os.environ)
        env["IGOS_HELPER_MANIFEST_DIR"] = str(self.tmp)
        env.pop("PKM_HELPER_INVOCATION", None)
        env.update(env_extra)
        script = (
            f'. "{HELPER_LIB}"\n'
            'igos_helper_init vscode\n'
        )
        return subprocess.run(
            ["bash", "-c", script], env=env, capture_output=True, text=True)

    def test_direct_run_says_the_install_will_not_be_tracked(self):
        proc = self._init_helper({})
        out = proc.stdout + proc.stderr
        self.assertIn("NOT be recorded", out)
        self.assertIn("pkm reinstall vscode", out)

    def test_run_under_pkm_stays_quiet(self):
        proc = self._init_helper({"PKM_HELPER_INVOCATION": "1"})
        out = proc.stdout + proc.stderr
        self.assertNotIn("NOT be recorded", out)

    def test_pkm_marks_its_own_helper_runs(self):
        # The other half: pkm must actually set the variable, or the advisory
        # prints on every pkm install and stops meaning anything.
        db = PackageDB(self.tmp / "pkm.db", root=str(self.tmp / "root"))
        self.addCleanup(db.close)
        inst = PackageInstaller(db, root=str(self.tmp / "root"))
        seen = {}

        def _capture(cmd, env=None, **kwargs):
            seen.update(env or {})
            return MagicMock(returncode=1)

        with patch("pkm.installer.subprocess.run", side_effect=_capture):
            inst._run_helper("vscode", self.tmp / "igos-install-vscode")
        self.assertEqual(seen.get("PKM_HELPER_INVOCATION"), "1")


if __name__ == "__main__":
    unittest.main()
