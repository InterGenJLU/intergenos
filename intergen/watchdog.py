"""InterGen watchdog — health monitoring and auto-recovery.

Monitors llama-server health and restarts on failure.
Runs as a background thread within the InterGen daemon.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)

_DEFAULT_CHECK_INTERVAL = 30
_MAX_RESTART_ATTEMPTS = 3
_RESTART_COOLDOWN = 60


class Watchdog:
    """Monitors service health and triggers recovery actions."""

    def __init__(self, *,
                 health_check: Callable[[], bool],
                 restart_action: Callable[[], bool],
                 check_interval: int = _DEFAULT_CHECK_INTERVAL,
                 max_restarts: int = _MAX_RESTART_ATTEMPTS,
                 on_failure: Callable[[str], None] | None = None):
        """
        Args:
            health_check: Returns True if service is healthy.
            restart_action: Returns True if restart succeeded.
            check_interval: Seconds between health checks.
            max_restarts: Max consecutive restart attempts before giving up.
            on_failure: Called with error message when max restarts exceeded.
        """
        self._health_check = health_check
        self._restart_action = restart_action
        self._check_interval = check_interval
        self._max_restarts = max_restarts
        self._on_failure = on_failure

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._consecutive_failures = 0
        self._total_restarts = 0
        self._last_healthy = time.monotonic()
        self._running = False

    def start(self) -> None:
        """Start the watchdog monitoring thread."""
        if self._running:
            return
        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="intergen-watchdog"
        )
        self._thread.start()
        logger.info("Watchdog started (interval=%ds, max_restarts=%d)",
                     self._check_interval, self._max_restarts)

    def stop(self) -> None:
        """Stop the watchdog."""
        self._stop_event.set()
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Watchdog stopped")

    def _monitor_loop(self) -> None:
        """Main monitoring loop."""
        while not self._stop_event.is_set():
            self._stop_event.wait(self._check_interval)
            if self._stop_event.is_set():
                break

            try:
                healthy = self._health_check()
            except Exception as e:
                logger.error("Health check exception: %s", e)
                healthy = False

            if healthy:
                self._consecutive_failures = 0
                self._last_healthy = time.monotonic()
                continue

            self._consecutive_failures += 1
            logger.warning("Health check failed (%d consecutive)",
                           self._consecutive_failures)

            if self._consecutive_failures >= 2:
                if self._total_restarts >= self._max_restarts:
                    msg = (f"Max restarts ({self._max_restarts}) exceeded — "
                           "watchdog giving up")
                    logger.error(msg)
                    if self._on_failure:
                        self._on_failure(msg)
                    self._stop_event.set()
                    break

                logger.info("Attempting restart (%d/%d)",
                            self._total_restarts + 1, self._max_restarts)
                try:
                    success = self._restart_action()
                except Exception as e:
                    logger.error("Restart action failed: %s", e)
                    success = False

                self._total_restarts += 1
                if success:
                    logger.info("Restart successful")
                    self._consecutive_failures = 0
                    time.sleep(_RESTART_COOLDOWN)
                else:
                    logger.error("Restart failed")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def total_restarts(self) -> int:
        return self._total_restarts

    @property
    def seconds_since_healthy(self) -> float:
        return time.monotonic() - self._last_healthy

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "consecutive_failures": self._consecutive_failures,
            "total_restarts": self._total_restarts,
            "seconds_since_healthy": round(self.seconds_since_healthy, 1),
        }
