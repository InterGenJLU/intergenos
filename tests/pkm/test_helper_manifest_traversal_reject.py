"""Traversal-segment rejection in the helper-manifest allowlist.

The path-prefix allowlist is a string ``startswith`` test, so a ``..`` segment
could prefix-satisfy it while resolving outside every accepted prefix
(``/usr/../root/x`` startswith ``/usr/`` but lands in ``/root``) — and the DB,
verify, remove, and the ELF re-audit's realpath would then operate on the
laundered location. A first-party helper never legitimately records a ``..``
segment, so the reader refuses the manifest loudly (verify, don't normalize —
mask-vs-verify). Defense-in-depth: the manifest content derives from real
upstream payloads (e.g. Valve's .deb), so a hostile/buggy payload shape must
hit a checked gate, not a silent pass-through.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pkm.installer  # noqa: E402
from pkm.installer import _has_traversal_segment, _read_helper_manifest  # noqa: E402


class TestHasTraversalSegment(unittest.TestCase):
    def test_dotdot_segment_detected(self):
        for p in ("/usr/../root/x", "/usr/lib/../../etc/shadow",
                  "/etc/..", "..", "/var/lib/a/../b"):
            self.assertTrue(_has_traversal_segment(p), p)

    def test_clean_and_lookalike_paths_pass(self):
        # `..` must be a SEGMENT — dotfile/dotdot-prefixed NAMES are legal.
        for p in ("/usr/share/app/file", "/usr/share/..hidden",
                  "/opt/app/a..b", "/etc/x.d/y..conf", "/usr/share/...z"):
            self.assertFalse(_has_traversal_segment(p), p)

    def test_non_string_is_not_flagged_here(self):
        # non-strings are refused by the existing isinstance gate, not this one
        self.assertFalse(_has_traversal_segment(None))
        self.assertFalse(_has_traversal_segment(42))


class TestReadManifestTraversalReject(unittest.TestCase):
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

    def _read(self, name, files, symlinks=None):
        manifest = {
            "version": 1, "name": name, "version_installed": "1.0",
            "files": files, "symlinks": symlinks or [], "depends": [],
            "post_install_actions_log": [],
        }
        (self.manifest_dir / f"{name}.manifest").write_text(json.dumps(manifest))
        return _read_helper_manifest(name)

    def test_traversal_inside_allowed_prefix_refused(self):
        # RED shape: prefix-satisfies /usr/ but resolves to /root
        manifest, err = self._read("h_trav", ["/usr/../root/planted"])
        self.assertIsNone(manifest)
        self.assertIn("traversal", err)

    def test_traversal_via_merged_usr_alias_refused(self):
        # /lib/../x normalizes to /usr/lib/../x on a merged-usr host (alias
        # resolution runs first) and stays refused; on a non-merged host the
        # raw path fails the allowlist. Refused either way — no skip needed.
        manifest, err = self._read("h_alias", ["/lib/../root/planted"])
        self.assertIsNone(manifest)

    def test_symlink_path_traversal_refused(self):
        manifest, err = self._read(
            "h_link", ["/usr/share/app/ok"],
            symlinks=[{"path": "/usr/share/../../root/link", "target": "/usr/share/x"}])
        self.assertIsNone(manifest)
        self.assertIn("traversal", err)

    def test_clean_manifest_still_accepted(self):
        manifest, err = self._read(
            "h_ok", ["/usr/share/app/file", "/etc/app/x.conf"],
            symlinks=[{"path": "/usr/share/app/link", "target": "/usr/share/app/file"}])
        self.assertIsNone(err)
        self.assertEqual(len(manifest["files"]), 2)


if __name__ == "__main__":
    unittest.main()
