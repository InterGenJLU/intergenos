# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Two things `intergen status` has to be honest about.

FIRST — the call can create the state it reports. The service is D-Bus
activatable, so on a machine with nothing running, asking for the status
STARTS the assistant. The activation is a legitimate mechanism and is left
alone; doing it without saying so is not, because the user then reads the
consequence of their question as the answer to it.

SECOND — WHICH model the report is about. The model was resolved by asking
ModelManager for a method it does not have, so the lookup always fell through
to the floor tier: the smallest model in the catalog. On a machine serving
something else, every printed line was true about a file that machine would
never load. These cases pin the daemon's own selection order — the
INTERGEN_MODEL_PATH override first, then the shared hardware resolution, and
the floor tier only as a labelled fallback.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from intergen import cli
from intergen.interfaces.types import HardwareTier, HardwareTierLevel, ModelInfo
from intergen.model_manager import ModelManager


def _tier() -> HardwareTier:
    return HardwareTier(
        ram_gb=64.0, gpu_vendor="nvidia", gpu_model="a discrete card",
        gpu_vram_mb=16384, tier=HardwareTierLevel.TIER_3,
        recommended_model="Big Serving Model", recommended_quant="Q4_K_M",
        estimated_model_size_gb=20.0)


def _model(name: str, path: Path) -> ModelInfo:
    return ModelInfo(
        name=name, filename=path.name, repo_id="test/model", quant="Q4_K_M",
        size_gb=20.0, sha256="", tier=HardwareTierLevel.TIER_3,
        local_path=str(path), downloaded=path.exists())


class ActivationDisclosureTests(unittest.TestCase):
    """A status call that started the assistant says so, once."""

    LINE = "the call started it"

    def test_a_status_call_that_activated_the_daemon_says_so(self):
        payload = json.dumps({"running": True, "version": "0.1.0"})
        with mock.patch.object(cli, "daemon_has_owner", return_value=False), \
             mock.patch.object(cli, "try_dbus", return_value=payload):
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli.cmd_status()
        out = buf.getvalue()
        self.assertIn(self.LINE, out,
                      "the status call started the assistant and said nothing")
        self.assertIn("NOT running when this status call began", out)
        self.assertEqual(out.count(self.LINE), 1, "one line, not a paragraph")

    def test_a_daemon_that_was_already_running_gets_no_such_line(self):
        """The disclosure is about a state change this call caused. No change,
        nothing to disclose — otherwise the line means nothing."""
        payload = json.dumps({"running": True, "version": "0.1.0"})
        with mock.patch.object(cli, "daemon_has_owner", return_value=True), \
             mock.patch.object(cli, "try_dbus", return_value=payload):
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli.cmd_status()
        self.assertNotIn(self.LINE, buf.getvalue())

    def test_an_activation_that_is_still_starting_up_discloses_too(self):
        """The activation can succeed while the freshly started daemon is too
        busy to answer inside the timeout. It still started because of this
        call, so it is still disclosed."""
        with mock.patch.object(cli, "daemon_has_owner",
                               side_effect=[False, True]), \
             mock.patch.object(cli, "try_dbus", return_value=None):
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli.cmd_status()
        out = buf.getvalue()
        self.assertIn(self.LINE, out)
        self.assertIn("daemon is busy", out)

    def test_a_down_machine_reports_down_without_claiming_a_start(self):
        """Nothing on the bus and no activation: the offline report must not
        grow a line saying something was started."""
        with mock.patch.object(cli, "daemon_has_owner", return_value=False), \
             mock.patch.object(cli, "try_dbus", return_value=None):
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli.cmd_status()
        out = buf.getvalue()
        self.assertNotIn(self.LINE, out)
        self.assertIn("not running", out)

    def test_the_renderer_carries_the_disclosure_from_the_payload(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.print_status({"running": True, "version": "0.1.0",
                              "activated_by_this_call": True})
        self.assertIn(self.LINE, buf.getvalue())


class OfflineModelSelectionTests(unittest.TestCase):
    """The report is about the model this machine would actually serve."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = Path(self._tmp.name)
        self.served = self.store / "big-serving-model.gguf"
        self.served.write_bytes(b"x" * 4096)
        self.floor = self.store / "small-floor-model.gguf"
        self.floor.write_bytes(b"y" * 16)
        # No INTERGEN_MODEL_PATH unless a case sets one — the environment this
        # test process happens to carry must not decide the answer.
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("INTERGEN_MODEL_PATH", None)

    def _detected(self):
        return mock.patch("intergen.hardware.HardwareDetector.detect",
                          return_value=_tier())

    def test_the_model_reported_is_the_one_the_daemon_would_serve(self):
        served = _model("Big Serving Model", self.served)
        floor = _model("Small Floor Model", self.floor)
        with self._detected(), \
             mock.patch.object(ModelManager, "resolve_for_detected",
                               return_value=served), \
             mock.patch.object(ModelManager, "get_model_for_tier",
                               return_value=floor):
            status = cli.offline_status()
        mf = status["model_file"]
        self.assertEqual(mf["name"], "Big Serving Model",
                         "status described a model this machine does not serve")
        self.assertEqual(mf["path"], str(self.served))
        self.assertTrue(mf["present"])
        self.assertEqual(mf["size_bytes"], 4096)
        self.assertFalse(mf["integrity_checked"])
        self.assertIn("hardware", mf["selected_by"])

    def test_an_explicit_model_path_override_wins(self):
        """The daemon honours INTERGEN_MODEL_PATH before anything else, so a
        status report that ignored it would name a different file than the one
        a start would load."""
        floor = _model("Small Floor Model", self.floor)
        with self._detected(), \
             mock.patch.dict(os.environ,
                             {"INTERGEN_MODEL_PATH": str(self.served)}), \
             mock.patch.object(ModelManager, "resolve_for_detected",
                               return_value=floor), \
             mock.patch.object(ModelManager, "get_model_for_tier",
                               return_value=floor):
            status = cli.offline_status()
        mf = status["model_file"]
        self.assertEqual(mf["path"], str(self.served))
        self.assertTrue(mf["present"])
        self.assertIn("INTERGEN_MODEL_PATH", mf["selected_by"])
        self.assertFalse(mf["integrity_checked"])

    def test_an_unresolvable_machine_falls_back_and_says_it_did(self):
        floor = _model("Small Floor Model", self.floor)
        with self._detected(), \
             mock.patch.object(ModelManager, "resolve_for_detected",
                               return_value=None), \
             mock.patch.object(ModelManager, "get_model_for_tier",
                               return_value=floor):
            status = cli.offline_status()
        mf = status["model_file"]
        self.assertEqual(mf["name"], "Small Floor Model")
        self.assertIn("floor tier", mf["selected_by"])

    def test_a_single_model_machine_reads_exactly_as_before(self):
        """No regression for the machine the old lookup happened to be right
        about: one model, resolved, reported present with its size."""
        floor = _model("Small Floor Model", self.floor)
        with self._detected(), \
             mock.patch.object(ModelManager, "resolve_for_detected",
                               return_value=floor), \
             mock.patch.object(ModelManager, "get_model_for_tier",
                               return_value=floor):
            status = cli.offline_status()
        mf = status["model_file"]
        self.assertEqual(mf["name"], "Small Floor Model")
        self.assertEqual(mf["size_bytes"], 16)
        self.assertTrue(mf["present"])

    def test_a_model_that_is_not_on_the_machine_is_reported_absent(self):
        missing = self.store / "not-here.gguf"
        absent = _model("Big Serving Model", missing)
        with self._detected(), \
             mock.patch.object(ModelManager, "resolve_for_detected",
                               return_value=absent), \
             mock.patch.object(ModelManager, "get_model_for_tier",
                               return_value=absent):
            status = cli.offline_status()
        mf = status["model_file"]
        self.assertFalse(mf["present"])
        self.assertEqual(mf["size_bytes"], 0)

    def test_selecting_the_served_model_still_hashes_nothing(self):
        """The 088 property, re-pinned against the new selection path: both
        verification entry points fail loudly if the status path reaches
        them."""
        def _refuse(*_a, **_k):
            raise AssertionError("the status path hashed the model file")

        served = _model("Big Serving Model", self.served)
        with self._detected(), \
             mock.patch.object(ModelManager, "resolve_for_detected",
                               return_value=served), \
             mock.patch.object(ModelManager, "verify_model", _refuse), \
             mock.patch.object(ModelManager, "verify_arbitrary_path", _refuse):
            status = cli.offline_status()
        self.assertEqual(status["model_file"]["name"], "Big Serving Model")

    def test_the_rendering_names_why_that_model(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.print_status({
                "running": False, "version": "0.1.0", "daemon_down": True,
                "model_file": {"name": "Big Serving Model",
                               "path": "/models/big.gguf", "present": True,
                               "size_bytes": 21474836480,
                               "integrity_checked": False,
                               "selected_by": "this machine's detected "
                                              "hardware"},
            })
        out = buf.getvalue()
        self.assertIn("Big Serving Model", out)
        self.assertIn("selected by this machine's detected hardware", out)


if __name__ == "__main__":
    unittest.main()
