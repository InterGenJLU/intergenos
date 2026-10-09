# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The panel serves the address its configuration names, its stop ends clean
whether or not the bind held, and a bind that is refused is in the daemon's
status in words.

THE ADDRESS. The daemon used to bind its panel on 127.0.0.1:8089 written into
the code, with no configuration key, so a second daemon on the same machine
(the start-up integration case, a battery's daemon) met the resident daemon's
panel there, and on a machine with no resident daemon it served the real panel
port for as long as it ran. The address is now the configuration's
web_server.host and web_server.port, shipped as 127.0.0.1:8089. Only a loopback
address is accepted: the panel carries the person's session over plain HTTP and
a WebSocket, so it listens on this machine only, and a value it cannot use is
refused in words rather than replaced by a guess.

THE STOP. The panel's thread binds with a watchdog that retries a refused bind
on an interval. The stop used to clean up the server's runner underneath that
watchdog: at the next retry it made a site on the cleaned-up runner and the
thread died logging "Web server thread crashed (RuntimeError: Call
runner.setup() before making a site)". After a bind that held, nothing stopped
the thread's event loop at all, so the stop waited out its five-second join and
left the thread running. A stop now ends the thread in both cases, and one that
arrives before the first bind attempt binds nothing.

THE STATUS. A refused bind was a log line only; the daemon's status had no word
about the panel. It now carries ``panel_down`` (None while the panel serves,
otherwise what is wrong, in words) and ``components.panel``.

The panel thread in the stop and status cases is started the way the daemon
starts it (a ready WebServer, then ``_run_web_server`` on its own thread), so
the same cases run against the code before the change.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest import mock

import pytest

from intergen import config as config_module
from intergen import dbus_daemon as dd
from intergen.config import Config
from intergen.tests.test_llama_launch import _free_port

DEADLINE = 15.0


class _Config:
    """A configuration holding only the keys a case names; every other key
    falls to the default its reader passes, as an unset key does."""

    def __init__(self, **values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


def _held_port(case: unittest.TestCase) -> int:
    """A loopback port this case holds with a listening socket for its whole
    run, so any other bind on it is refused."""
    holder = socket.socket()
    case.addCleanup(holder.close)
    holder.bind(("127.0.0.1", 0))
    holder.listen()
    return holder.getsockname()[1]


def _port_is_free(port: int) -> bool:
    probe = socket.socket()
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


class _Records(logging.Handler):
    """Every record a logger emits, from any thread, with an event set at the
    first one at or above the level the case waits for."""

    def __init__(self, wait_level: int) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []
        self.wait_level = wait_level
        self.seen = threading.Event()

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)
        if record.levelno >= self.wait_level:
            self.seen.set()


class _PanelCase(unittest.TestCase):

    def daemon(self) -> dd.InterGenDaemon:
        d = dd.InterGenDaemon()
        # A case that fails part-way still stops what it started.
        self.addCleanup(d.stop_service)
        return d

    def watch(self, name: str, wait_level: int = logging.WARNING) -> _Records:
        logger = logging.getLogger(name)
        records = _Records(wait_level)
        old_level = logger.level
        logger.addHandler(records)
        logger.setLevel(logging.DEBUG)
        self.addCleanup(logger.setLevel, old_level)
        self.addCleanup(logger.removeHandler, records)
        return records

    def start_panel_thread(self, d: dd.InterGenDaemon, port: int) -> None:
        """The panel thread on ``port``, started as the daemon starts it."""
        from intergen.web_server import WebServer
        d._web_server = WebServer(host="127.0.0.1", port=port)
        d._web_server.mark_ready()
        d._web_thread = threading.Thread(target=d._run_web_server,
                                         name="intergen-web", daemon=True)
        d._web_thread.start()

    def wait_bound(self, d: dd.InterGenDaemon) -> None:
        deadline = time.monotonic() + DEADLINE
        while time.monotonic() < deadline and not d._web_server.running:
            time.sleep(0.05)
        self.assertTrue(d._web_server.running,
                        f"the panel did not bind within {DEADLINE} s")

    def wait_refused(self, records: _Records) -> None:
        self.assertTrue(records.seen.wait(DEADLINE),
                        f"the daemon logged no refused bind within {DEADLINE} s")

    def get_page(self, port: int):
        """The panel's page on ``port``: its HTTP status, or the error."""
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/",
                                        timeout=5) as answer:
                return answer.status
        except OSError as e:
            return e


class PanelAddressIsConfiguredTests(_PanelCase):
    """The daemon serves the panel on the configuration's address."""

    def test_the_shipped_address_is_loopback_8089(self):
        empty = tempfile.mkdtemp(prefix="panel-config-")
        self.addCleanup(os.rmdir, empty)
        with mock.patch.object(config_module, "_SYSTEM_CONFIG",
                               config_module.Path(empty, "system.yml")), \
                mock.patch.object(config_module, "_USER_CONFIG",
                                  config_module.Path(empty, "user.yml")):
            cfg = Config()
        self.assertEqual(cfg.get("web_server.host"), "127.0.0.1")
        self.assertEqual(cfg.get("web_server.port"), 8089)
        self.assertEqual(dd._panel_address(cfg), ("127.0.0.1", 8089))

    def test_the_address_is_read_from_the_configuration(self):
        self.assertEqual(
            dd._panel_address(_Config(**{"web_server.port": 18089})),
            ("127.0.0.1", 18089))
        self.assertEqual(
            dd._panel_address(_Config(**{"web_server.host": "::1",
                                         "web_server.port": 18089})),
            ("::1", 18089))
        self.assertEqual(dd._panel_address(_Config()), ("127.0.0.1", 8089))

    def test_an_address_off_this_machine_is_refused_in_words(self):
        for host in ("0.0.0.0", "::", "localhost", "", None, 2130706433):
            with self.subTest(host=host):
                with self.assertRaises(ValueError) as caught:
                    dd._panel_address(_Config(**{"web_server.host": host}))
                words = str(caught.exception)
                self.assertIn("web_server.host", words)
                self.assertIn(repr(host), words)
                self.assertIn("loopback", words)

    def test_a_port_that_is_not_a_port_is_refused_in_words(self):
        for port in (0, 65536, -1, "8089", 8089.0, True, None):
            with self.subTest(port=port):
                with self.assertRaises(ValueError) as caught:
                    dd._panel_address(_Config(**{"web_server.port": port}))
                words = str(caught.exception)
                self.assertIn("web_server.port", words)
                self.assertIn(repr(port), words)

    @pytest.mark.reaches_a_server_it_starts_itself
    def test_the_daemon_serves_the_configured_port(self):
        port = _free_port()
        d = self.daemon()
        d._config = _Config(**{"web_server.port": port})
        d._start_panel()
        self.wait_bound(d)
        self.assertEqual(d._web_server.address, f"127.0.0.1:{port}")
        self.assertEqual(self.get_page(port), 200)


class PanelStopIsCleanTests(_PanelCase):
    """The stop ends the panel's thread with nothing logged as an error,
    whether the bind held or was refused."""

    def stop_cleanly(self, d: dd.InterGenDaemon) -> None:
        with self.assertNoLogs("intergen.dbus_daemon", level="ERROR"):
            d.stop_service()
            ended_with_the_stop = not d._web_thread.is_alive()
            # What the thread still does after the stop returns is part of the
            # stop: it is given time to do it while the log is watched, so a
            # watchdog retry that lands just after the stop's own wait is seen.
            d._web_thread.join(DEADLINE)
        self.assertTrue(ended_with_the_stop,
                        "the panel's thread was still running when the stop "
                        "returned")

    def test_the_stop_after_a_refused_bind_logs_no_crash_and_ends_the_thread(self):
        port = _held_port(self)
        records = self.watch("intergen.dbus_daemon")
        d = self.daemon()
        self.start_panel_thread(d, port)
        self.wait_refused(records)
        self.stop_cleanly(d)

    def test_the_stop_after_a_bind_that_held_ends_the_thread_and_frees_the_port(self):
        port = _free_port()
        d = self.daemon()
        self.start_panel_thread(d, port)
        self.wait_bound(d)
        self.stop_cleanly(d)
        self.assertTrue(_port_is_free(port),
                        f"127.0.0.1:{port} is still bound after the stop")

    def test_a_stop_before_the_first_attempt_ends_the_thread_with_nothing_bound(self):
        port = _free_port()
        d = self.daemon()
        self.start_panel_thread(d, port)
        self.stop_cleanly(d)
        self.assertTrue(_port_is_free(port),
                        f"127.0.0.1:{port} is bound after a stop that came "
                        f"before the bind")


class PanelDownIsReportedTests(_PanelCase):
    """A panel that is not serving is in the daemon's status, in words."""

    def status(self, d: dd.InterGenDaemon) -> dict:
        status = json.loads(d.status())
        self.assertIn("panel_down", status,
                      "the daemon's status says nothing about the panel")
        self.assertIn("panel", status.get("components", {}),
                      "the daemon's status lists no panel component")
        return status

    def test_a_refused_bind_is_in_the_status_in_words(self):
        port = _held_port(self)
        records = self.watch("intergen.dbus_daemon")
        d = self.daemon()
        self.start_panel_thread(d, port)
        self.wait_refused(records)
        status = self.status(d)
        self.assertFalse(status["components"]["panel"])
        down = status["panel_down"]
        self.assertIsInstance(down, str)
        self.assertIn(f"127.0.0.1:{port}", down)
        self.assertIn("held by another process", down)
        logged = " ".join(r.getMessage() for r in records.records
                          if r.levelno >= logging.WARNING)
        self.assertIn(f"127.0.0.1:{port} is held by another process", logged)

    def test_a_bind_that_held_is_in_the_status_as_serving(self):
        port = _free_port()
        d = self.daemon()
        self.start_panel_thread(d, port)
        self.wait_bound(d)
        status = self.status(d)
        self.assertTrue(status["components"]["panel"])
        self.assertIsNone(status["panel_down"])

    def test_a_bind_given_up_is_in_the_status_until_a_restart(self):
        port = _held_port(self)
        d = self.daemon()
        with mock.patch.object(dd, "WEB_BIND_RETRY_INTERVAL", 0.05), \
                mock.patch.object(dd, "WEB_BIND_MAX_ATTEMPTS", 2):
            self.start_panel_thread(d, port)
            d._web_thread.join(DEADLINE)
        self.assertFalse(d._web_thread.is_alive(),
                         "the panel's thread did not end after the bind budget")
        status = self.status(d)
        self.assertFalse(status["components"]["panel"])
        down = status["panel_down"]
        self.assertIn(f"127.0.0.1:{port}", down)
        self.assertIn("held by another process", down)
        self.assertIn("restart", down)

    def test_a_configured_address_refused_is_in_the_status_and_nothing_is_bound(self):
        d = self.daemon()
        d._config = _Config(**{"web_server.host": "0.0.0.0",
                               "web_server.port": 18089})
        d._start_panel()
        self.assertIsNone(d._web_thread)
        status = self.status(d)
        self.assertFalse(status["components"]["panel"])
        self.assertIn("web_server.host '0.0.0.0' is not a loopback",
                      status["panel_down"])


if __name__ == "__main__":
    unittest.main()
