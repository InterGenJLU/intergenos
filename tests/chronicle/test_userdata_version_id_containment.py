#!/usr/bin/env python3
"""A stored user-data version id is joined to the store root as a path segment,
and remove_version_tree deletes whatever that path names. These tests pin the
guard: only the canonical id shape is accepted, the joined path must resolve to
a direct child of the userdata directory, and a rejected id never removes
anything — including when it arrives through a manifest file on disk.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from chronicle import manifest as _manifest
from chronicle import paths as _paths
from chronicle import userdata as _userdata

CANON = "0000000007-0123456789ab"


class VersionIdShapeTest(unittest.TestCase):
    def test_canonical_shape_accepted(self):
        self.assertTrue(_manifest.is_canonical_version_id(CANON))

    def test_non_canonical_shapes_refused(self):
        for bad in ("..", ".", "", "/", "../x", "0000000007-0123456789ab/..",
                    "0000000007-0123456789AB", "7-0123456789ab", "0000000007-0123",
                    "0000000007-0123456789ab\n", None, 7):
            with self.subTest(bad=bad):
                self.assertFalse(_manifest.is_canonical_version_id(bad))


class UserdataTreeContainmentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chronicle-test-")
        self.root = Path(self.tmp) / "target"
        (self.root / "userdata" / CANON).mkdir(parents=True)
        (self.root / "userdata" / CANON / "keep.txt").write_text("k")
        (self.root / "sentinel.txt").write_text("store root content")

    def test_canonical_id_maps_to_direct_child(self):
        self.assertEqual(_userdata.userdata_tree(self.root, CANON),
                         self.root / "userdata" / CANON)

    def test_dot_pair_refused_and_nothing_removed(self):
        with self.assertRaises(ValueError):
            _userdata.userdata_tree(self.root, "..")
        with self.assertRaises(ValueError):
            _userdata.remove_version_tree(self.root, "..")
        self.assertTrue((self.root / "sentinel.txt").exists())
        self.assertTrue((self.root / "userdata" / CANON / "keep.txt").exists())

    def test_slash_and_nested_ids_refused(self):
        for bad in ("../..", "x/../..", "0000000007-0123456789ab/../.."):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    _userdata.remove_version_tree(self.root, bad)
        self.assertTrue((self.root / "sentinel.txt").exists())

    def test_symlinked_version_dir_not_removed(self):
        # A canonical-looking name that is a symlink out of the store must not
        # be followed into a deletion.
        link_id = "0000000009-abcdefabcdef"
        outside = Path(self.tmp) / "outside"
        outside.mkdir()
        (outside / "precious.txt").write_text("p")
        os.symlink(outside, self.root / "userdata" / link_id)
        with self.assertRaises(ValueError):
            _userdata.remove_version_tree(self.root, link_id)
        self.assertTrue((outside / "precious.txt").exists())

    def test_symlinked_userdata_directory_is_refused(self):
        # Review finding G1: when the store's own userdata directory is a
        # symlink to an outside directory holding a canonical-id child, both
        # sides of the resolved-parent comparison resolve outside the store.
        # The layout is refused before any path is returned or removed.
        outside = Path(self.tmp) / "outside-store"
        (outside / CANON).mkdir(parents=True)
        (outside / CANON / "precious.txt").write_text("p")
        store = Path(self.tmp) / "store2"
        store.mkdir()
        os.symlink(outside, store / "userdata")
        with self.assertRaises(ValueError):
            _userdata.userdata_tree(store, CANON)
        with self.assertRaises(ValueError):
            _userdata.remove_version_tree(store, CANON)
        self.assertTrue((outside / CANON / "precious.txt").exists())

    def test_real_version_tree_is_removed_and_neighbours_survive(self):
        other = "0000000008-fedcba987654"
        (self.root / "userdata" / other).mkdir()
        (self.root / "userdata" / other / "stay.txt").write_text("s")
        _userdata.remove_version_tree(self.root, CANON)
        self.assertFalse((self.root / "userdata" / CANON).exists())
        self.assertTrue((self.root / "userdata" / other / "stay.txt").exists())
        self.assertTrue((self.root / "sentinel.txt").exists())
        # Removing an absent version is a no-op, not an error.
        _userdata.remove_version_tree(self.root, CANON)

    def test_manifest_with_escaping_id_is_not_listed(self):
        vdir = _paths.versions_dir(self.root, _paths.LAYER_USER_DATA)
        vdir.mkdir(parents=True, exist_ok=True)
        good = {"layer": _paths.LAYER_USER_DATA, "version_id": CANON, "sequence": 7, "entries": []}
        (vdir / f"{CANON}.json").write_text(json.dumps(good))
        bad = {"layer": _paths.LAYER_USER_DATA, "version_id": "..", "sequence": 8, "entries": []}
        (vdir / "0000000008-ffffffffffff.json").write_text(json.dumps(bad))
        listed = [m["version_id"] for m in _manifest.list_versions(self.root, _paths.LAYER_USER_DATA)]
        self.assertEqual(listed, [CANON])


if __name__ == "__main__":
    unittest.main()
