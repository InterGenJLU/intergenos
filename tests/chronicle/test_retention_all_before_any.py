#!/usr/bin/env python3
"""Retention removes versions all-before-any, fail-loud, and announced.

Three properties of a prune plan, each pinned against the engine and the
user-data layer (the only layer whose versions own an on-disk tree):

  * ALL BEFORE ANY — every candidate is checked with the non-mutating
    userdata.check_version_removal before the first version is dropped; one
    refused candidate refuses the whole plan and nothing is removed.
  * FAIL-LOUD, VERIFIED — a tree removal that fails stops retention with an
    error naming the version; the manifest is unlinked only after its tree is
    verifiably gone, so a failed removal leaves a still-listed version that the
    next retention pass plans again — never an orphan behind a deleted
    manifest.
  * ANNOUNCED — the plan is recorded in persistent engine state before the
    first removal and its outcome after; status() exposes the record.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from chronicle import engine as _engine
from chronicle import escalate as _escalate
from chronicle import paths as _paths
from chronicle import retention as _retention
from chronicle import userdata as _userdata

CANON = "0000000007-0123456789ab"
OTHER = "0000000008-fedcba987654"


def _touch_tree(root, vid, name="keep.txt"):
    tree = root / "userdata" / vid
    tree.mkdir(parents=True, exist_ok=True)
    (tree / name).write_text("k")
    return tree


class CheckVersionRemovalTest(unittest.TestCase):
    """check_version_removal performs every check remove_version_tree performs
    and never touches the store."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chronicle-test-")
        self.root = Path(self.tmp) / "target"
        _touch_tree(self.root, CANON)
        (self.root / "sentinel.txt").write_text("store root content")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _store_intact(self):
        self.assertTrue((self.root / "sentinel.txt").exists())
        self.assertTrue((self.root / "userdata" / CANON / "keep.txt").exists())

    def test_real_tree_passes_and_is_untouched(self):
        _userdata.check_version_removal(self.root, CANON)
        self._store_intact()

    def test_absent_version_passes(self):
        _userdata.check_version_removal(self.root, OTHER)
        self._store_intact()

    def test_non_canonical_id_refused(self):
        for bad in ("..", ".", "", "x/../..", CANON + "\n"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    _userdata.check_version_removal(self.root, bad)
        self._store_intact()

    def test_symlinked_version_entry_refused_and_target_intact(self):
        link_id = "0000000009-abcdefabcdef"
        outside = Path(self.tmp) / "outside"
        outside.mkdir()
        (outside / "precious.txt").write_text("p")
        os.symlink(outside, self.root / "userdata" / link_id)
        with self.assertRaises(ValueError):
            _userdata.check_version_removal(self.root, link_id)
        self.assertTrue((outside / "precious.txt").exists())
        self.assertTrue((self.root / "userdata" / link_id).is_symlink())
        self._store_intact()

    def test_non_directory_entry_refused(self):
        file_id = "0000000010-0a0a0a0a0a0a"
        (self.root / "userdata" / file_id).write_text("not a tree")
        with self.assertRaises(ValueError):
            _userdata.check_version_removal(self.root, file_id)
        self.assertTrue((self.root / "userdata" / file_id).is_file())
        self._store_intact()

    def test_symlinked_userdata_directory_refused(self):
        outside = Path(self.tmp) / "outside-store"
        (outside / CANON).mkdir(parents=True)
        (outside / CANON / "precious.txt").write_text("p")
        store = Path(self.tmp) / "store2"
        store.mkdir()
        os.symlink(outside, store / "userdata")
        with self.assertRaises(ValueError):
            _userdata.check_version_removal(store, CANON)
        self.assertTrue((outside / CANON / "precious.txt").exists())

    def test_absent_userdata_directory_passes(self):
        store = Path(self.tmp) / "store3"
        store.mkdir()
        _userdata.check_version_removal(store, CANON)


class RemoveVersionTreeFailLoudTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chronicle-test-")
        self.root = Path(self.tmp) / "target"
        self.tree = _touch_tree(self.root, CANON)

    def tearDown(self):
        for dirpath, dirnames, _files in os.walk(self.tmp):
            for d in dirnames:
                os.chmod(os.path.join(dirpath, d), 0o700)
        shutil.rmtree(self.tmp, ignore_errors=True)

    @unittest.skipIf(os.geteuid() == 0, "root bypasses directory permissions")
    def test_failed_removal_raises_instead_of_returning(self):
        locked = self.tree / "locked"
        locked.mkdir()
        (locked / "held.txt").write_text("h")
        os.chmod(locked, 0o500)
        with self.assertRaises(OSError):
            _userdata.remove_version_tree(self.root, CANON)
        self.assertTrue((locked / "held.txt").exists())

    def test_removal_is_verified_absent(self):
        _userdata.remove_version_tree(self.root, CANON)
        self.assertFalse((self.root / "userdata" / CANON).exists())


class EngineAllBeforeAnyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chronicle-test-")
        self.local = os.path.join(self.tmp, "local")
        self.target = os.path.join(self.tmp, "target")
        os.makedirs(self.target, exist_ok=True)
        self.now = [1_000_000.0]
        self.eng = _engine.Engine(local_root=self.local,
                                  now_fn=lambda: self.now[0])
        _orig_cap = _escalate.has_cap_chown
        self.addCleanup(setattr, _escalate, "has_cap_chown", _orig_cap)
        _escalate.has_cap_chown = lambda *a, **k: True
        home = os.path.join(self.tmp, "home")
        os.makedirs(home, exist_ok=True)
        self.home_file = Path(home) / "a.txt"
        self.home_file.write_text("v1")
        self.eng.config.user_data_paths = [home]
        self.eng.target_adopt(self.target, target_class="directory")
        self.vids = []
        for i in range(3):
            self.home_file.write_text(f"v{i}")
            self.now[0] += 3600
            self.vids.append(self.eng.capture(
                _paths.LAYER_USER_DATA, reason=f"capture {i}")["version_id"])
        self.troot = Path(self.eng.target_root())
        # Thinning keeps only the newest version, so the plan prunes the two
        # older ones, oldest first.
        keep_newest = lambda versions, now: {self.vids[-1]}  # noqa: E731
        self.addCleanup(setattr, _retention, "thin_keep_user_data",
                        _retention.thin_keep_user_data)
        _retention.thin_keep_user_data = keep_newest

    def tearDown(self):
        for dirpath, dirnames, _files in os.walk(self.tmp):
            for d in dirnames:
                try:
                    os.chmod(os.path.join(dirpath, d), 0o700)
                except OSError:
                    pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _tree(self, vid):
        return self.troot / "userdata" / vid

    def _manifest_path(self, vid):
        return _paths.versions_dir(self.troot, _paths.LAYER_USER_DATA) / f"{vid}.json"

    def _listed(self):
        return [m["version_id"] for m in self.eng.list_versions(_paths.LAYER_USER_DATA)]

    def _events(self):
        return self.eng.status()["retention_events"]

    def _events_on_disk(self):
        data = json.loads(_paths.state_path(self.local).read_text())
        return data.get("retention_events", [])

    def test_one_refused_candidate_refuses_the_whole_plan(self):
        v0, v1, v2 = self.vids
        # The SECOND prune candidate's tree is swapped for a symlink to an
        # outside directory; the first candidate is a real tree.
        outside = Path(self.tmp) / "outside"
        outside.mkdir()
        (outside / "precious.txt").write_text("p")
        real = self._tree(v1)
        moved = Path(self.tmp) / "moved-aside"
        os.rename(real, moved)
        os.symlink(outside, real)
        with self.assertRaisesRegex(_engine.EngineError, "retention refused"):
            self.eng.retention_apply(_paths.LAYER_USER_DATA)
        # All before any: the first candidate was NOT dropped.
        self.assertTrue(self._tree(v0).is_dir(), "first candidate's tree survives")
        self.assertTrue(self._manifest_path(v0).exists(), "first candidate still listed")
        self.assertTrue(self._manifest_path(v1).exists())
        self.assertTrue((outside / "precious.txt").exists())
        self.assertEqual(self._listed(), [v0, v1, v2])
        kinds = [e["kind"] for e in self._events()]
        self.assertEqual(kinds, ["prune-refused"])
        self.assertEqual(self._events()[0]["version_ids"], [v0, v1])
        self.assertIn(v1, self._events()[0]["problems"][0])

    def test_plan_is_announced_before_the_first_removal(self):
        v0, v1, v2 = self.vids
        seen = []
        real_remove = _userdata.remove_version_tree

        def spy(target_root, version_id):
            seen.append([e["kind"] for e in self._events_on_disk()])
            return real_remove(target_root, version_id)

        self.addCleanup(setattr, _userdata, "remove_version_tree", real_remove)
        _userdata.remove_version_tree = spy
        out = self.eng.retention_apply(_paths.LAYER_USER_DATA)
        self.assertEqual(out["pruned"], [v0, v1])
        self.assertEqual(len(seen), 2)
        for kinds_at_removal in seen:
            self.assertIn("prune-announced", kinds_at_removal,
                          "the announcement is on disk before any removal")
        kinds = [e["kind"] for e in self._events()]
        self.assertEqual(kinds, ["prune-announced", "prune-completed"])
        announced = self._events()[0]
        self.assertEqual(announced["layer"], _paths.LAYER_USER_DATA)
        self.assertEqual(announced["version_ids"], [v0, v1])
        self.assertEqual(announced["reason"], "thinning")
        self.assertEqual(self._listed(), [v2])
        self.assertFalse(self._tree(v0).exists())
        self.assertFalse(self._tree(v1).exists())

    def test_empty_plan_records_nothing(self):
        _retention.thin_keep_user_data = lambda versions, now: set(self.vids)
        out = self.eng.retention_apply(_paths.LAYER_USER_DATA)
        self.assertEqual(out["pruned"], [])
        self.assertEqual(self._events(), [])

    @unittest.skipIf(os.geteuid() == 0, "root bypasses directory permissions")
    def test_failed_tree_removal_stops_loudly_and_keeps_the_manifest(self):
        v0, v1, v2 = self.vids
        locked = self._tree(v1) / "locked"
        locked.mkdir()
        (locked / "held.txt").write_text("h")
        os.chmod(locked, 0o500)
        with self.assertRaisesRegex(_engine.EngineError, v1):
            self.eng.retention_apply(_paths.LAYER_USER_DATA)
        # v0 (first in plan) was removed; v1 failed: its manifest MUST remain
        # so the version stays listed and is planned again.
        self.assertFalse(self._tree(v0).exists())
        self.assertFalse(self._manifest_path(v0).exists())
        self.assertTrue(self._manifest_path(v1).exists(),
                        "manifest survives a failed tree removal")
        self.assertEqual(self._listed(), [v1, v2])
        kinds = [e["kind"] for e in self._events()]
        self.assertEqual(kinds, ["prune-announced", "prune-stopped"])
        stopped = self._events()[-1]
        self.assertEqual(stopped["removed"], [v0])
        self.assertEqual(stopped["remaining"], [v1])
        self.assertIn(v1, stopped["error"])
        # Once the obstruction is gone the next pass completes the plan.
        os.chmod(locked, 0o700)
        out = self.eng.retention_apply(_paths.LAYER_USER_DATA)
        self.assertEqual(out["pruned"], [v1])
        self.assertEqual(self._listed(), [v2])
        self.assertFalse(self._tree(v1).exists())

    def test_status_carries_the_record_and_state_survives_reload(self):
        self.eng.retention_apply(_paths.LAYER_USER_DATA)
        reloaded = _engine.Engine(local_root=self.local,
                                  now_fn=lambda: self.now[0])
        kinds = [e["kind"] for e in reloaded.status()["retention_events"]]
        self.assertEqual(kinds, ["prune-announced", "prune-completed"])


if __name__ == "__main__":
    unittest.main()
