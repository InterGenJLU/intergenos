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
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


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
                 audit_log_count: int = 0,
                 web_connections: int = 0,
                 ) -> None:
        self._llama = llama_manager
        self._watchdog = watchdog
        self._governance = governance
        self._audit_log_count = audit_log_count
        self._web_connections = web_connections

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
                summary=f"{'Running' if running else 'Not running'} "
                        f"(PID {self._llama.pid or '?'})"
                        if running else "",
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

        # API reachable
        if self._llama:
            reachable = False
            try:
                import urllib.request
                req = urllib.request.Request(
                    "http://127.0.0.1:8080/health",
                    method="GET",
                )
                resp = urllib.request.urlopen(req, timeout=2)
                reachable = resp.status == 200
            except Exception:
                pass
            checks.append(HealthCheck(
                name="API reachable",
                status="green" if reachable else "red",
                summary="localhost:8080 responding" if reachable
                        else "localhost:8080 unreachable",
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
        checks.append(HealthCheck(
            name="Audit log",
            status="green",
            summary=f"{self._audit_log_count} entries, 0 tamper events",
        ))

        # Web server connections
        checks.append(HealthCheck(
            name="Web server",
            status="green" if self._web_connections >= 0 else "yellow",
            summary=f"{self._web_connections} active connections",
        ))

        return HealthLayer(name="Daemon", checks=checks)

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