# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""`intergen status` on a machine with no daemon running.

The command is read-only. It used to start a whole daemon to ask it how it was
doing, which meant detecting hardware, sha256-hashing the entire model file, and
starting a model server — on a machine whose complaint was that nothing was
running. On a large-model box that hash reads tens of gigabytes.

These cases pin the two properties that matter: the status path loads nothing
and therefore hashes nothing, and it SAYS it has not checked the file's
integrity so presence is never read as verification.
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from intergen import cli


class OfflineStatusTests(unittest.TestCase):

    def test_status_with_no_daemon_does_not_build_a_daemon(self):
        """The behaviour the whole cut is about: with nothing on the bus, the
        status path must not construct a daemon or start its services."""
        import intergen.dbus_daemon as dbus_daemon

        built = []

        def _refuse(*_a, **_k):
            built.append(True)
            raise AssertionError(
                "a read-only status call constructed a daemon — which detects "
                "hardware, hashes the whole model file and starts a model "
                "server")

        with mock.patch.object(cli, "try_dbus", return_value=None), \
             mock.patch.object(cli, "daemon_has_owner", return_value=False), \
             mock.patch.object(dbus_daemon, "InterGenDaemon", _refuse):
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli.cmd_status()
        self.assertEqual(built, [], "no daemon may be constructed")
        self.assertIn("InterGen Status", buf.getvalue())

    def test_the_status_path_never_hashes_the_model(self):
        """The specific expense removed. Both verification entry points are
        replaced with something that fails loudly if reached, so the case
        cannot pass by the model merely being absent."""
        from intergen.model_manager import ModelManager

        def _refuse(*_a, **_k):
            raise AssertionError("the status path hashed the model file")

        with mock.patch.object(ModelManager, "verify_model", _refuse), \
             mock.patch.object(ModelManager, "verify_arbitrary_path", _refuse):
            status = cli.offline_status()
        self.assertTrue(status["daemon_down"])
        self.assertFalse(status["running"])

    def test_the_payload_states_that_integrity_was_not_checked(self):
        """Presence must never be readable as verification — by anything
        parsing the payload, not only by a person reading the prose."""
        status = cli.offline_status()
        model_file = status.get("model_file")
        if model_file is None:
            self.skipTest("no model resolvable on this machine")
        self.assertIn("integrity_checked", model_file)
        self.assertFalse(model_file["integrity_checked"])

    def test_the_rendering_says_what_it_did_not_check(self):
        status = {
            "running": False, "version": "0.1.0", "daemon_down": True,
            "model_file": {"name": "SomeModel", "path": "/models/some.gguf",
                           "present": True, "size_bytes": 22016023168,
                           "integrity_checked": False},
        }
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.print_status(status)
        out = buf.getvalue()
        self.assertIn("not running", out)
        # Decimal GB (bytes / 1e9), the tier table's convention. The fixture
        # carries the real tier-3 payload's byte count, which renders 22.0.
        self.assertIn("22.0 GB on disk", out)
        self.assertIn("NOT checked", out)
        # and it tells the user how to change the state it is reporting
        self.assertIn("systemctl --user start intergen", out)

    def test_an_absent_model_file_is_reported_as_absent(self):
        status = {
            "running": False, "version": "0.1.0", "daemon_down": True,
            "model_file": {"name": "SomeModel", "path": "/models/some.gguf",
                           "present": False, "size_bytes": 0,
                           "integrity_checked": False},
        }
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.print_status(status)
        out = buf.getvalue()
        self.assertIn("NOT on this machine", out)
        self.assertIn("/models/some.gguf", out)

    def test_a_running_daemon_payload_is_rendered_unchanged(self):
        """The down-state block is additive: a payload from a live daemon must
        not grow any of it."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.print_status({"running": True, "version": "0.1.0",
                              "requests_handled": 7})
        out = buf.getvalue()
        self.assertIn("Running:    True", out)
        self.assertNotIn("not running", out)
        self.assertNotIn("NOT checked", out)


if __name__ == "__main__":
    unittest.main()
