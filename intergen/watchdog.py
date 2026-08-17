# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
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
# How long the watchdog waits, after exhausting its restart budget, before it
# tries once more.
#
# WHY THERE IS A RETRY AT ALL. Exhausting the budget used to stop the monitoring
# thread outright, so the assistant stayed silent for the rest of the session
# even after the reason it could not start had gone away — a GPU still held by a
# game that has since been closed, a model file still being downloaded, a
# display server still coming up. The budget exists to stop a broken server from
# being relaunched in a tight loop, and a long wait between attempts serves that
# purpose just as well as never trying again, without the part where a machine
# that is now fine stays mute until someone notices and restarts the daemon.
#
# Fifteen minutes: long enough that a genuinely broken engine is retried four
# times an hour rather than continuously, short enough that a user who frees the
# accelerator gets the assistant back without being told to reboot.
_RECOVERY_RETRY_INTERVAL = 900


def _format_interval(seconds: int) -> str:
    """Render a wait as text a person can check against a clock.

    The give-up message used to say `seconds // 60` minutes, so any interval
    under a minute was announced as "retrying every 0 minutes" — a sentence
    that promises a retry and denies the wait in the same breath. The shipped
    default (900s) never hit it, but a message the code is able to make untrue
    is a defect in the message, not a hypothetical.
    """
    seconds = int(seconds)
    minutes, remainder = divmod(seconds, 60)

    def _plural(value, unit):
        return f"{value} {unit}" if value == 1 else f"{value} {unit}s"

    if minutes and remainder:
        return f"{_plural(minutes, 'minute')} {_plural(remainder, 'second')}"
    if minutes:
        return _plural(minutes, "minute")
    return _plural(remainder, "second")


class Watchdog:
    """Monitors service health and triggers recovery actions."""

    def __init__(self, *,
                 health_check: Callable[[], bool],
                 restart_action: Callable[[], bool],
                 check_interval: int = _DEFAULT_CHECK_INTERVAL,
                 max_restarts: int = _MAX_RESTART_ATTEMPTS,
                 on_failure: Callable[[str], None] | None = None,
                 precondition: Callable[[], bool] | None = None,
                 recovery_interval: int = _RECOVERY_RETRY_INTERVAL):
        """
        Args:
            health_check: Returns True if service is healthy.
            restart_action: Returns True if restart succeeded.
            check_interval: Seconds between health checks.
            max_restarts: Max consecutive restart attempts before giving up.
            on_failure: Called with error message when max restarts exceeded.
            precondition: Optional gate. When it returns False the watchdog
                QUIETLY HOLDS — it does not probe health, count a failure, or
                spend the restart budget — and keeps polling. This models
                "waiting on a prerequisite": e.g. the embed model is still
                provisioning on first boot, so a not-yet-running embedder is
                expected, not a fault. Only once it returns True does a dead
                service count as a failure and draw on the budget (so a genuine
                post-provisioning death still logs loud). Default (None) = always
                ready, i.e. unchanged behavior.
            recovery_interval: Seconds to wait, after the restart budget is
                spent, before trying once more. The watchdog does NOT stop
                monitoring on exhaustion — it reports the failure once and then
                retries at this interval, so a machine whose blocking condition
                has cleared recovers on its own instead of staying silent until
                the daemon is restarted.
        """
        self._health_check = health_check
        self._restart_action = restart_action
        self._check_interval = check_interval
        self._max_restarts = max_restarts
        self._on_failure = on_failure
        self._precondition = precondition
        self._recovery_interval = recovery_interval
        # Set once when the budget is first spent, so the failure is announced
        # to the user exactly once rather than at every recovery attempt.
        self._exhausted = False

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

            # Precondition gate: if the prerequisite is not yet met (e.g. the
            # embed model is still provisioning), HOLD quietly — do not probe,
            # do not count a failure, do not spend the restart budget. A
            # not-yet-running service is expected here, not a fault. We also
            # clear any stale failure count so the first real failure after the
            # prerequisite lands starts from zero.
            if self._precondition is not None:
                try:
                    ready = self._precondition()
                except Exception as e:
                    logger.error("Watchdog precondition check failed: %s", e)
                    ready = False
                if not ready:
                    self._consecutive_failures = 0
                    continue

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
                    # The budget is spent. Report it ONCE — the failure is real
                    # and the user is entitled to see it — then wait a long
                    # time and try again, rather than stopping the thread and
                    # leaving the machine mute for the rest of the session even
                    # after the cause has cleared.
                    if not self._exhausted:
                        msg = (f"Max restarts ({self._max_restarts}) exceeded "
                               f"— retrying every "
                               f"{_format_interval(self._recovery_interval)}")
                        logger.error(msg)
                        self._exhausted = True
                        if self._on_failure:
                            self._on_failure(msg)
                    logger.info(
                        "recovery hold: waiting %ds before the next attempt",
                        self._recovery_interval)
                    if self._stop_event.wait(self._recovery_interval):
                        break
                    # One attempt per hold. The budget is reset to a single
                    # try, not refilled, so a permanently broken engine costs
                    # one launch per interval instead of another full burst.
                    self._total_restarts = self._max_restarts - 1
                    self._consecutive_failures = 2

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
                    # Recovered. Clear the exhausted flag so a LATER failure is
                    # announced again rather than being swallowed as a repeat
                    # of one the user was told about hours ago.
                    self._exhausted = False
                    # Cool down on the STOP EVENT, never on a bare sleep. stop()
                    # sets the event and joins with timeout=5, so an
                    # uninterruptible sleep here meant a stop landing in the
                    # cooldown either blocked for the rest of the minute or —
                    # past the join timeout — returned while this thread was
                    # still resident. Every other wait in this loop is already
                    # the stop-event wait, the exhaustion hold above included.
                    if self._stop_event.wait(_RESTART_COOLDOWN):
                        break
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
            # Surfaced so Status can say "the budget is spent and it is
            # retrying every N minutes" rather than leaving the user to infer
            # from silence whether anything is still trying.
            "restart_budget_exhausted": self._exhausted,
            "recovery_interval": self._recovery_interval,
        }
