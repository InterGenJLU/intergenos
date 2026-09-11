#!/usr/bin/env python3
"""Engine state and version commits serialize across instances and processes."""

import multiprocessing
import tempfile
import unittest
from pathlib import Path

from chronicle import engine as _engine
from chronicle import manifest as _manifest
from chronicle import paths as _paths
from chronicle import userdata as _userdata


def _process_capture(local_root, source, barrier, results):
    engine = _engine.Engine(local_root=local_root, now_fn=lambda: 1_000_000)
    barrier.wait()
    try:
        version = engine.capture(
            _paths.LAYER_CONFIG_STATE,
            scope=[source],
            reason=f"process-{multiprocessing.current_process().name}",
        )["version_id"]
        results.put(("ok", version))
    except Exception as exc:
        results.put(("error", f"{type(exc).__name__}: {exc}"))


class EngineStateTransactionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="chronicle-state-")
        root = Path(self.tmp.name)
        self.local = root / "local"
        self.source = root / "source"
        self.source.mkdir()
        (self.source / "setting").write_text("value\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_stale_instances_allocate_distinct_sequences_and_versions(self):
        first_engine = _engine.Engine(
            local_root=self.local, now_fn=lambda: 1_000_000
        )
        stale_engine = _engine.Engine(
            local_root=self.local, now_fn=lambda: 1_000_000
        )

        first = first_engine.capture(
            _paths.LAYER_CONFIG_STATE,
            scope=[str(self.source)],
            reason="first",
        )["version_id"]
        second = stale_engine.capture(
            _paths.LAYER_CONFIG_STATE,
            scope=[str(self.source)],
            reason="second",
        )["version_id"]

        self.assertNotEqual(first, second)
        versions = _manifest.list_versions(
            self.local, _paths.LAYER_CONFIG_STATE
        )
        self.assertEqual([m["sequence"] for m in versions], [1, 2])
        self.assertEqual([m["reason"] for m in versions], ["first", "second"])

    def test_stale_state_updates_merge_instead_of_replacing_fields(self):
        first_engine = _engine.Engine(local_root=self.local)
        stale_engine = _engine.Engine(local_root=self.local)
        target = Path(self.tmp.name) / "target"
        target.mkdir()

        first_engine.target_adopt(target, target_class="directory")
        stale_engine.pin("version-to-keep")

        fresh = _engine.Engine(local_root=self.local)
        self.assertEqual(fresh.state["target"]["mountpoint"], str(target))
        self.assertIn("version-to-keep", fresh.state["pins"])

    def test_long_lived_status_reloads_external_state(self):
        observer = _engine.Engine(local_root=self.local)
        writer = _engine.Engine(local_root=self.local)
        target = Path(self.tmp.name) / "target-status"
        target.mkdir()
        writer.target_adopt(target, target_class="directory")

        status = observer.status()

        self.assertEqual(status["target"]["mountpoint"], str(target))

    def test_async_capture_persists_clock_state(self):
        engine = _engine.Engine(
            local_root=self.local, now_fn=lambda: 1_234_567
        )

        engine.capture(
            _paths.LAYER_CONFIG_STATE,
            reason="queued",
            sync=False,
        )

        fresh = _engine.Engine(local_root=self.local)
        self.assertEqual(fresh.state["clock_last_wall"], 1_234_567)

    def test_processes_allocate_distinct_versions(self):
        # Spawn independent interpreters: this exercises the file lock without
        # inheriting any threads the GUI tests may have created earlier.
        context = multiprocessing.get_context("spawn")
        barrier = context.Barrier(3)
        results = context.Queue()
        processes = [
            context.Process(
                target=_process_capture,
                args=(str(self.local), str(self.source), barrier, results),
                name=f"capture-{index}",
            )
            for index in range(2)
        ]
        for process in processes:
            process.start()
        barrier.wait(timeout=5)
        for process in processes:
            process.join(timeout=10)
            self.assertFalse(process.is_alive(), "capture process did not finish")
            self.assertEqual(process.exitcode, 0)

        outcomes = [results.get(timeout=2) for _ in processes]
        self.assertEqual([kind for kind, _value in outcomes], ["ok", "ok"])
        version_ids = [value for _kind, value in outcomes]
        self.assertEqual(len(set(version_ids)), 2)
        versions = _manifest.list_versions(
            self.local, _paths.LAYER_CONFIG_STATE
        )
        self.assertEqual([m["sequence"] for m in versions], [1, 2])

    def test_manifest_commit_never_replaces_existing_version(self):
        first = _manifest.build_manifest(
            _paths.LAYER_CONFIG_STATE, 1, 1_000_000, "first", []
        )
        replacement = dict(first)
        replacement["reason"] = "replacement"
        _manifest.commit_manifest(self.local, first)

        with self.assertRaises(_manifest.ManifestCollision):
            _manifest.commit_manifest(self.local, replacement)

        stored = _manifest.find_version(
            self.local, _paths.LAYER_CONFIG_STATE, first["version_id"]
        )
        self.assertEqual(stored["reason"], "first")

    def test_user_data_commit_never_replaces_existing_version(self):
        target = Path(self.tmp.name) / "userdata-target"
        first = _userdata.capture(
            [str(self.source)], target, None, 1, 1_000_000, "first"
        )

        with self.assertRaises(_manifest.ManifestCollision):
            _userdata.capture(
                [str(self.source)], target, None, 1, 1_000_000, "replacement"
            )

        stored = _manifest.find_version(
            target, _paths.LAYER_USER_DATA, first
        )
        self.assertEqual(stored["reason"], "first")
        tree = _userdata.userdata_tree(target, first)
        self.assertTrue((tree / str(self.source).lstrip("/") / "setting").exists())


if __name__ == "__main__":
    unittest.main()
