#!/usr/bin/env python3
"""Directory targets stay within their physical-allocation cap."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from chronicle import cas as _cas
from chronicle import engine as _engine
from chronicle import manifest as _manifest
from chronicle import paths as _paths
from chronicle import userdata as _userdata


def _allocated_bytes(root):
    seen = set()
    total = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        paths = [dirpath]
        paths.extend(
            os.path.join(dirpath, name)
            for name in dirnames
            if os.path.islink(os.path.join(dirpath, name))
        )
        paths.extend(os.path.join(dirpath, name) for name in filenames)
        for path in paths:
            metadata = os.lstat(path)
            identity = (metadata.st_dev, metadata.st_ino)
            if identity not in seen:
                seen.add(identity)
                total += metadata.st_blocks * 512
    return total


class DirectoryTargetCapTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="chronicle-cap-")
        root = Path(self.tmp.name)
        self.local = root / "local"
        self.target = root / "target"
        self.target.mkdir()
        self.user_root = root / "user"
        self.user_root.mkdir()
        self.document = self.user_root / "document"
        self.now = [1_000_000]
        self.engine = _engine.Engine(
            local_root=self.local, now_fn=lambda: self.now[0]
        )
        self.engine.config.user_data_paths = [str(self.user_root)]
        self.engine.target_adopt(
            self.target, target_class="directory", cap_bytes=1_000_000_000
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _set_cap(self, cap):
        self.engine.state["target"]["cap_bytes"] = cap
        self.engine._save_state()

    def _capture_user_data(self, reason):
        return self.engine.capture(
            _paths.LAYER_USER_DATA, reason=reason
        )["version_id"]

    def _versions(self, layer):
        return _manifest.list_versions(self.engine.target_root(), layer)

    def test_first_oversized_capture_is_rejected_and_cleaned(self):
        baseline = _allocated_bytes(self.engine.target_root())
        cap = baseline + 16 * 1024
        self._set_cap(cap)
        self.document.write_bytes(b"A" * (256 * 1024))

        with self.assertRaisesRegex(_engine.EngineError, "directory target cap"):
            self._capture_user_data("too large")

        self.assertEqual(self._versions(_paths.LAYER_USER_DATA), [])
        self.assertLessEqual(_allocated_bytes(self.engine.target_root()), cap)

    def test_oldest_unpinned_version_is_pruned_before_capture_returns(self):
        self.document.write_bytes(b"A" * (256 * 1024))
        first = self._capture_user_data("first")
        usage = _allocated_bytes(self.engine.target_root())
        cap = usage + 32 * 1024
        self._set_cap(cap)
        self.now[0] += 60
        self.document.write_bytes(b"B" * (256 * 1024))

        second = self._capture_user_data("second")

        versions = [m["version_id"] for m in self._versions(_paths.LAYER_USER_DATA)]
        self.assertNotIn(first, versions)
        self.assertEqual(versions, [second])
        self.assertLessEqual(_allocated_bytes(self.engine.target_root()), cap)
        events = self.engine.status()["retention_events"]
        self.assertEqual(
            [(event["kind"], event["reason"], event["version_ids"])
             for event in events[-2:]],
            [
                ("prune-announced", "cap", [first]),
                ("prune-completed", "cap", [first]),
            ],
        )

    def test_pinned_history_blocks_capture_without_partial_pruning(self):
        self.document.write_bytes(b"A" * (256 * 1024))
        first = self._capture_user_data("pinned")
        self.engine.pin(first)
        usage = _allocated_bytes(self.engine.target_root())
        cap = usage + 32 * 1024
        self._set_cap(cap)
        self.now[0] += 60
        self.document.write_bytes(b"B" * (256 * 1024))

        with self.assertRaisesRegex(_engine.EngineError, "pinned versions"):
            self._capture_user_data("blocked")

        versions = [m["version_id"] for m in self._versions(_paths.LAYER_USER_DATA)]
        self.assertEqual(versions, [first])
        self.assertLessEqual(_allocated_bytes(self.engine.target_root()), cap)

    def test_pin_block_preserves_earlier_unpinned_history(self):
        self.document.write_bytes(b"A" * (128 * 1024))
        first = self._capture_user_data("oldest unpinned")
        self.now[0] += 60
        self.document.write_bytes(b"B" * (128 * 1024))
        pinned = self._capture_user_data("newer pinned")
        self.engine.pin(pinned)
        usage = _allocated_bytes(self.engine.target_root())
        cap = usage + 32 * 1024
        self._set_cap(cap)
        self.now[0] += 60
        self.document.write_bytes(b"C" * (256 * 1024))

        with self.assertRaisesRegex(_engine.EngineError, "pinned versions"):
            self._capture_user_data("blocked after unpinned candidate")

        versions = [m["version_id"] for m in self._versions(_paths.LAYER_USER_DATA)]
        self.assertEqual(versions, [first, pinned])
        self.assertLessEqual(_allocated_bytes(self.engine.target_root()), cap)

    def test_cap_plan_refusal_preserves_every_prior_version(self):
        self.document.write_bytes(b"A" * (64 * 1024))
        first = self._capture_user_data("first")
        self.now[0] += 60
        self.document.write_bytes(b"B" * (64 * 1024))
        refused = self._capture_user_data("will be refused")
        self.now[0] += 60
        self.document.write_bytes(b"C" * (256 * 1024))
        latest = self._capture_user_data("latest")

        target_root = self.engine.target_root()
        refused_tree = _userdata.userdata_tree(target_root, refused)
        latest_tree = _userdata.userdata_tree(target_root, latest)
        shutil.rmtree(refused_tree)
        os.symlink(latest_tree, refused_tree)
        usage = _allocated_bytes(target_root)
        cap = usage + 32 * 1024
        self._set_cap(cap)
        self.now[0] += 60
        self.document.write_bytes(b"D" * (256 * 1024))

        with self.assertRaisesRegex(
            _engine.EngineError, "retention refused, nothing removed"
        ):
            self._capture_user_data("cap plan must be all-before-any")

        versions = [m["version_id"] for m in self._versions(_paths.LAYER_USER_DATA)]
        self.assertEqual(versions, [first, refused, latest])
        self.assertTrue(refused_tree.is_symlink())
        self.assertLessEqual(_allocated_bytes(target_root), cap)
        refused_events = [
            event for event in self.engine.status()["retention_events"]
            if event["kind"] == "prune-refused" and event["reason"] == "cap"
        ]
        self.assertEqual(len(refused_events), 1)
        self.assertIn(refused, refused_events[0]["version_ids"])

    def test_impossible_capture_preserves_unpinned_history(self):
        self.document.write_bytes(b"A" * (64 * 1024))
        first = self._capture_user_data("existing history")
        usage = _allocated_bytes(self.engine.target_root())
        cap = usage + 16 * 1024
        self._set_cap(cap)
        self.now[0] += 60
        self.document.write_bytes(b"B" * (512 * 1024))

        with self.assertRaisesRegex(_engine.EngineError, "minimum projected use"):
            self._capture_user_data("cannot fit even alone")

        versions = [m["version_id"] for m in self._versions(_paths.LAYER_USER_DATA)]
        self.assertEqual(versions, [first])
        self.assertLessEqual(_allocated_bytes(self.engine.target_root()), cap)

    def test_unchanged_hardlinks_are_counted_once(self):
        self.document.write_bytes(b"A" * (256 * 1024))
        first = self._capture_user_data("first")
        usage = _allocated_bytes(self.engine.target_root())
        cap = usage + 64 * 1024
        self._set_cap(cap)
        self.now[0] += 3600

        second = self._capture_user_data("unchanged")

        versions = [m["version_id"] for m in self._versions(_paths.LAYER_USER_DATA)]
        self.assertEqual(versions, [first, second])
        self.assertLessEqual(_allocated_bytes(self.engine.target_root()), cap)

    def test_retention_enforces_a_lowered_cap(self):
        self.document.write_bytes(b"A" * (256 * 1024))
        first = self._capture_user_data("first")
        self.now[0] += 3600
        self.document.write_bytes(b"B" * (256 * 1024))
        second = self._capture_user_data("second")
        usage = _allocated_bytes(self.engine.target_root())
        self._set_cap(usage - 128 * 1024)

        self.engine.retention_apply(_paths.LAYER_USER_DATA)

        versions = [m["version_id"] for m in self._versions(_paths.LAYER_USER_DATA)]
        self.assertNotIn(first, versions)
        self.assertIn(second, versions)
        self.assertLessEqual(
            _allocated_bytes(self.engine.target_root()),
            self.engine.state["target"]["cap_bytes"],
        )

    def test_oversized_cas_mirror_leaves_no_target_manifest_or_blob(self):
        baseline = _allocated_bytes(self.engine.target_root())
        cap = baseline + 16 * 1024
        self._set_cap(cap)
        config_root = Path(self.tmp.name) / "config"
        config_root.mkdir()
        config_file = config_root / "setting"
        config_file.write_bytes(b"C" * (256 * 1024))

        with self.assertRaisesRegex(_engine.EngineError, "directory target cap"):
            self.engine.capture(
                _paths.LAYER_CONFIG_STATE,
                scope=[str(config_root)],
                reason="mirror too large",
            )

        self.assertEqual(self._versions(_paths.LAYER_CONFIG_STATE), [])
        self.assertLessEqual(_allocated_bytes(self.engine.target_root()), cap)

    def test_cas_orphan_is_collected_before_valid_history_is_pruned(self):
        self.document.write_bytes(b"A" * (64 * 1024))
        first = self._capture_user_data("valid history")
        target_root = self.engine.target_root()
        target_store = _cas.ContentStore(target_root)
        orphan = target_store.put_bytes(b"O" * (256 * 1024))
        self._set_cap(_allocated_bytes(target_root))
        config_root = Path(self.tmp.name) / "small-config"
        config_root.mkdir()
        (config_root / "setting").write_bytes(b"x")

        self.engine.capture(
            _paths.LAYER_CONFIG_STATE,
            scope=[str(config_root)],
            reason="small mirror",
        )

        versions = [m["version_id"] for m in self._versions(_paths.LAYER_USER_DATA)]
        self.assertEqual(versions, [first])
        self.assertFalse(target_store.exists(orphan))
        self.assertLessEqual(
            _allocated_bytes(target_root), self.engine.state["target"]["cap_bytes"]
        )
        self.assertFalse(any(
            event["reason"] == "cap"
            and first in event["version_ids"]
            for event in self.engine.status()["retention_events"]
        ))

    def test_allocation_scan_errors_are_not_treated_as_empty_space(self):
        target_root = self.engine.target_root()
        blocked = target_root / "blocked"
        blocked.mkdir()
        (blocked / "payload").write_bytes(b"D" * (64 * 1024))
        real_scandir = os.scandir

        def fail_blocked(path):
            if os.fspath(path) == os.fspath(blocked):
                raise OSError("injected allocation scan failure")
            return real_scandir(path)

        with mock.patch.object(_engine.os, "scandir", side_effect=fail_blocked):
            with self.assertRaisesRegex(OSError, "allocation scan failure"):
                _engine._allocated_bytes(target_root)


if __name__ == "__main__":
    unittest.main()
