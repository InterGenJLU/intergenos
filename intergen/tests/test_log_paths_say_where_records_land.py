# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The configuration must not point at a file nothing writes.

The shipped configuration states /var/log/intergen/intergen.log. A per-user
service cannot write there — ProtectSystem=strict leaves that root-owned
directory read-only — so InterGen resolves the log under the user's XDG state
directory instead. The code has done that correctly since the G3-7 work and
announces the file it chose; what was missing was any word of it in the file a
person reads. Measured 2026-09-17 on an installed machine: the configured path
did not exist at all, while the daemon was writing
~/.local/state/intergen/intergen.log.

Asserted here: the redirect the configuration now describes is the redirect the
code performs, for the log file and for the event log, so the two cannot drift
apart silently.
"""
from __future__ import annotations

import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from intergen.config import _DEFAULTS, Config
from intergen.metrics import EventLogger


class TheShippedLogPathIsRedirectedForAUserService(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.state = Path(self._td.name) / "state"
        self._handlers = logging.getLogger().handlers[:]
        self.addCleanup(
            lambda: setattr(logging.getLogger(), "handlers", self._handlers))

    def _configured_file(self):
        return _DEFAULTS["logging"]["file"]

    def test_the_shipped_default_is_still_a_var_log_path(self):
        # If this ever stops being true the comment this test guards is wrong.
        self.assertTrue(self._configured_file().startswith("/var/log/"))

    def test_a_non_root_process_logs_under_its_own_state_directory(self):
        cfg = Config()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.state)}), \
                mock.patch("intergen.config.os.geteuid", return_value=1000):
            cfg.setup_logging()
        expected = self.state / "intergen" / Path(self._configured_file()).name
        self.assertTrue(
            expected.parent.is_dir(),
            f"the user state directory {expected.parent} was not created")
        targets = [t for t in (getattr(h, "baseFilename", None)
                               for h in logging.getLogger().handlers) if t]
        self.assertIn(str(expected), targets,
                      f"no handler writes {expected}; handlers: {targets}")
        self.assertNotIn(self._configured_file(), targets,
                         "a user service must not be writing the root path")

    def test_the_event_log_follows_the_same_rule(self):
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.state)}), \
                mock.patch("intergen.metrics.os.geteuid", return_value=1000):
            el = EventLogger()
        self.assertEqual(el._log_dir, self.state / "intergen")

    def test_a_root_deployment_keeps_the_configured_directory(self):
        with mock.patch("intergen.metrics.os.geteuid", return_value=0):
            el = EventLogger(log_dir=str(Path(self._td.name) / "varlog"))
        self.assertEqual(el._log_dir, Path(self._td.name) / "varlog")


if __name__ == "__main__":
    unittest.main()
