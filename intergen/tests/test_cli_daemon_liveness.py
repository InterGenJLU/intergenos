# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""GBC003 G3-6: `intergen ask` must talk to a running daemon, never mistake a
busy daemon for a dead one and spawn a competing direct session.

On a development machine the daemon owned com.intergenos.InterGen (NameHasOwner=true) and answered
busctl Status instantly, yet `intergen ask` printed "InterGen daemon not running.
Starting direct session..." after ~10.2s. Cause: the daemon's single-threaded
GLib main loop cannot service a second call while doing inference, so the old
"Ask times out -> Status probe also times out -> assume dead -> direct fallback"
path mis-fired. The fix decides liveness with NameHasOwner (served by the
dbus-daemon, instant even while InterGen is busy) and waits ASK_TIMEOUT_MS for
the LLM. These tests pin that branching.

Amended 2026-09-22: with nothing on the bus the command no longer starts an
assistant inside the asking process. That session was never the service the
machine manages — `systemctl --user is-active intergen` read inactive while it
answered — so the command now reports the service state and the start command
and exits non-zero, and the in-process session stays behind --direct.
"""

import json
import unittest
from unittest.mock import patch

from intergen import cli


class TestCmdAskLiveness(unittest.TestCase):
    def test_running_daemon_is_used_with_long_timeout(self):
        ok_json = json.dumps({"response": "Hi, InterGenOS."})
        with patch.object(cli, "daemon_has_owner", return_value=True), \
             patch.object(cli, "try_dbus", return_value=ok_json) as m_try:
            with patch("builtins.print") as m_print:
                cli.cmd_ask("hello")
        # Talked to the daemon with the generous LLM timeout, not the 5s default.
        m_try.assert_called_once()
        self.assertEqual(m_try.call_args.args[0], "Ask")
        self.assertEqual(m_try.call_args.kwargs.get("timeout_ms"),
                         cli.ASK_TIMEOUT_MS)
        m_print.assert_any_call("Hi, InterGenOS.")

    def test_busy_daemon_does_not_spawn_direct_session(self):
        # daemon owns the name but the call returns None (still loading / busy):
        # must exit(2), NOT import+start a competing direct daemon.
        with patch.object(cli, "daemon_has_owner", return_value=True), \
             patch.object(cli, "try_dbus", return_value=None):
            with patch("intergen.dbus_daemon.InterGenDaemon") as m_daemon:
                with self.assertRaises(SystemExit) as ctx:
                    cli.cmd_ask("hello")
        self.assertEqual(ctx.exception.code, 2)
        m_daemon.assert_not_called()

    def test_absent_daemon_reports_the_service_and_starts_nothing(self):
        with patch.object(cli, "daemon_has_owner", return_value=False), \
             patch.object(cli, "try_dbus", return_value=None):
            with patch("intergen.dbus_daemon.InterGenDaemon") as m_daemon:
                with patch("builtins.print"):
                    with self.assertRaises(SystemExit) as caught:
                        cli.cmd_ask("hello")
        self.assertEqual(caught.exception.code, 2)
        m_daemon.assert_not_called()

    def test_absent_daemon_still_answers_in_process_behind_direct(self):
        with patch.object(cli, "daemon_has_owner", return_value=False), \
             patch.object(cli, "try_dbus", return_value=None):
            with patch("intergen.dbus_daemon.InterGenDaemon") as m_daemon:
                inst = m_daemon.return_value
                inst.ask.return_value = json.dumps({"response": "direct",
                                                    "handled": True})
                with patch("builtins.print"):
                    cli.cmd_ask("hello", direct=True)
        m_daemon.assert_called_once()
        inst.start_service.assert_called_once()
        inst.ask.assert_called_once_with("hello")


if __name__ == "__main__":
    unittest.main()
