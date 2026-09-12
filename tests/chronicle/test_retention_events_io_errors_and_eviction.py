#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Two gaps in the retention record, found by an independent documentary
review of the landed guard (2026-09-11) and closed here.

R1 — a validator I/O error bypassed the saved outcome. The shared checked-open
in userdata can raise OSError (lstat, open, fstat of the store's directories).
_check_drop converted only ValueError into the engine's own error class, so an
OSError during the plan's preflight escaped WITHOUT a prune-refused record, and
an OSError during the repeated validation of a later candidate — after an
earlier candidate had already been removed — escaped WITHOUT a prune-stopped
record: the announcement stayed alone in state, indistinguishable from a
process killed mid-plan, although the process was alive and knew what happened.

R2 — eviction could erase an unresolved plan. The engine keeps the newest 200
retention events; an announcement whose plan never reached a terminal record
(completed / stopped / refused) was dropped like any other old event after 200
later appends, taking with it the only evidence that a destructive plan was in
flight. The docstring also claimed a timer task prints every run to the journal
— no such task existed.

The fixes: (1) OSError joins the caught class in _check_drop; (2) every event of
one plan carries the same plan id and the store root, eviction never drops an
announcement whose plan has no terminal record, and every retention event is
emitted through the module logger (the daemon runs under systemd, whose
journal carries the process's stderr, where an unconfigured logger's warnings
go).
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


class _PlanFixture(unittest.TestCase):
    """Three captured user-data versions, thinning keeps the newest, so a plan
    prunes the two older ones oldest first — the fixture the all-before-any
    tests use."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chronicle-retevents-")
        self.local = os.path.join(self.tmp, "local")
        self.target = os.path.join(self.tmp, "target")
        os.makedirs(self.local)
        os.makedirs(self.target)
        self.now = [1_800_000_000]
        self.eng = _engine.Engine(local_root=self.local, now_fn=lambda: self.now[0])
        self.addCleanup(setattr, _escalate, "has_cap_chown", _escalate.has_cap_chown)
        _escalate.has_cap_chown = lambda *a, **k: True
        home = os.path.join(self.tmp, "home")
        os.makedirs(home, exist_ok=True)
        self.home_file = Path(home) / "a.txt"
        self.eng.config.user_data_paths = [home]
        self.eng.target_adopt(self.target, target_class="directory")
        self.vids = []
        for i in range(3):
            self.home_file.write_text(f"v{i}")
            self.now[0] += 3600
            self.vids.append(self.eng.capture(
                _paths.LAYER_USER_DATA, reason=f"capture {i}")["version_id"])
        self.troot = Path(self.eng.target_root())
        self.addCleanup(setattr, _retention, "thin_keep_user_data",
                        _retention.thin_keep_user_data)
        _retention.thin_keep_user_data = lambda versions, now: {self.vids[-1]}
        self.addCleanup(setattr, _userdata, "check_version_removal",
                        _userdata.check_version_removal)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _events(self):
        return self.eng.status()["retention_events"]

    def _listed(self):
        return [m["version_id"] for m in self.eng.list_versions(_paths.LAYER_USER_DATA)]

    def _manifest_path(self, vid):
        return _paths.versions_dir(self.troot, _paths.LAYER_USER_DATA) / f"{vid}.json"


class ValidatorIoErrorTest(_PlanFixture):

    def test_io_error_in_preflight_records_refused_and_removes_nothing(self):
        v0, v1, v2 = self.vids
        real = _userdata.check_version_removal

        def failing(root, vid):
            if vid == v1:
                raise PermissionError(13, "Permission denied", str(vid))
            return real(root, vid)
        _userdata.check_version_removal = failing
        with self.assertRaises(_engine.EngineError) as ctx:
            self.eng.retention_apply(_paths.LAYER_USER_DATA)
        self.assertIn("Permission denied", str(ctx.exception))
        self.assertEqual([e["kind"] for e in self._events()], ["prune-refused"])
        self.assertEqual(self._listed(), [v0, v1, v2], "nothing removed")
        self.assertTrue(self._manifest_path(v0).exists())

    def test_io_error_on_a_later_candidate_records_stopped_with_what_was_removed(self):
        v0, v1, v2 = self.vids
        real = _userdata.check_version_removal
        calls = {v1: 0}

        def failing(root, vid):
            if vid == v1:
                calls[v1] += 1
                if calls[v1] >= 2:  # preflight passes; the repeated validation before the drop fails
                    raise PermissionError(13, "Permission denied", str(vid))
            return real(root, vid)
        _userdata.check_version_removal = failing
        with self.assertRaises(_engine.EngineError) as ctx:
            self.eng.retention_apply(_paths.LAYER_USER_DATA)
        self.assertIn("Permission denied", str(ctx.exception))
        kinds = [e["kind"] for e in self._events()]
        self.assertEqual(kinds, ["prune-announced", "prune-stopped"])
        stopped = self._events()[-1]
        self.assertEqual(stopped["removed"], [v0])
        self.assertEqual(stopped["remaining"], [v1])
        self.assertIn("Permission denied", stopped["error"])
        self.assertTrue(self._manifest_path(v1).exists(), "v1 stays listed for the next pass")
        self.assertEqual(self._listed(), [v1, v2])


class PlanIdentityAndEvictionTest(_PlanFixture):

    def test_every_event_of_a_plan_carries_the_same_plan_id_and_the_root(self):
        self.eng.retention_apply(_paths.LAYER_USER_DATA)
        announced, completed = self._events()
        self.assertEqual(announced["kind"], "prune-announced")
        self.assertEqual(completed["kind"], "prune-completed")
        self.assertTrue(announced["plan"])
        self.assertEqual(announced["plan"], completed["plan"])
        self.assertEqual(announced["root"], str(self.troot))
        self.assertEqual(completed["root"], str(self.troot))

    def test_a_resolved_plan_is_evicted_like_any_old_event(self):
        self.eng.retention_apply(_paths.LAYER_USER_DATA)
        plan = self._events()[0]["plan"]
        for i in range(self.eng._RETENTION_EVENTS_KEPT + 5):
            self.eng._record_retention_event("test-filler", _paths.LAYER_USER_DATA, "test", [], plan=f"filler-{i}")
        self.assertEqual(len(self._events()), self.eng._RETENTION_EVENTS_KEPT)
        self.assertNotIn(plan, {e["plan"] for e in self._events()})

    def test_an_unresolved_announcement_survives_eviction(self):
        # An announcement whose plan never reached a terminal record — the state
        # a process killed mid-plan leaves behind.
        self.eng._record_retention_event(
            "prune-announced", _paths.LAYER_USER_DATA, "thinning", ["gone-1", "gone-2"],
            plan="unresolved-plan", root=str(self.troot))
        for i in range(self.eng._RETENTION_EVENTS_KEPT + 50):
            self.eng._record_retention_event("test-filler", _paths.LAYER_USER_DATA, "test", [], plan=f"filler-{i}")
        events = self._events()
        self.assertEqual(len(events), self.eng._RETENTION_EVENTS_KEPT + 1)
        self.assertEqual(events[0]["kind"], "prune-announced")
        self.assertEqual(events[0]["plan"], "unresolved-plan")
        self.assertEqual(events[0]["version_ids"], ["gone-1", "gone-2"])
        # And it survives on disk and across a reload, which is the point.
        data = json.loads(_paths.state_path(self.local).read_text())
        self.assertEqual(data["retention_events"][0]["plan"], "unresolved-plan")
        reloaded = _engine.Engine(local_root=self.local, now_fn=lambda: self.now[0])
        self.assertEqual(reloaded.status()["retention_events"][0]["plan"], "unresolved-plan")

    def test_an_announcement_without_a_plan_id_is_treated_as_unresolved(self):
        # Records written before plan ids existed carry none; unknown = unresolved.
        self.eng._record_retention_event(
            "prune-announced", _paths.LAYER_USER_DATA, "thinning", ["old-1"])
        for i in range(self.eng._RETENTION_EVENTS_KEPT + 10):
            self.eng._record_retention_event("test-filler", _paths.LAYER_USER_DATA, "test", [], plan=f"filler-{i}")
        self.assertEqual(self._events()[0]["version_ids"], ["old-1"])

    def test_every_retention_event_reaches_the_module_logger(self):
        with self.assertLogs("chronicle.retention", level="WARNING") as captured:
            self.eng.retention_apply(_paths.LAYER_USER_DATA)
        text = "\n".join(captured.output)
        self.assertIn("prune-announced", text)
        self.assertIn("prune-completed", text)
        for vid in self.vids[:2]:
            self.assertIn(vid, text)


if __name__ == "__main__":
    unittest.main()
