"""InterGen event logger and metrics tracker.

Simplified from JARVIS core/event_logger.py. Provides structured event
logging to file and in-memory metrics tracking for status reporting.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

_LOG_DIR = "/var/log/intergen"
_LOG_FILE = "events.jsonl"


@dataclass
class Event:
    category: str
    event: str
    message: str
    severity: str = "info"
    source: str = ""
    status: str = "success"
    latency_ms: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class EventLogger:
    """Structured event logger — writes JSON lines to /var/log/intergen/events.jsonl."""

    def __init__(self, log_dir: str = _LOG_DIR):
        self._log_dir = Path(log_dir)
        self._log_file: Path | None = None
        self._lock = Lock()
        self._setup_log_dir()

    def _setup_log_dir(self) -> None:
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            self._log_file = self._log_dir / _LOG_FILE
        except PermissionError:
            fallback = Path.home() / ".local" / "share" / "intergen" / "logs"
            fallback.mkdir(parents=True, exist_ok=True)
            self._log_file = fallback / _LOG_FILE
            logger.warning("Cannot write to %s, using %s", self._log_dir, fallback)

    def emit(self, category: str, event: str, message: str, *,
             severity: str = "info", source: str = "", status: str = "success",
             latency_ms: float | None = None,
             metadata: dict[str, Any] | None = None) -> None:
        """Emit a structured event."""
        evt = Event(
            category=category,
            event=event,
            message=message,
            severity=severity,
            source=source,
            status=status,
            latency_ms=latency_ms,
            metadata=metadata or {},
        )

        if self._log_file:
            with self._lock:
                try:
                    with open(self._log_file, "a") as f:
                        f.write(json.dumps(asdict(evt)) + "\n")
                except Exception as e:
                    logger.error("Failed to write event: %s", e)

        if severity == "error":
            logger.error("[%s] %s: %s", category, event, message)
        else:
            logger.debug("[%s] %s: %s", category, event, message)


class MetricsTracker:
    """In-memory metrics for status reporting."""

    def __init__(self):
        self._lock = Lock()
        self._counters: dict[str, int] = defaultdict(int)
        self._latencies: dict[str, list[float]] = defaultdict(list)
        self._start_time = time.monotonic()
        self._last_error: str | None = None
        self._last_error_time: float | None = None

    def increment(self, name: str, count: int = 1) -> None:
        with self._lock:
            self._counters[name] += count

    def record_latency(self, name: str, ms: float) -> None:
        with self._lock:
            bucket = self._latencies[name]
            bucket.append(ms)
            if len(bucket) > 1000:
                bucket[:] = bucket[-500:]

    def record_error(self, error: str) -> None:
        with self._lock:
            self._counters["errors"] += 1
            self._last_error = error
            self._last_error_time = time.time()

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            status = {
                "uptime_seconds": round(time.monotonic() - self._start_time, 1),
                "counters": dict(self._counters),
                "last_error": self._last_error,
                "last_error_time": self._last_error_time,
            }
            for name, bucket in self._latencies.items():
                if bucket:
                    status[f"latency_{name}_avg_ms"] = round(
                        sum(bucket) / len(bucket), 1
                    )
                    status[f"latency_{name}_p99_ms"] = round(
                        sorted(bucket)[int(len(bucket) * 0.99)], 1
                    )
            return status

    @property
    def uptime_seconds(self) -> float:
        return time.monotonic() - self._start_time

    @property
    def requests_handled(self) -> int:
        return self._counters.get("requests", 0)
