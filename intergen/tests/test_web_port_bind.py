# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Web-server bind hardening — the cold-boot greeter port-8089 collision.

9B lane item 2. Extends the bind-ownership pattern the chat/embed ports already
carry to the web port: a held port (EADDRINUSE — the GDM greeter session's own
InterGen web server at cold boot) is a HANDLED condition, not an unhandled
"web server thread crashed" traceback with no recovery. start() returns False
instead of raising, stays not-running so the daemon's web watchdog rebinds once
the port frees, and marks running ONLY after OUR bind succeeds (a foreign holder
answering on the port never counts as ours). aiohttp bind mocked so it runs
anywhere.
"""

from __future__ import annotations

import errno
import unittest
from unittest import mock

from intergen.web_server import WebServer


class WebPortBindHardeningTests(unittest.IsolatedAsyncioTestCase):
    def _server(self) -> WebServer:
        return WebServer(host="127.0.0.1", port=8089)

    def _patched_bind(self, srv, *, start_side_effect):
        # Mock aiohttp's AppRunner + TCPSite and isolate the stats broadcaster.
        runner = mock.patch("intergen.web_server.web.AppRunner")
        site = mock.patch("intergen.web_server.web.TCPSite")
        # `new=` installs a plain MagicMock deliberately. patch.object detects
        # that _broadcast_system_stats is `async def` and would autospec an
        # AsyncMock, so CALLING it manufactures a coroutine — and the
        # asyncio.create_task patched just below is a MagicMock that never
        # schedules it. The coroutine is then collected unawaited, which
        # surfaces as a RuntimeWarning attributed to whatever test happens to
        # be running when the GC fires (measured: two of them landed on
        # installer/tests/test_forge_trace_after_lift.py, a different suite
        # entirely). A non-async mock returns a value instead of a coroutine,
        # so there is nothing to leak.
        stats = mock.patch.object(
            srv, "_broadcast_system_stats",
            new=mock.MagicMock(return_value=mock.MagicMock()))
        task = mock.patch("intergen.web_server.asyncio.create_task",
                          return_value=mock.MagicMock())
        R = runner.start()
        S = site.start()
        stats.start()
        task.start()
        self.addCleanup(runner.stop)
        self.addCleanup(site.stop)
        self.addCleanup(stats.stop)
        self.addCleanup(task.stop)
        R.return_value.setup = mock.AsyncMock()
        S.return_value.start = mock.AsyncMock(side_effect=start_side_effect)
        return R, S

    async def test_bind_collision_handled_not_crash(self):
        # WEDGE: a held port must be HANDLED — start() returns False and does NOT
        # raise. Pre-fix the OSError propagated as an unhandled thread crash with
        # no recovery.
        srv = self._server()
        self._patched_bind(
            srv, start_side_effect=OSError(errno.EADDRINUSE,
                                           "Address already in use"))
        ok = await srv.start()   # must not raise
        self.assertFalse(ok)
        self.assertFalse(srv.running)

    async def test_non_addrinuse_oserror_also_handled(self):
        # Any bind OSError is handled (watchdog retries), never a raise.
        srv = self._server()
        self._patched_bind(
            srv, start_side_effect=OSError(errno.EACCES, "Permission denied"))
        ok = await srv.start()
        self.assertFalse(ok)
        self.assertFalse(srv.running)

    async def test_bind_success_marks_running(self):
        srv = self._server()
        self._patched_bind(srv, start_side_effect=None)
        ok = await srv.start()
        self.assertTrue(ok)
        self.assertTrue(srv.running)

    async def test_already_running_is_noop(self):
        srv = self._server()
        srv._running = True
        ok = await srv.start()
        self.assertTrue(ok)

    async def test_retry_binds_after_port_frees(self):
        # The watchdog analogue at the unit level: first attempt collides
        # (False), second binds (True). start() is retriable WITHOUT re-setting
        # up the runner/middleware.
        srv = self._server()
        R, _ = self._patched_bind(
            srv, start_side_effect=[OSError(errno.EADDRINUSE, "in use"), None])
        first = await srv.start()
        second = await srv.start()
        self.assertFalse(first)
        self.assertTrue(second)
        self.assertTrue(srv.running)
        # Runner constructed exactly once across the two attempts.
        self.assertEqual(R.call_count, 1)
        # Middleware appended exactly once (no duplicate on the retry).
        self.assertEqual(srv._app.middlewares.count(srv._csp_middleware), 1)


if __name__ == "__main__":
    unittest.main()
