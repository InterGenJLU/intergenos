# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The Web server health check must measure something (P2-06).

intergen/health.py:231 reads:

    status="green" if self._web_connections >= 0 else "yellow",

``_web_connections`` is an ``int`` that defaults to ``0``, and the ONLY
construction of ``HealthAggregator`` in the tree (intergen/dbus_daemon.py:2301,
step 13 of daemon start) passes the literal ``web_connections=0``.  Nothing ever
writes ``_web_connections`` again — the attribute is assigned exactly once, in
``__init__``, and there is no setter, property or live source.  So ``>= 0`` is
true for every value the daemon can produce: the yellow branch is dead code and
the check is green by construction.

Three consequences, all on the operator's only health surface:

  * The aggregator is built at dbus_daemon.py:2301, BEFORE the web thread starts
    at :2333.  When ``_web_bind_with_watchdog`` (dbus_daemon.py:2656) exhausts
    ``WEB_BIND_MAX_ATTEMPTS`` it logs CRITICAL, returns unbound, and
    ``_run_web_server`` skips ``run_forever`` — and nobody tells the aggregator.
    ``/health`` still answers "Web server — green — 0 active connections" on a
    machine with no web server bound at all.

  * web_server.py ``_build_health_report`` (:3031) copies the aggregator's report
    verbatim, adding only ``type`` and ``timestamp``; the aggregator-less
    fallback (:3046) hardcodes green too.  console/shell.py
    ``_format_health_report`` (:672) paints whatever status arrives.  There is no
    second guard anywhere on the path.

  * The same process already computes the REAL number: web_server.py:600 counts
    live web connections for ``/api/status``.  That number never reaches the
    aggregator, so ``/api/status`` can say 1 while ``/health``, asked over that
    very connection, says 0.

These cases assert the behaviour the quoted line promises — a check that goes
yellow when the web server was not observed, and a summary that can carry the
live count.  They are RED at this snapshot.

Hermetic: no service, no socket, no daemon, no network.  ``llama_manager`` is
None, so ``_check_model_server`` never reaches its urllib probe of
localhost:8080; ``gi`` is replaced with a stub whose ``require_version`` raises,
so ``_check_daemon``'s session-bus call is never made (health.py catches it and
reports the D-Bus check yellow); and ``subprocess.run`` is stubbed so the GPU
check never execs ``lspci``.  Only ``os.statvfs`` and ``/proc/meminfo`` are read
for real, and neither leaves the machine.
"""

from __future__ import annotations

import sys
import types
import unittest
from unittest import mock

from intergen.health import HealthAggregator, quick_health

# How intergen/dbus_daemon.py:2301-2307 builds it, minus the three component
# handles (irrelevant to the Web server check, and None keeps this case off the
# model server's socket).
DAEMON_KWARGS = {
    "llama_manager": None,
    "watchdog": None,
    "governance": None,
    "audit_log_count": 0,
    "web_connections": 0,
}


def _no_bus_no_exec():
    """Patch context that keeps a collect() off the session bus and off exec."""
    gi_stub = types.ModuleType("gi")

    def _require_version(*_a, **_k):
        raise RuntimeError("gi is blocked in this hermetic case")

    gi_stub.require_version = _require_version
    return (
        mock.patch.dict(sys.modules, {"gi": gi_stub}),
        mock.patch("intergen.health.subprocess.run",
                   side_effect=OSError("exec is blocked in this hermetic case")),
    )


def _collect(aggregator):
    bus_patch, exec_patch = _no_bus_no_exec()
    with bus_patch, exec_patch:
        return aggregator.collect()


def _quick(**kwargs):
    bus_patch, exec_patch = _no_bus_no_exec()
    with bus_patch, exec_patch:
        return quick_health(**kwargs)


def _web_check(report):
    """The one check named 'Web server' in a protocol §8 report."""
    for layer in report["layers"]:
        for check in layer["checks"]:
            if check["name"] == "Web server":
                return check
    raise AssertionError("no 'Web server' check in the health report")


class WebServerHealthCheckMustMeasureSomething(unittest.TestCase):

    def test_a_web_server_never_observed_is_not_reported_green(self):
        """Nothing was measured, so nothing may be certified.

        This is the daemon's own construction: web_connections=0, no web-server
        handle, built before the bind is even attempted.  On a machine where
        _web_bind_with_watchdog gave up, this is the state the report is built
        from — and it must not come out green.
        """
        check = _web_check(_quick(**DAEMON_KWARGS))
        self.assertNotEqual(
            "green", check["status"],
            "the Web server check is green having observed nothing — "
            "health.py:231 asks `self._web_connections >= 0`, which is true for "
            "every value dbus_daemon.py:2306 can pass, so a machine whose web "
            "server never bound reports %r" % (check,))

    def test_an_unbound_server_and_a_served_client_are_not_the_same_check(self):
        """The check must discriminate; today it is a constant function.

        Left: the give-up case — the daemon's literal 0, no server bound.
        Right: a web server serving one live connection (the count
        web_server.py:600 already computes for /api/status).
        A check worth printing does not answer both the same way.
        """
        unbound = _web_check(_quick(**DAEMON_KWARGS))
        serving = _web_check(_quick(**{**DAEMON_KWARGS, "web_connections": 1}))
        self.assertNotEqual(
            unbound["status"], serving["status"],
            "a machine with no web server bound and a machine serving a client "
            "produce the identical status %r — the yellow branch at "
            "health.py:231 is unreachable for every non-negative int"
            % (unbound["status"],))

    def test_the_live_connection_count_can_reach_the_report(self):
        """The aggregator is built ONCE; a live number must be able to reach it.

        dbus_daemon.py:2301 constructs the aggregator a single time and every
        later /health is served from that instance, so a count frozen at
        construction can never be true.  Probe the PUBLIC surfaces a fix could
        offer — a setter, a public attribute, a callable source, a web-server
        handle — and require that at least one of them makes a later collect()
        report the one live connection.  Private attribute pokes are not probed:
        the console client asking /health cannot reach into the daemon's
        internals, and neither should the web server.
        """
        tried: list[str] = []

        def _says_one(report):
            return "1 active connections" in _web_check(report)["summary"]

        # (a) a setter on the instance the daemon already holds
        aggregator = HealthAggregator(**DAEMON_KWARGS)
        setter = getattr(aggregator, "set_web_connections", None)
        if callable(setter):
            try:
                setter(1)
                if _says_one(_collect(aggregator)):
                    return
            except Exception as exc:      # pragma: no cover - probe only
                tried.append("set_web_connections raised %r" % (exc,))
        else:
            tried.append("no public set_web_connections()")

        # (b) a public attribute on that same instance
        aggregator = HealthAggregator(**DAEMON_KWARGS)
        try:
            aggregator.web_connections = 1
            if _says_one(_collect(aggregator)):
                return
            tried.append("public attribute web_connections is ignored by collect()")
        except Exception as exc:          # pragma: no cover - probe only
            tried.append("setting web_connections raised %r" % (exc,))

        # (c) a live callable handed in at construction
        try:
            if _says_one(_quick(**{**DAEMON_KWARGS,
                                   "web_connections": lambda: 1})):
                return
            tried.append("a callable web_connections is not called")
        except Exception as exc:
            tried.append("a callable web_connections raised %r" % (exc,))

        # (d) the web server itself, which knows its own connections
        class _StubWebServer:
            def web_connection_count(self):
                return 1

        try:
            if _says_one(_quick(**{**DAEMON_KWARGS,
                                   "web_server": _StubWebServer()})):
                return
            tried.append("a web_server handle is not consulted")
        except Exception as exc:
            tried.append("a web_server handle raised %r" % (exc,))

        self.fail(
            "no public way to give the aggregator the live connection count, so "
            "the report is frozen at the 0 passed at dbus_daemon.py:2306 and the "
            "client that asked /health is not counted: " + "; ".join(tried))


if __name__ == "__main__":
    unittest.main()
