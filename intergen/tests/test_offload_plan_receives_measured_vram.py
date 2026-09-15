# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The offload plan is measured with the video-memory figure the detector read.

The defect (seen on every installed machine, 2026-09-05 and 2026-09-10): the
daemon's hardware record — the dictionary it keeps from the detector's tier —
carried no ``gpu_vram_mb`` key, while the model-launch path read exactly that
key to size the offload. The plan therefore always ran with an unknown card
and said "video memory could not be read, so whether the model fits is
unknown", then offloaded every layer blind. On a 4 GB card that blind offload
aborted the engine at model load; on a 32 GB card it merely lied about having
measured anything.

What is pinned here:

  * the record the daemon builds from a detected tier carries the detector's
    ``gpu_vram_mb`` figure, and the service start path builds its record
    through that one function (read from the source, so a second literal
    dictionary cannot reappear beside it);
  * the plan computed from that record on a machine whose detector read the
    card states the measured fit (projected against the card) and never says
    the figure could not be read;
  * a card the detector could not read still yields the honest "could not be
    read" plan — the fix passes the measurement through, it does not invent one.
"""

from __future__ import annotations

import ast
import inspect
import unittest

from intergen import dbus_daemon
from intergen.gpu_offload import plan_offload
from intergen.interfaces.types import HardwareTier, HardwareTierLevel


def _tier(vram):
    return HardwareTier(ram_gb=94.0, gpu_vendor="amd", gpu_model="amd [0x7551]",
                        gpu_vram_mb=vram, tier=HardwareTierLevel.TIER_3,
                        recommended_model="test-model",
                        recommended_quant="Q4_K_M",
                        estimated_model_size_gb=5.0)


class HardwareRecordCarriesTheMeasurementTests(unittest.TestCase):

    def test_the_record_carries_the_detectors_figure(self):
        record = dbus_daemon._hardware_tier_record(_tier(32624))
        self.assertEqual(record["gpu_vram_mb"], 32624)
        # The fields the record always carried are still there.
        for key in ("level", "ram_gb", "gpu_vendor", "gpu_model",
                    "recommended_model", "recommended_quant",
                    "estimated_model_size_gb"):
            self.assertIn(key, record)
        self.assertEqual(record["level"], HardwareTierLevel.TIER_3.value)

    def test_an_unread_card_is_carried_as_unknown_not_invented(self):
        record = dbus_daemon._hardware_tier_record(_tier(None))
        self.assertIn("gpu_vram_mb", record)
        self.assertIsNone(record["gpu_vram_mb"])

    def test_the_service_start_builds_its_record_through_that_function(self):
        src = inspect.getsource(dbus_daemon)
        tree = ast.parse(src)
        calls = []
        literal_records = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = getattr(fn, "id", None) or getattr(fn, "attr", None)
                if name == "_hardware_tier_record":
                    calls.append(node.lineno)
            if (isinstance(node, ast.Assign)
                    and isinstance(node.value, ast.Dict)
                    and any(isinstance(t, ast.Attribute)
                            and t.attr == "_hardware_tier"
                            for t in node.targets)):
                literal_records.append(node.lineno)
        self.assertTrue(calls, "start_service no longer builds the hardware "
                               "record through _hardware_tier_record")
        self.assertEqual(literal_records, [],
                         "a literal hardware-record dictionary is assigned "
                         f"beside the function at lines {literal_records}")


class PlanFromTheRecordTests(unittest.TestCase):

    def _plan(self, record, model_bytes, layers):
        vram = record.get("gpu_vram_mb")
        if not isinstance(vram, int):
            vram = None
        return plan_offload(vram_mb=vram, model_bytes=model_bytes,
                            projector_bytes=0, total_layers=layers)

    def test_a_read_card_gets_a_measured_plan(self):
        record = dbus_daemon._hardware_tier_record(_tier(32624))
        plan = self._plan(record, model_bytes=5 * 1024 ** 3, layers=40)
        self.assertNotIn("could not be read", plan.reason)
        self.assertIn("32624", plan.reason)
        self.assertTrue(plan.fits)

    def test_a_small_card_gets_a_measured_partial_plan(self):
        record = dbus_daemon._hardware_tier_record(_tier(4096))
        plan = self._plan(record, model_bytes=6 * 1024 ** 3, layers=48)
        self.assertNotIn("could not be read", plan.reason)
        self.assertFalse(plan.fits)
        self.assertLess(plan.layers, 48)

    def test_an_unread_card_keeps_the_honest_unknown_plan(self):
        record = dbus_daemon._hardware_tier_record(_tier(None))
        plan = self._plan(record, model_bytes=5 * 1024 ** 3, layers=40)
        self.assertIn("could not be read", plan.reason)


if __name__ == "__main__":
    unittest.main()
