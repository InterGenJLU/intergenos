#!/usr/bin/env python3
"""Restore capability escalation (spec §6, §16.2): the CAP_CHOWN gate and the
request-staging seam that runs the higher-capability chronicle-restore@ unit."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from chronicle import escalate as _escalate


class HasCapChownTest(unittest.TestCase):
    def _status(self, cap_eff_hex):
        tmp = tempfile.mkdtemp(prefix="chronicle-cap-")
        p = os.path.join(tmp, "status")
        Path(p).write_text(f"Name:\tx\nCapEff:\t{cap_eff_hex}\n")
        return p

    def test_cap_chown_present(self):
        # bit 0 set
        self.assertTrue(_escalate.has_cap_chown(self._status("0000000000000001")))
        self.assertTrue(_escalate.has_cap_chown(self._status("00000000a80425fb")))

    def test_cap_chown_absent(self):
        # CAP_DAC_READ_SEARCH (bit 2) only, no CAP_CHOWN (bit 0)
        self.assertFalse(_escalate.has_cap_chown(self._status("0000000000000004")))
        self.assertFalse(_escalate.has_cap_chown(self._status("0000000000000000")))


class EscalationSeamTest(unittest.TestCase):
    def setUp(self):
        self.rt = tempfile.mkdtemp(prefix="chronicle-rt-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.rt, ignore_errors=True)

    def test_stage_run_and_cleanup(self):
        seen = {}

        def fake_runner(unit):
            rid = unit.split("@")[1].split(".service")[0]
            req = json.loads(Path(self.rt, f"restore-{rid}.json").read_text())
            seen["req"] = req
            Path(self.rt, f"restore-{rid}.result.json").write_text(
                json.dumps({"version_id": req["version_id"],
                            "results": [{"path": p, "ok": True, "written_to": p}
                                        for p in req["paths"]]}))

        result = _escalate.run_restore_via_unit(
            "user-data", "v7", ["/home/u/a", "/home/u/b"], mode="beside",
            runtime_dir=self.rt, runner=fake_runner, request_id="cafef00d")

        self.assertEqual(seen["req"]["layer"], "user-data")
        self.assertEqual(seen["req"]["mode"], "beside")
        self.assertEqual(len(result["results"]), 2)
        # Request + result files are cleaned up.
        self.assertFalse(Path(self.rt, "restore-cafef00d.json").exists())
        self.assertFalse(Path(self.rt, "restore-cafef00d.result.json").exists())

    def test_runner_failure_raises_and_cleans_up(self):
        def boom(_unit):
            raise _escalate.RestoreEscalationError("unit failed")

        with self.assertRaises(_escalate.RestoreEscalationError):
            _escalate.run_restore_via_unit(
                "config-state", "v1", ["/etc/x"], runtime_dir=self.rt,
                runner=boom, request_id="deadbeef")
        # Request file cleaned up even on failure.
        self.assertFalse(Path(self.rt, "restore-deadbeef.json").exists())

    def test_missing_result_raises(self):
        with self.assertRaises(_escalate.RestoreEscalationError):
            _escalate.run_restore_via_unit(
                "config-state", "v1", ["/etc/x"], runtime_dir=self.rt,
                runner=lambda unit: None, request_id="beadfeed")  # no result written


if __name__ == "__main__":
    unittest.main()
