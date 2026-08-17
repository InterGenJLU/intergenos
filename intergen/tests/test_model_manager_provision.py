# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Tests for ModelManager.provision_model — the root-vs-pkexec install branch.

provision_model keeps the model store root-owned read-only either way:
  - root (Forge / sudo): writes straight via download_model.
  - unprivileged `intergen setup`: stage-download as the user, then ONE pkexec
    call installs the pin-re-verified file root-owned.
These tests mock geteuid + the staging download + subprocess.run so no real
download, pkexec, or /var/lib write happens.
"""

from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from intergen import model_manager as mm_mod
from intergen.model_manager import ModelManager, PROVISION_RUNNER_PATH
from intergen.interfaces.types import HardwareTierLevel, ModelInfo


def _model() -> ModelInfo:
    return ModelInfo(
        name="Qwen3.5-2B", filename="Qwen3.5-2B-Q4_K_M.gguf",
        repo_id="unsloth/Qwen3.5-2B-GGUF", quant="Q4_K_M", size_gb=1.5,
        sha256="a" * 64, tier=HardwareTierLevel.TIER_1,
    )


class TestProvisionRouting(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Path(self._tmp.name) / "llm"
        self.mm = ModelManager(model_dir=self.store,
                               manifest_path=Path(self._tmp.name) / "m.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_root_delegates_to_download_model(self):
        with mock.patch("intergen.model_manager.os.geteuid", return_value=0), \
             mock.patch.object(self.mm, "download_model",
                               return_value=True) as dl, \
             mock.patch.object(self.mm, "_provision_via_pkexec") as viapk:
            self.assertTrue(self.mm.provision_model(_model()))
            dl.assert_called_once()
            viapk.assert_not_called()

    def test_nonroot_routes_through_pkexec(self):
        with mock.patch("intergen.model_manager.os.geteuid", return_value=1000), \
             mock.patch.object(self.mm, "download_model") as dl, \
             mock.patch.object(self.mm, "_provision_via_pkexec",
                               return_value=True) as viapk:
            self.assertTrue(self.mm.provision_model(_model()))
            viapk.assert_called_once()
            dl.assert_not_called()


class TestProvisionViaPkexec(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Path(self._tmp.name) / "llm"
        self.mm = ModelManager(model_dir=self.store,
                               manifest_path=Path(self._tmp.name) / "m.json")

    def tearDown(self):
        self._tmp.cleanup()

    def _fake_download(self):
        """A download_model that writes the staged file into the staging
        ModelManager's own model_dir and returns True."""
        def _dl(inner_self, model, *, progress_callback=None):
            inner_self._model_dir.mkdir(parents=True, exist_ok=True)
            (inner_self._model_dir / model.filename).write_bytes(b"staged")
            return True
        return _dl

    def test_install_success_sets_store_path(self):
        ok_proc = types.SimpleNamespace(returncode=0, stdout="installed", stderr="")
        with mock.patch.object(ModelManager, "download_model",
                               new=self._fake_download()), \
             mock.patch("intergen.model_manager.subprocess.run",
                        return_value=ok_proc) as run:
            model = _model()
            self.assertTrue(self.mm._provision_via_pkexec(model))
            self.assertTrue(model.downloaded)
            self.assertEqual(model.local_path,
                             str(self.store / model.filename))
            # pkexec was invoked with the setup runner + a JSON arg.
            argv = run.call_args.args[0]
            self.assertEqual(argv[0], "pkexec")
            self.assertEqual(argv[1], PROVISION_RUNNER_PATH)
            self.assertIn(model.filename, argv[2])

    def test_auth_denied_returns_false(self):
        denied = types.SimpleNamespace(returncode=126, stdout="", stderr="dismissed")
        with mock.patch.object(ModelManager, "download_model",
                               new=self._fake_download()), \
             mock.patch("intergen.model_manager.subprocess.run",
                        return_value=denied):
            self.assertFalse(self.mm._provision_via_pkexec(_model()))

    def test_staging_download_failure_short_circuits(self):
        def _dl_fail(inner_self, model, *, progress_callback=None):
            return False
        with mock.patch.object(ModelManager, "download_model", new=_dl_fail), \
             mock.patch("intergen.model_manager.subprocess.run") as run:
            self.assertFalse(self.mm._provision_via_pkexec(_model()))
            run.assert_not_called()  # never reaches pkexec if staging failed

    def test_pkexec_missing_returns_false(self):
        with mock.patch.object(ModelManager, "download_model",
                               new=self._fake_download()), \
             mock.patch("intergen.model_manager.subprocess.run",
                        side_effect=FileNotFoundError("pkexec")):
            self.assertFalse(self.mm._provision_via_pkexec(_model()))


if __name__ == "__main__":
    unittest.main()
