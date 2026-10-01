# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The CLI's own top-level description must say what `ask` does with no service.

`intergen/cli.py`'s module docstring is the first thing a reader of the file --
or of any reference generated from it -- is told the command line does. At this
snapshot it says:

    This connects via D-Bus to the running InterGen daemon. If the daemon
    isn't running, it starts a direct session (useful for development).

That is the behaviour the command was changed AWAY from. `cmd_ask` and
`cmd_ask_frontier` now report the state of the user service and exit 2 when
nothing owns the assistant's bus name; the in-process session survives only
behind `--direct`, which `main()` says "is never implied". The command's own
usage text agrees with the code -- "Without the service running the command
reports how to start it and exits non-zero; --direct answers in this process
instead" -- so the module docstring is the one surface left describing the
removed fallback, and it never names `--direct` at all.

What is pinned here:

  * the behaviour, read from the command itself: with nothing on the bus,
    `ask` exits 2 and reports the service rather than answering;
  * the top-level description of the module must describe THAT behaviour: it
    must not promise a session started on the daemon's absence, and it must
    name the `--direct` opt-in that the developer path actually requires.

A reader who acts on the docstring expects an answer and gets exit 2.
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from intergen import cli


def _ask_with_no_service_on_the_bus():
    """Run `cmd_ask` with nothing owning the assistant's bus name.

    Hermetic: `daemon_has_owner` is answered False, so the D-Bus branch is
    never entered, and the only outward call left on the path is the
    `systemctl --user is-active` probe in `_user_service_state`, which is
    answered here. The `--direct` branch is not taken, so no assistant is
    constructed and `intergen.dbus_daemon` is never imported.
    """
    out, err = io.StringIO(), io.StringIO()
    code = None
    probe = mock.Mock(stdout="inactive", stderr="", returncode=0)
    with mock.patch.object(cli, "daemon_has_owner", return_value=False), \
            mock.patch.object(cli.subprocess, "run", return_value=probe):
        with redirect_stdout(out), redirect_stderr(err):
            try:
                cli.cmd_ask("what is my hostname?")
            except SystemExit as exc:
                code = exc.code
    return code, out.getvalue(), err.getvalue()


class TheModuleDescriptionMatchesWhatAskDoesTests(unittest.TestCase):

    def test_asking_with_no_service_reports_the_service_and_exits_two(self):
        """Ground truth: the behaviour the description has to describe."""
        code, out, err = _ask_with_no_service_on_the_bus()
        self.assertEqual(code, 2, err)
        self.assertIn("not running", err)
        self.assertIn("systemctl --user start intergen", err)
        self.assertNotIn("Answering in this process", out)

    def test_the_module_docstring_does_not_promise_the_removed_fallback(self):
        doc = cli.__doc__ or ""
        self.assertNotIn(
            "starts a direct session", doc,
            "the module docstring still promises that an absent daemon is "
            "answered by starting a session; cmd_ask reports the service "
            "state and exits 2 (cli.py lines 677-685), and the in-process "
            "session exists only behind --direct")

    def test_the_module_docstring_names_the_direct_opt_in(self):
        doc = cli.__doc__ or ""
        self.assertIn(
            "--direct", doc,
            "the module docstring never names the --direct opt-in, so its "
            "reader is not told that answering in this process is something "
            "they must ask for; main() says --direct 'is never implied'")


if __name__ == "__main__":
    unittest.main()
