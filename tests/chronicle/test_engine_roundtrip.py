#!/usr/bin/env python3
"""Chronicle engine round-trips: capture -> list -> verify -> scrub -> restore
across all three layers, plus the clock-skew flag. Restore recreates ownership
(needs CAP_CHOWN), so these tests run the in-process path by asserting the
capability present; the escalation path is covered in test_escalate.py.
"""

import os
import tempfile
import unittest
from pathlib import Path

from chronicle import engine as _engine
from chronicle import escalate as _escalate
from chronicle import paths as _paths


class EngineRoundTripTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chronicle-test-")
        self.local = os.path.join(self.tmp, "local")
        self.target = os.path.join(self.tmp, "target")
        os.makedirs(self.target, exist_ok=True)
        # Deterministic, controllable clock.
        self.now = [1_000_000.0]
        self.eng = _engine.Engine(local_root=self.local,
                                  now_fn=lambda: self.now[0])
        # In-process restore: assert the capability so restore_apply does not
        # try to escalate to the (absent) systemd unit under the test harness.
        # addCleanup (not tearDown) so the module global is restored even if a
        # later setUp step raises — no cross-test leakage.
        _orig_cap = _escalate.has_cap_chown
        self.addCleanup(setattr, _escalate, "has_cap_chown", _orig_cap)
        _escalate.has_cap_chown = lambda *a, **k: True

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- config-state ---------------------------------------------------

    def test_config_state_capture_list_verify_restore(self):
        etc = os.path.join(self.tmp, "etc")
        os.makedirs(etc, exist_ok=True)
        f = os.path.join(etc, "app.conf")
        Path(f).write_text("key = 1\n")

        out = self.eng.capture(_paths.LAYER_CONFIG_STATE, scope=[etc],
                               reason="unit")
        vid = out["version_id"]
        self.assertTrue(vid)

        versions = self.eng.list_versions(_paths.LAYER_CONFIG_STATE)
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]["version_id"], vid)

        vr = self.eng.verify(_paths.LAYER_CONFIG_STATE, vid)
        self.assertTrue(vr.get("ok"), vr)

        # Restore beside so we do not need to write back into /etc.
        res = self.eng.restore_apply(_paths.LAYER_CONFIG_STATE, vid, [f],
                                     mode="beside")
        self.assertTrue(res["results"][0]["ok"], res)
        written = Path(res["results"][0]["written_to"])
        self.assertTrue(written.exists())
        self.assertEqual(written.read_text(), "key = 1\n")

    # -- user-data (directory-class target, no loop device) -------------

    def test_user_data_roundtrip_and_hardlink_rotation(self):
        home = os.path.join(self.tmp, "home")
        os.makedirs(home, exist_ok=True)
        a = os.path.join(home, "a.txt")
        Path(a).write_text("v1")
        self.eng.config.user_data_paths = [home]

        self.eng.target_adopt(self.target, target_class="directory")

        out1 = self.eng.capture(_paths.LAYER_USER_DATA, reason="first")
        v1 = out1["version_id"]
        self.now[0] += 3600
        out2 = self.eng.capture(_paths.LAYER_USER_DATA, reason="second-unchanged")
        v2 = out2["version_id"]
        self.assertNotEqual(v1, v2)

        # Unchanged file across versions shares an inode (hardlink rotation).
        troot = self.eng.target_root()
        m1 = self.eng.get_manifest(_paths.LAYER_USER_DATA, v1)
        m2 = self.eng.get_manifest(_paths.LAYER_USER_DATA, v2)
        e1 = next(e for e in m1["entries"] if e["path"] == a)
        st1 = os.stat(_userdata_file(troot, v1, e1))
        e2 = next(e for e in m2["entries"] if e["path"] == a)
        st2 = os.stat(_userdata_file(troot, v2, e2))
        self.assertEqual(st1.st_ino, st2.st_ino,
                         "unchanged file should share an inode across versions")

        # A changed file breaks the hardlink.
        Path(a).write_text("v3-longer")
        self.now[0] += 3600
        v3 = self.eng.capture(_paths.LAYER_USER_DATA, reason="third")["version_id"]
        m3 = self.eng.get_manifest(_paths.LAYER_USER_DATA, v3)
        e3 = next(e for e in m3["entries"] if e["path"] == a)
        st3 = os.stat(_userdata_file(troot, v3, e3))
        self.assertNotEqual(st2.st_ino, st3.st_ino,
                            "changed file must NOT share the prior inode")

    # -- restore-point --------------------------------------------------

    def test_restore_point_captures_footprint(self):
        etc = os.path.join(self.tmp, "etc2")
        os.makedirs(etc, exist_ok=True)
        f = os.path.join(etc, "kept.conf")
        Path(f).write_text("live\n")
        footprint = {"verb": "upgrade", "packages": ["demo"],
                     "reason": "pre-transaction: demo", "paths": [f]}
        out = self.eng.capture(_paths.LAYER_RESTORE_POINT, scope=footprint)
        vid = out["version_id"]
        m = self.eng.get_manifest(_paths.LAYER_RESTORE_POINT, vid)
        paths = [e["path"] for e in m["entries"]]
        self.assertIn(f, paths)

    # -- clock skew -----------------------------------------------------

    def test_backward_clock_is_flagged_not_reordered(self):
        etc = os.path.join(self.tmp, "etc3")
        os.makedirs(etc, exist_ok=True)
        Path(os.path.join(etc, "x")).write_text("1")
        self.eng.capture(_paths.LAYER_CONFIG_STATE, scope=[etc])
        # Move the wall clock backward; the next capture must still order after
        # the first (monotonic sequence) and record a skew event.
        self.now[0] -= 100_000
        Path(os.path.join(etc, "x")).write_text("2")
        self.eng.capture(_paths.LAYER_CONFIG_STATE, scope=[etc])
        self.assertTrue(self.eng.state.get("clock_skew_events"),
                        "a backward wall-clock jump must be recorded")
        vs = self.eng.list_versions(_paths.LAYER_CONFIG_STATE)
        seqs = [v["sequence"] for v in vs]
        self.assertEqual(seqs, sorted(seqs),
                         "versions ordered by monotonic sequence, not wall-clock")

    # -- scrub ----------------------------------------------------------

    def test_scrub_flags_a_flipped_byte(self):
        etc = os.path.join(self.tmp, "etc4")
        os.makedirs(etc, exist_ok=True)
        f = os.path.join(etc, "c.conf")
        Path(f).write_text("intact\n")
        vid = self.eng.capture(_paths.LAYER_CONFIG_STATE, scope=[etc])["version_id"]

        clean = self.eng.scrub()
        self.assertEqual(clean.get("corrupt", []), [], clean)

        # Corrupt one blob in the local CAS and rescrub.
        for blob in _iter_blob_files(Path(self.local) / "cas"):
            blob.write_bytes(b"tampered")
            break
        dirty = self.eng.scrub()
        self.assertTrue(dirty.get("corrupt"), "scrub must name the corrupt blob")
        # The affected version is named.
        named = str(dirty)
        self.assertIn(vid, named)


def _userdata_file(target_root, version_id, entry):
    from chronicle import userdata as _ud
    return _ud.read_file(target_root, version_id, entry)


def _iter_blob_files(cas_dir):
    for shard in sorted(Path(cas_dir).glob("*")):
        if shard.is_dir():
            for b in sorted(shard.glob("*")):
                if b.is_file():
                    yield b


if __name__ == "__main__":
    unittest.main()
