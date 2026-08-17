"""Merged-/usr root-alias normalization in _read_helper_manifest.

A helper that deposits under a merged-usr root alias (/lib, /bin, /sbin ->
/usr/...) records the alias path it copied to. The manifest reader must
normalize that leading root to its canonical /usr/... target BEFORE the
path-prefix allowlist check, so the alias is accepted (it IS under /usr) and
pkm files/verify/remove track the real on-disk path — WITHOUT widening the
four accepted prefixes. Surfaced live by Steam's lib/udev/rules.d payload:
`/lib/udev/rules.d/60-steam-*.rules` was refused though /lib -> usr/lib, which
left the whole Steam client footprint untracked.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pkm.installer  # noqa: E402
from pkm.installer import _normalize_merged_usr_root, _read_helper_manifest  # noqa: E402

_MERGED_USR = os.path.islink("/lib") and os.path.realpath("/lib").startswith("/usr/")


class TestNormalizeMergedUsrRoot(unittest.TestCase):
    @unittest.skipUnless(_MERGED_USR, "not a merged-usr host")
    def test_lib_alias_resolved_into_usr(self):
        got = _normalize_merged_usr_root("/lib/udev/rules.d/60-steam-vr.rules")
        self.assertEqual(
            got, os.path.realpath("/lib") + "/udev/rules.d/60-steam-vr.rules")
        self.assertTrue(got.startswith("/usr/"))

    @unittest.skipUnless(_MERGED_USR, "not a merged-usr host")
    def test_bin_and_sbin_aliases_resolved(self):
        for root in ("bin", "sbin"):
            if not os.path.islink("/" + root):
                continue
            got = _normalize_merged_usr_root(f"/{root}/foo")
            self.assertEqual(got, os.path.realpath("/" + root) + "/foo")
            self.assertTrue(got.startswith("/usr/"))

    def test_canonical_usr_path_unchanged(self):
        for p in ("/usr/share/applications/steam.desktop",
                  "/etc/environment.d/x.conf", "/opt/app/bin/tool",
                  "/var/lib/intergen/legal/x.json"):
            self.assertEqual(_normalize_merged_usr_root(p), p)

    def test_non_alias_root_unchanged(self):
        # a root name not in the merged-usr set is left alone (fail-closed:
        # a real path outside /usr stays outside the allowlist).
        self.assertEqual(_normalize_merged_usr_root("/data/x"), "/data/x")

    def test_non_symlink_root_unchanged(self):
        # when a root name is a REAL dir (not a symlink) it must not be
        # rewritten — the file genuinely lives outside /usr.
        for root in ("lib", "lib64", "bin", "sbin"):
            if os.path.exists("/" + root) and not os.path.islink("/" + root):
                self.assertEqual(
                    _normalize_merged_usr_root(f"/{root}/x"), f"/{root}/x")

    def test_relative_and_short_paths_unchanged(self):
        self.assertEqual(_normalize_merged_usr_root("lib/x"), "lib/x")
        self.assertEqual(_normalize_merged_usr_root("/lib"), "/lib")
        self.assertEqual(_normalize_merged_usr_root(""), "")
        self.assertEqual(_normalize_merged_usr_root(None), None)


class TestReadManifestMergedUsr(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.manifest_dir = Path(self._tmp.name) / "helpers"
        self.manifest_dir.mkdir()
        self._p = patch.object(
            pkm.installer, "HELPER_MANIFEST_DIR", self.manifest_dir)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        self._tmp.cleanup()

    def _read(self, name, files):
        manifest = {
            "version": 1, "name": name, "version_installed": "1.0",
            "files": files, "symlinks": [], "depends": [],
            "post_install_actions_log": [],
        }
        (self.manifest_dir / f"{name}.manifest").write_text(json.dumps(manifest))
        return _read_helper_manifest(name)

    @unittest.skipUnless(_MERGED_USR, "not a merged-usr host")
    def test_merged_usr_path_accepted_and_canonicalized(self):
        # absent files are tolerated by the ELF re-audit, so no deposit is
        # needed to exercise the normalize->allowlist->store path.
        manifest, err = self._read("h_steam", ["/lib/udev/rules.d/60-steam-vr.rules"])
        self.assertIsNone(err)
        self.assertTrue(manifest["files"][0].startswith("/usr/lib/"))

    def test_genuinely_outside_path_still_refused(self):
        manifest, err = self._read("h_bad", ["/data/outside/thing"])
        self.assertIsNone(manifest)
        self.assertIn("outside helper-manifest allowlist", err)


if __name__ == "__main__":
    unittest.main()
