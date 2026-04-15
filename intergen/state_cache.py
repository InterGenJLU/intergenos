"""InterGen system state cache — proactive polling for instant responses.

Runs background threads that periodically execute system commands and
cache the results. When the user asks "how much disk space?", the
answer comes from cache (0ms) instead of live execution (50-200ms+).

Cache freshness:
  - Static tier (5 min): hostname, kernel, OS, CPU, GPU, packages
  - Dynamic tier (30s):  disk, memory, uptime, load, services
  - Never cached:        processes, connections, logs, file contents

The user gets data that's at most 30-60 seconds old. For system
monitoring queries, this is perfectly acceptable — they're asking
a question, not watching a real-time dashboard.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_STATIC_INTERVAL = 300    # 5 minutes
_DYNAMIC_INTERVAL = 30    # 30 seconds


@dataclass
class CachedValue:
    value: str
    timestamp: float
    command: str
    stale_after: float


# Commands to cache, grouped by refresh interval
_STATIC_COMMANDS = {
    "hostname": "hostname",
    "kernel": "uname -r",
    "os_release": "cat /etc/os-release",
    "cpu_info": "lscpu | head -20",
    "gpu_info": "lspci | grep -i vga",
    "block_devices": "lsblk",
    "usb_devices": "lsusb",
    "network_interfaces": "ip -brief addr show",
}

_DYNAMIC_COMMANDS = {
    "disk_usage": "df -h",
    "memory_usage": "free -h",
    "uptime": "uptime",
    "load_average": "cat /proc/loadavg",
    "service_list": "systemctl list-units --type=service --state=running --no-pager --no-legend | head -30",
}


class StateCache:
    """Background system state cache with tiered refresh intervals."""

    def __init__(self):
        self._cache: dict[str, CachedValue] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._static_thread: threading.Thread | None = None
        self._dynamic_thread: threading.Thread | None = None

    def start(self) -> None:
        """Start background polling threads."""
        self._stop_event.clear()

        # Initial population (blocking — fills cache before daemon reports ready)
        self._poll_all(_STATIC_COMMANDS, _STATIC_INTERVAL)
        self._poll_all(_DYNAMIC_COMMANDS, _DYNAMIC_INTERVAL)
        logger.info("State cache populated: %d entries", len(self._cache))

        # Background threads for ongoing refresh
        self._static_thread = threading.Thread(
            target=self._poll_loop,
            args=(_STATIC_COMMANDS, _STATIC_INTERVAL),
            daemon=True, name="intergen-cache-static",
        )
        self._dynamic_thread = threading.Thread(
            target=self._poll_loop,
            args=(_DYNAMIC_COMMANDS, _DYNAMIC_INTERVAL),
            daemon=True, name="intergen-cache-dynamic",
        )
        self._static_thread.start()
        self._dynamic_thread.start()
        logger.info("State cache threads started (static=%ds, dynamic=%ds)",
                     _STATIC_INTERVAL, _DYNAMIC_INTERVAL)

    def stop(self) -> None:
        """Stop background polling."""
        self._stop_event.set()
        logger.info("State cache stopped")

    def get(self, key: str) -> str | None:
        """Get a cached value by key. Returns None if not cached."""
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            return entry.value

    def get_if_fresh(self, key: str) -> str | None:
        """Get cached value only if within its freshness window."""
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            age = time.monotonic() - entry.timestamp
            if age > entry.stale_after * 2:
                return None
            return entry.value

    def get_age(self, key: str) -> float | None:
        """Get the age in seconds of a cached value."""
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            return time.monotonic() - entry.timestamp

    def get_all(self) -> dict[str, str]:
        """Get all cached values as a dict."""
        with self._lock:
            return {k: v.value for k, v in self._cache.items()}

    def lookup_for_query(self, query: str) -> str | None:
        """Try to answer a query from cache based on keywords.

        Returns cached output if the query matches a cached key,
        or None if no cache hit (caller should execute live).
        """
        lower = query.lower()

        _QUERY_TO_CACHE = {
            "hostname": ["hostname", "host name", "machine name", "box called"],
            "kernel": ["kernel", "uname"],
            "os_release": ["os version", "operating system", "os release", "what os"],
            "cpu_info": ["cpu", "processor", "lscpu"],
            "gpu_info": ["gpu", "graphics", "vga", "video card"],
            "disk_usage": ["disk", "storage", "space", "df", "full"],
            "memory_usage": ["memory", "ram", "free"],
            "uptime": ["uptime", "how long"],
            "load_average": ["load", "load average"],
            "block_devices": ["block device", "lsblk", "drives", "partitions"],
            "usb_devices": ["usb"],
            "network_interfaces": ["network interface", "interfaces", "ip addr", "ip address", "ip link", "show network"],
            "service_list": ["services", "running services", "systemctl"],
        }

        for cache_key, keywords in _QUERY_TO_CACHE.items():
            if any(kw in lower for kw in keywords):
                value = self.get(cache_key)
                if value is not None:
                    return value

        return None

    @property
    def entry_count(self) -> int:
        with self._lock:
            return len(self._cache)

    # ── Internal ──

    def _poll_all(self, commands: dict[str, str], stale_after: float) -> None:
        """Execute all commands and update cache.

        CRITICAL: pollers must be invisible to system performance.
        - Commands run at lowest CPU/IO priority (nice 19, ionice idle)
        - Each command has a 5-second timeout
        - If system load is high (>80% of cores), skip this cycle
        - 100ms sleep between commands to prevent burst
        """
        # Skip if system is under heavy load
        try:
            load_1min = os.getloadavg()[0]
            cpu_count = os.cpu_count() or 1
            if load_1min > cpu_count * 0.8:
                logger.debug("System load high (%.1f/%d), skipping cache poll",
                             load_1min, cpu_count)
                return
        except (OSError, AttributeError):
            pass

        for key, cmd in commands.items():
            if self._stop_event.is_set():
                break
            try:
                # Run at lowest priority — never compete with user work
                nice_cmd = f"nice -n 19 ionice -c 3 {cmd}"
                result = subprocess.run(
                    nice_cmd, shell=True, capture_output=True, text=True,
                    timeout=5,
                )
                output = result.stdout.rstrip()
                if output:
                    with self._lock:
                        self._cache[key] = CachedValue(
                            value=output,
                            timestamp=time.monotonic(),
                            command=cmd,
                            stale_after=stale_after,
                        )
                # Brief pause between commands — prevent burst
                time.sleep(0.1)
            except subprocess.TimeoutExpired:
                logger.debug("Cache command timed out: %s", cmd)
            except Exception as e:
                logger.debug("Cache command failed (%s): %s", cmd, e)

    def _poll_loop(self, commands: dict[str, str], interval: float) -> None:
        """Background polling loop."""
        while not self._stop_event.is_set():
            self._stop_event.wait(interval)
            if self._stop_event.is_set():
                break
            self._poll_all(commands, interval)
