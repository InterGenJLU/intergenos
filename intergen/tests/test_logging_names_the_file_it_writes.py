# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The startup log line names the file that actually receives records.

`Config.setup_logging()` redirects a non-root process's log away from the
root-owned `/var/log/intergen` and into the user's XDG state directory, which
is right. Its closing line then reported the CONFIGURED path rather than the
one it had just chosen, so every `intergen` command announced

    Logging configured: level=INFO, file=/var/log/intergen/intergen.log

while writing to `~/.local/state/intergen/intergen.log`. Measured on this
machine: the announced file had not been touched since a package install five
days earlier, and the file that grew was the one in the state directory. A
diagnostic that points away from its own output sends anyone debugging to an
empty file.

Found while adding `intergen --version`, which prints this line like every
other command. Out of the scope of that command, resolved here.
"""

from __future__ import annotations

import logging
import unittest
from pathlib import Path
from unittest import mock

from intergen.config import Config


class LoggingNamesItsOwnFileTests(unittest.TestCase):
    """These cases call `logging.basicConfig(force=True)` through
    `setup_logging`, which replaces the ROOT logger's handlers for the whole
    process. Left in place they would point every later test in a suite run at
    a handler on a deleted temporary directory, so the original handlers and
    level are saved and put back around each case."""

    def setUp(self):
        root = logging.getLogger()
        self._saved_handlers = list(root.handlers)
        self._saved_level = root.level

    def tearDown(self):
        root = logging.getLogger()
        for h in list(root.handlers):
            if h not in self._saved_handlers:
                try:
                    h.close()
                except Exception:  # noqa: BLE001 — teardown must not mask a failure
                    pass
        root.handlers[:] = self._saved_handlers
        root.setLevel(self._saved_level)

    def _configure(self, tmp: Path, configured: str) -> str:
        """Run setup_logging with a configured path and return the path named
        in its closing "Logging configured" line."""
        named: list[str] = []
        real_info = logging.Logger.info

        def _capture(self, msg, *args, **kwargs):
            if isinstance(msg, str) and msg.startswith("Logging configured"):
                named.append(args[1])
            return real_info(self, msg, *args, **kwargs)

        cfg = Config()
        with mock.patch.object(cfg, "get", side_effect=lambda k, d=None: (
                configured if k == "logging.file" else
                "INFO" if k == "logging.level" else d)), \
             mock.patch.dict("os.environ", {"XDG_STATE_HOME": str(tmp)}), \
             mock.patch.object(logging.Logger, "info", _capture):
            cfg.setup_logging()
        self.assertEqual(len(named), 1,
                         "expected exactly one 'Logging configured' line")
        return named[0]

    def test_a_user_process_names_the_state_dir_file_not_var_log(self):
        """The case the defect was measured in: an unprivileged run."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            with mock.patch("os.geteuid", return_value=1000):
                named = self._configure(tmp, "/var/log/intergen/intergen.log")
        self.assertNotIn("/var/log/", named,
                         "the line still names the root-owned path a user "
                         "process never writes")
        self.assertTrue(named.endswith("intergen/intergen.log"), named)
        self.assertIn(str(tmp), named,
                      "the named file is not the one under XDG_STATE_HOME "
                      "that the handler was actually opened on")

    def test_the_named_file_is_the_one_that_receives_a_record(self):
        """Stronger than comparing strings: write a record and require it to
        land in the file the line named."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            with mock.patch("os.geteuid", return_value=1000):
                named = self._configure(tmp, "/var/log/intergen/intergen.log")
            marker = "marker-a1b2c3-record-must-land-here"
            logging.getLogger("intergen.tests").warning(marker)
            # Flush only THIS test's handlers. logging.shutdown() would close
            # every handler in the process, including ones a suite run's other
            # tests still hold.
            for h in logging.getLogger().handlers:
                h.flush()
            target = Path(named)
            self.assertTrue(target.exists(),
                            f"the named file {named} was never created")
            self.assertIn(marker, target.read_text(),
                          f"the record did not land in {named}, the file the "
                          "startup line named")


if __name__ == "__main__":
    unittest.main()
