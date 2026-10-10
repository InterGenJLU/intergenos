# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""InterGen Health Check Aggregator — B-007.

Collects health data from all components and produces a structured report
matching the WebSocket protocol §8 format. Provides a single API surface
for the web server, console, and governance dashboard to query system
health without each component assembling its own ad-hoc data.
"""

from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from intergen.config import panel_netloc

logger = logging.getLogger(__name__)

# How long the panel's line waits for its address to accept a connection. The
# address is on this machine, so an answer is immediate; this bounds a stall.
PANEL_CONNECT_TIMEOUT_S = 2.0


@dataclass
class HealthCheck:
    """A single health check result."""
    name: str
    status: str       # "green", "yellow", "red"
    summary: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class HealthLayer:
    """A named group of related health checks."""
    name: str
    checks: list[HealthCheck] = field(default_factory=list)


class HealthAggregator:
    """Collects health data from all InterGen components.

    Usage:
        agg = HealthAggregator(
            llama_manager=daemon._llama,
            watchdog=daemon._watchdog,
            governance=daemon._governance,
        )
        report = agg.collect()
        # report -> {layers: [...], summary: {green: N, yellow: N, red: N}}

    Components that are not available (None) are reported as
    "unavailable" (yellow) rather than silently omitted — the
    operator needs to know when a subsystem is missing.
    """

    def __init__(self, *,
                 llama_manager: Any = None,
                 watchdog: Any = None,
                 governance: Any = None,
                 audit_log_count: int | None = None,
                 web_connections: int | Callable[[], int] | None = None,
                 web_server: Any = None,
                 panel_state: Callable[[], str | None] | None = None,
                 panel_address: tuple[str, int] | None = None,
                 ) -> None:
        self._llama = llama_manager
        self._watchdog = watchdog
        self._governance = governance
        self._audit_log_count = audit_log_count
        self._web_connections = web_connections
        self._web_server = web_server
        # The panel (the web server behind the panel window, the page and the
        # console client): panel_state answers why it is not serving, in
        # words, or None while its own bind holds its address; panel_address
        # is that address. The daemon hands over its own reading of both.
        self._panel_state = panel_state
        self._panel_address = panel_address

    def set_web_server(self, web_server: Any) -> None:
        """Bind the live web server after construction.

        The daemon builds this aggregator BEFORE the web server exists -- the
        web server takes the aggregator -- and every later health request is
        served from this same instance, so a connection count captured at
        construction could never become true. The handle is bound here instead,
        and read at each collect().
        """
        self._web_server = web_server

    def collect(self) -> dict[str, Any]:
        """Run all health checks and return the protocol §8 report."""
        layers = [
            self._check_model_server(),
            self._check_daemon(),
            self._check_system(),
        ]
        return self._format_report(layers)

    def _check_model_server(self) -> HealthLayer:
        checks: list[HealthCheck] = []

        # llama-server process
        if self._llama:
            running = self._llama.is_running()
            checks.append(HealthCheck(
                name="llama-server process",
                status="green" if running else "red",
                # The parser concatenates the two f-strings BEFORE a trailing
                # conditional applies, so an `if running else ""` at the end
                # governed the whole joined string rather than the PID tail:
                # the 'Not running' half was reachable only when running was
                # True, where the inner conditional picks 'Running' anyway. A
                # stopped engine therefore arrived at the console
                # (console/shell.py) and the panel (web/app.js) with an empty
                # summary -- a red dot, the check's name, a colon and nothing
                # -- on the one check whose job is to name the failure, while
                # its neighbours in the same layer carried their failing text.
                summary=(f"{'Running' if running else 'Not running'} "
                         f"(PID {self._llama.pid or '?'})"),
            ))
        else:
            checks.append(HealthCheck(
                name="llama-server process",
                status="yellow",
                summary="llama_manager not initialized",
            ))

        # Model loaded
        if self._llama:
            try:
                health = self._llama.health()
                model_loaded = health.model_loaded if hasattr(health, 'model_loaded') else running
                model_name = self._llama.model_name
                checks.append(HealthCheck(
                    name="Model loaded",
                    status="green" if model_loaded else "yellow",
                    summary=f"{model_name}" if model_loaded else "No model loaded",
                ))
            except Exception:
                checks.append(HealthCheck(
                    name="Model loaded",
                    status="yellow",
                    summary="Could not determine model status",
                ))
        else:
            checks.append(HealthCheck(
                name="Model loaded",
                status="yellow",
                summary="llama_manager not initialized",
            ))

        # API reachable: the chat server's /health on the address its own
        # manager names, which follows the configured port.
        if self._llama:
            reachable = False
            where = "the chat server's address"
            reason = ""
            try:
                import urllib.parse
                import urllib.request
                endpoint = urllib.parse.urlsplit(self._llama.get_endpoint())
                where = endpoint.netloc
                req = urllib.request.Request(
                    f"{endpoint.scheme}://{endpoint.netloc}/health",
                    method="GET",
                )
                with urllib.request.urlopen(req, timeout=2) as resp:
                    reachable = resp.status == 200
            except Exception as e:  # noqa: BLE001 — a health read never raises
                reason = f": {e}"
            checks.append(HealthCheck(
                name="API reachable",
                status="green" if reachable else "red",
                summary=f"{where} responding" if reachable
                        else f"{where} unreachable{reason}",
            ))
        else:
            checks.append(HealthCheck(
                name="API reachable",
                status="yellow",
                summary="llama_manager not initialized",
            ))

        # Context window
        context_size = self._llama.context_size if self._llama else 0
        checks.append(HealthCheck(
            name="Context window",
            status="green" if context_size > 0 else "yellow",
            summary=f"{context_size:,} tokens available" if context_size
                    else "Not determined",
        ))

        return HealthLayer(name="Model Server", checks=checks)

    def _check_daemon(self) -> HealthLayer:
        checks: list[HealthCheck] = []

        # D-Bus service
        dbus_registered = False
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib
            bus = Gio.bus_get_sync(Gio.BusType.SESSION)
            result = bus.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus",
                "org.freedesktop.DBus", "NameHasOwner",
                GLib.Variant("(s)", ("com.intergenos.InterGen",)),
                GLib.VariantType("(b)"),
                Gio.DBusCallFlags.NONE, 1000,
            )
            dbus_registered = result.unpack()[0]
        except Exception:
            pass
        checks.append(HealthCheck(
            name="D-Bus service",
            status="green" if dbus_registered else "yellow",
            summary="com.intergenos.InterGen registered" if dbus_registered
                    else "D-Bus service not running",
        ))

        # Watchdog
        if self._watchdog:
            wd_running = getattr(self._watchdog, 'is_running', False)
            checks.append(HealthCheck(
                name="Watchdog",
                status="green" if wd_running else "red",
                summary="Polling every 30s" if wd_running
                        else "Watchdog not running",
            ))
        else:
            checks.append(HealthCheck(
                name="Watchdog",
                status="yellow",
                summary="Watchdog not initialized",
            ))

        # Governance
        if self._governance:
            hs = self._governance.health_snapshot()
            hash_ok = hs.get("hash_verified", False)
            tier_name = hs.get("autonomy_tier_name", "?")
            checks.append(HealthCheck(
                name="Governance",
                status="green" if hash_ok else "red",
                summary=f"Hash {'verified' if hash_ok else 'UNVERIFIED — TAMPER'}"
                        f"; Tier {tier_name}",
            ))
        else:
            checks.append(HealthCheck(
                name="Governance",
                status="yellow",
                summary="Governance engine not initialized",
            ))

        # Audit log
        checks.append(self._check_audit_log())

        # Web server
        checks.append(self._check_web_server())

        return HealthLayer(name="Daemon", checks=checks)

    def _check_audit_log(self) -> HealthCheck:
        """The dispatch ledger as it reads on disk, certifying nothing.

        This check was two constants: the status was the string "green" with no
        condition attached, and the summary was "<n> entries, 0 tamper events"
        built from a count the constructor was handed -- the literal 0 at the
        daemon's one construction, never written again. Nothing in this process
        opened, counted, hashed or chained an audit record, so the zero-tamper
        half was an integrity verdict on the user-owned ledger that nothing had
        established, and the entry count could not move.

        It now reports the two things it can measure through audit_log's own
        public surface, and nothing else. read_records() skips a line that does
        not parse, by design, so record_count() on its own cannot tell a ledger
        that is empty from one truncated or rewritten mid-record: the raw
        non-empty lines are counted here too and the difference is reported as
        unreadable records rather than dropped. No integrity check exists
        anywhere on this path, so this check makes no integrity claim; a
        hash-chained ledger would be a change in audit_log.py, not here.

        The ledger is read on demand (the report is built per request, not in
        the periodic broadcast), and the two passes are a scan of a per-user
        JSONL file that logrotate keeps to 30 days.
        """
        if self._audit_log_count is not None:
            # A caller that already holds the count. Labelled as handed in, so
            # a reader can tell a supplied figure from one read off the disk.
            return HealthCheck(
                name="Audit log",
                status="green",
                summary=f"{self._audit_log_count} entries "
                        f"(count supplied by the caller)",
            )
        try:
            from intergen import audit_log
            path = audit_log.default_log_path()
            parsed = audit_log.record_count(path)
            raw = 0
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    raw = sum(1 for line in f if line.strip())
        except Exception as exc:  # noqa: BLE001 -- a health check never raises
            logger.warning(
                "health: the dispatch ledger could not be read: %s", exc)
            return HealthCheck(
                name="Audit log",
                status="yellow",
                summary="Dispatch ledger could not be read",
            )
        unreadable = max(raw - parsed, 0)
        if unreadable:
            return HealthCheck(
                name="Audit log",
                status="red",
                summary=f"{parsed} entries read, {unreadable} "
                        f"{'record' if unreadable == 1 else 'records'} "
                        f"unreadable",
            )
        return HealthCheck(
            name="Audit log",
            status="green",
            summary=f"{parsed} entries read from the dispatch ledger",
        )

    def _check_web_server(self) -> HealthCheck:
        """The web server as something observed, or not observed at all.

        This check asked whether `self._web_connections >= 0`, where the
        attribute was an int assigned once in __init__ and never written again,
        and the one construction in the tree passed the literal 0. That
        comparison is true for every value that call can produce, so the yellow
        branch was unreachable and the check was green by construction: a
        machine whose bind attempts were exhausted, whose web thread never
        reached run_forever, still answered "Web server - green - 0 active
        connections" to the very client that had asked for it. The same process
        already counted its live web connections for /api/status, and that
        number never reached here.

        A count of 0 carries nothing on its own, because a server that never
        bound and a bound server with no clients produce the same figure. So the
        status follows the SOURCE of the reading: the web server itself, or a
        callable that reads it, is an observation, and so is a positive static
        count, which no unbound server could have produced. Anything else is
        reported yellow as not observed, because nothing was.

        When the daemon supplies the panel's own state (the panel is the web
        server behind the panel window, the page and the console client), that
        state is read first: red in the daemon's own words while the panel is
        not serving; while its own bind holds its address, green only if that
        address accepts a connection, and red naming the address when it does
        not. The green line names the address, and the live connection count
        when one is observed.
        """
        name = "Web server"
        if self._panel_state is not None:
            try:
                down = self._panel_state()
            except Exception as e:  # noqa: BLE001 — a health read never raises
                return HealthCheck(
                    name=name, status="red",
                    summary=f"the panel's state could not be read: {e}")
            if down is not None:
                return HealthCheck(name=name, status="red", summary=down)
            if self._panel_address is None:
                return HealthCheck(
                    name=name, status="yellow",
                    summary="the panel serves, but no address was supplied to "
                            "this check")
            host, port = self._panel_address
            where = panel_netloc(host, port)
            try:
                with socket.create_connection((host, port),
                                              timeout=PANEL_CONNECT_TIMEOUT_S):
                    pass
            except OSError as e:
                return HealthCheck(name=name, status="red",
                                   summary=f"{where} does not answer: {e}")
            count = self._web_connection_reading()
            observed = "" if count is None else f", {count} active connections"
            return HealthCheck(name=name, status="green",
                               summary=f"Running on {where}{observed}")
        count = self._web_connection_reading()
        if count is None:
            return HealthCheck(
                name=name,
                status="yellow",
                summary="Not observed - no live web-server reading available",
            )
        return HealthCheck(
            name=name,
            status="green",
            summary=f"{count} active connections",
        )

    def _web_connection_reading(self) -> int | None:
        """The live web-connection count, or None when nothing was observed.

        The web server answers None itself when its own bind never succeeded,
        and that is carried through rather than flattened to 0.
        """
        if self._web_server is not None:
            try:
                count = self._web_server.web_connection_count()
            except Exception as exc:  # noqa: BLE001 -- a check never raises
                logger.debug(
                    "health: the web server did not answer its count: %s", exc)
                return None
            return int(count) if count is not None else None
        source = self._web_connections
        if callable(source):
            try:
                return int(source())
            except Exception as exc:  # noqa: BLE001 -- a check never raises
                logger.debug(
                    "health: the web-connection source raised: %s", exc)
                return None
        if isinstance(source, int) and source > 0:
            return source
        return None

    def _check_system(self) -> HealthLayer:
        checks: list[HealthCheck] = []

        # Disk space
        try:
            stat = os.statvfs("/")
            free_gb = (stat.f_bsize * stat.f_bavail) / (1024 ** 3)
            status = "green" if free_gb > 10 else ("yellow" if free_gb > 2 else "red")
            checks.append(HealthCheck(
                name="Disk space",
                status=status,
                summary=f"{free_gb:.0f} GB free on /",
            ))
        except Exception:
            checks.append(HealthCheck(
                name="Disk space",
                status="yellow",
                summary="Could not determine disk usage",
            ))

        # Memory
        try:
            with open("/proc/meminfo") as f:
                meminfo = {}
                for line in f:
                    k, v = line.split(":", 1)
                    meminfo[k] = int(v.strip().split()[0])
            total_kb = meminfo.get("MemTotal", 0)
            avail_kb = meminfo.get("MemAvailable", 0)
            avail_gb = avail_kb / (1024 ** 2)
            status = "green" if avail_gb > 2 else ("yellow" if avail_gb > 0.5 else "red")
            checks.append(HealthCheck(
                name="Memory",
                status=status,
                summary=f"{avail_gb:.1f} GB available "
                        f"(of {total_kb/(1024**2):.0f} GB total)",
            ))
        except Exception:
            checks.append(HealthCheck(
                name="Memory",
                status="yellow",
                summary="Could not determine memory usage",
            ))

        # GPU
        try:
            result = subprocess.run(
                ["lspci"], capture_output=True, text=True, timeout=5,
            )
            vga_line = [l for l in result.stdout.split("\n")
                        if "VGA" in l or "vga" in l]
            if vga_line:
                checks.append(HealthCheck(
                    name="GPU",
                    status="green",
                    summary=vga_line[0].split(": ")[-1] if ": " in vga_line[0]
                            else vga_line[0][:60],
                ))
            else:
                checks.append(HealthCheck(
                    name="GPU",
                    status="yellow",
                    summary="No GPU detected",
                ))
        except Exception:
            checks.append(HealthCheck(
                name="GPU",
                status="yellow",
                summary="Could not query GPU",
            ))

        return HealthLayer(name="System", checks=checks)

    def _format_report(self, layers: list[HealthLayer]) -> dict[str, Any]:
        report_layers = []
        green = yellow = red = 0
        for layer in layers:
            layer_checks = []
            for c in layer.checks:
                layer_checks.append({
                    "name": c.name,
                    "status": c.status,
                    "summary": c.summary,
                })
                if c.status == "green":
                    green += 1
                elif c.status == "red":
                    red += 1
                else:
                    yellow += 1
            report_layers.append({
                "name": layer.name,
                "checks": layer_checks,
            })
        return {
            "layers": report_layers,
            "summary": {"green": green, "yellow": yellow, "red": red},
        }


def quick_health(**kwargs: Any) -> dict[str, Any]:
    """One-shot health aggregation — no persistent aggregator needed.

    Convenience function for callers that just need the report.
    """
    return HealthAggregator(**kwargs).collect()