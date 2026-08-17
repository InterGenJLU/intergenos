#!/usr/bin/env python3
"""chronicled --task restore --request FILE: the request-file form the
chronicle-restore@ unit uses. It reads {layer, version_id, paths, mode} and
writes the result beside the request (spec §6)."""

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

from chronicle import engine as _engine
from chronicle import escalate as _escalate
from chronicle import paths as _paths


def _load_chronicled():
    # The entrypoint has no .py suffix, so spec_from_file_location can't infer a
    # loader — supply a SourceFileLoader explicitly.
    from importlib.machinery import SourceFileLoader
    assets = Path(__file__).resolve().parents[2] / "assets" / "intergenos-backup"
    loader = SourceFileLoader("chronicled_main", str(assets / "chronicled"))
    spec = importlib.util.spec_from_loader("chronicled_main", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class ChronicledRequestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chronicle-req-")
        self.local = os.path.join(self.tmp, "local")
        _orig = _escalate.has_cap_chown
        self.addCleanup(setattr, _escalate, "has_cap_chown", _orig)
        _escalate.has_cap_chown = lambda *a, **k: True  # in-process restore
        self.chronicled = _load_chronicled()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_request_form_restores_and_writes_result(self):
        # Capture a config-state version to restore from.
        etc = os.path.join(self.tmp, "etc")
        os.makedirs(etc, exist_ok=True)
        f = os.path.join(etc, "d.conf")
        Path(f).write_text("restore-me\n")
        eng = _engine.Engine(local_root=self.local)
        vid = eng.capture(_paths.LAYER_CONFIG_STATE, scope=[etc])["version_id"]

        req_path = os.path.join(self.tmp, "restore-abc123.json")
        Path(req_path).write_text(json.dumps({
            "layer": _paths.LAYER_CONFIG_STATE,
            "version_id": vid,
            "paths": [f],
            "mode": "beside",
        }))

        rc = self.chronicled.main(
            ["--local-root", self.local, "--task", "restore",
             "--request", req_path])
        self.assertEqual(rc, 0)

        res_path = os.path.join(self.tmp, "restore-abc123.result.json")
        self.assertTrue(os.path.exists(res_path), "result written beside request")
        result = json.loads(Path(res_path).read_text())
        self.assertEqual(result["version_id"], vid)
        self.assertTrue(result["results"][0]["ok"], result)
        # The beside restore materialized the file.
        self.assertTrue(Path(result["results"][0]["written_to"]).exists())


if __name__ == "__main__":
    unittest.main()
