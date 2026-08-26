# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A refused model install must not be reported as a failed download.

`intergen setup` run unprivileged downloads and pin-verifies the model into a
staging directory AS THE USER, then escalates once through pkexec under
org.intergenos.intergen.provision-model-storage to install it into the
root-owned store. If the person dismisses THAT prompt, the download has already
succeeded and only the install was refused.

Before this change the refusal was logged inside the model manager and thrown
away: provision_model returned False, and setup's reporting — which reads
``last_download_failure`` — found nothing recorded, fell through to its
catch-all, and printed "The download did not finish, and the reason was not one
this setup could identify." Every word of that is wrong for this case, and it is
the same swallowing this project already fixed one layer up in the greeter.

What is asserted here: the model manager records WHY provisioning failed, in the
same shape it already records why a download failed, and tells apart the three
outcomes the pkexec call can produce — the authorization was not given (126),
the runner could not be executed (127), and the privileged dispatcher itself
refused (anything else, e.g. a checksum re-verify mismatch).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from intergen.interfaces.types import HardwareTierLevel
from intergen.model_manager import ModelManager

PKEXEC_NOT_AUTHORIZED = 126
PKEXEC_COMMAND_NOT_EXECUTED = 127


class ProvisionFailureIsRecorded(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.mm = ModelManager(model_dir=root / "llm",
                               manifest_path=root / "m.json")
        (root / "llm").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_manager_has_somewhere_to_record_it(self):
        self.assertTrue(
            hasattr(self.mm, "last_provision_failure"),
            "the model manager records why a DOWNLOAD failed but has nowhere to "
            "record why the privileged INSTALL failed, so the reason is lost "
            "between the pkexec call and the sentence the user reads")

    def _provision_with_rc(self, rc):
        model = SimpleNamespace(
            name="test-model", filename="test-model.gguf",
            has_vision=False, mmproj_filename=None,
            local_path=None, downloaded=False, mmproj_local_path=None)

        def fake_download(m, progress_callback=None):
            # The staging download succeeds; only the install is refused.
            staged = Path(self._staging) / m.filename
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(b"x")
            return True

        real_init = ModelManager.__init__

        def capture_init(inner, model_dir=None, **kw):
            real_init(inner, model_dir=model_dir, **kw)
            self._staging = str(model_dir)
            inner.download_model = fake_download

        with mock.patch.object(ModelManager, "__init__", capture_init), \
             mock.patch("intergen.model_manager.subprocess.run",
                        return_value=SimpleNamespace(
                            returncode=rc, stdout="", stderr="")):
            ok = self.mm._provision_via_pkexec(model)
        return ok

    def test_a_refused_authorization_is_recorded_as_such(self):
        ok = self._provision_with_rc(PKEXEC_NOT_AUTHORIZED)
        self.assertFalse(ok)
        self.assertEqual(
            self.mm.last_provision_failure, "not-authorized",
            "a dismissed or refused install prompt was not recorded as such")

    def test_a_runner_that_cannot_be_executed_is_told_apart(self):
        ok = self._provision_with_rc(PKEXEC_COMMAND_NOT_EXECUTED)
        self.assertFalse(ok)
        self.assertEqual(self.mm.last_provision_failure, "runner-missing")

    def test_a_dispatcher_refusal_is_told_apart_from_both(self):
        ok = self._provision_with_rc(1)
        self.assertFalse(ok)
        self.assertEqual(self.mm.last_provision_failure, "dispatcher-refused")

    def test_a_success_clears_it(self):
        ok = self._provision_with_rc(0)
        self.assertTrue(ok)
        self.assertIsNone(self.mm.last_provision_failure)


class SetupReportsTheRefusal(unittest.TestCase):
    """setup.py must say the install was refused, and emit a mappable marker."""

    def test_setup_has_a_reporter_for_it(self):
        from intergen import setup as setup_mod
        self.assertTrue(
            hasattr(setup_mod, "report_provision_failure"),
            "setup has no reporting path for a refused install, so it falls "
            "through to the download-failed text")

    def test_the_refusal_text_does_not_claim_the_download_failed(self):
        from intergen import setup as setup_mod
        lines = setup_mod.report_provision_failure("not-authorized")
        body = "\n".join(lines).lower()
        self.assertNotIn("download did not finish", body)
        self.assertIn("downloaded", body,
                      "the text should say the model IS downloaded — that is the "
                      "fact that makes retrying cheap")

    def test_a_stable_marker_is_emitted_for_the_caller(self):
        from intergen import setup as setup_mod
        lines = setup_mod.report_provision_failure("not-authorized")
        self.assertTrue(
            any(line.startswith("intergen-setup: result=") for line in lines),
            "nothing machine-readable is emitted, so a caller has to match prose "
            "to know what happened")
        self.assertTrue(
            any("provisioning-refused" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
