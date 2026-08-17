# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Watchdog: stop() is answered during the post-restart cooldown, and the
give-up message states a duration that is true.

TWO DEFECTS, both found while reviewing the serving-watchdog recovery work.

F1 — STOP WAS NOT ANSWERED DURING THE COOLDOWN. After a successful restart the
loop held with a bare `time.sleep(_RESTART_COOLDOWN)` — 60 seconds that nothing
can interrupt. Watchdog.stop() sets the stop event and joins with timeout=5, so
a stop landing inside that window blocked for the rest of the sleep (or, past
the 5s join, returned while a thread it believed stopped was still resident).
Every other wait in this loop is `self._stop_event.wait(...)`, including the
exhaustion hold a few lines above; the cooldown was the one that was not.

F2 — THE GIVE-UP MESSAGE COULD STATE SOMETHING FALSE. The text rendered the
retry interval as `recovery_interval // 60` minutes, so any interval under a
minute was announced as "retrying every 0 minutes" — a message telling the user
it will retry, and in the same breath that it will wait no time at all. The
shipped default is 900s so this is not reachable in production, but a message
the code can make untrue is a defect in the message.

These are daemon-free thread tests: a real Watchdog with real callbacks and a
tiny check interval, no llama-server and no D-Bus.
"""
from __future__ import annotations

import threading
import time

import intergen.watchdog as wmod
from intergen.watchdog import Watchdog


def _restart_once_then_healthy(state, restarted):
    """health_check: unhealthy until a restart happens, healthy after."""
    return state["healthy"]


def _make_restarting_watchdog(restarted, state, cooldown_marker=None):
    def restart_action():
        state["healthy"] = True          # the restart works
        restarted.set()
        return True

    return Watchdog(
        health_check=lambda: state["healthy"],
        restart_action=restart_action,
        check_interval=0.02,
        max_restarts=5,
    )


class TestStopIsAnsweredDuringTheCooldown:
    def test_stop_returns_promptly_when_it_lands_in_the_cooldown(self, monkeypatch):
        """The measurement: how long stop() itself takes.

        RED shape: with the bare sleep, stop() blocks for the remainder of the
        cooldown. GREEN shape: the stop event ends the wait at once.
        """
        monkeypatch.setattr(wmod, "_RESTART_COOLDOWN", 1.5)
        state = {"healthy": False}
        restarted = threading.Event()
        wd = _make_restarting_watchdog(restarted, state)

        wd.start()
        assert restarted.wait(timeout=5), "the watchdog never attempted a restart"
        time.sleep(0.05)                 # let the loop enter the cooldown
        t0 = time.monotonic()
        wd.stop()
        elapsed = time.monotonic() - t0

        assert elapsed < 0.5, (
            f"stop() took {elapsed:.2f}s — it waited out an uninterruptible "
            f"cooldown instead of being answered by the stop event")

    def test_the_thread_is_gone_when_stop_returns(self, monkeypatch):
        """A cooldown longer than stop()'s 5s join makes the leak observable.

        RED shape: stop() gives up joining after 5s and returns while the
        watchdog thread is still resident, sleeping out the rest of its
        cooldown — a thread the daemon believes it has stopped. GREEN shape:
        the thread is gone before stop() returns. This case is deliberately
        slow to fail and fast to pass.
        """
        monkeypatch.setattr(wmod, "_RESTART_COOLDOWN", 6.0)
        state = {"healthy": False}
        restarted = threading.Event()
        wd = _make_restarting_watchdog(restarted, state)

        wd.start()
        assert restarted.wait(timeout=5), "the watchdog never attempted a restart"
        time.sleep(0.05)
        wd.stop()

        assert wd._thread is not None
        assert not wd._thread.is_alive(), (
            "stop() returned while the watchdog thread was still alive in its "
            "cooldown — the daemon believes this thread is stopped")

    def test_a_stop_during_the_cooldown_still_ends_the_loop(self, monkeypatch):
        """The fix must not turn the cooldown into an early `continue`."""
        monkeypatch.setattr(wmod, "_RESTART_COOLDOWN", 1.5)
        state = {"healthy": False}
        restarted = threading.Event()
        wd = _make_restarting_watchdog(restarted, state)

        wd.start()
        assert restarted.wait(timeout=5)
        time.sleep(0.05)
        wd.stop()
        time.sleep(0.1)

        assert not wd.is_running
        assert not wd._thread.is_alive()

    def test_the_cooldown_still_happens_when_nobody_stops_it(self, monkeypatch):
        """The negative control: the cooldown is a real wait, not a no-op.

        If the fix had dropped the wait entirely both tests above would pass
        while the restart-storm protection was gone. Here the service stays
        broken, so the loop restarts, cools down, and restarts again — and the
        second restart must not arrive before the cooldown has elapsed.
        """
        monkeypatch.setattr(wmod, "_RESTART_COOLDOWN", 0.6)
        stamps: list[float] = []

        def restart_action():
            stamps.append(time.monotonic())
            return True                   # "succeeded", but the service stays dead

        wd = Watchdog(
            health_check=lambda: False,   # never recovers
            restart_action=restart_action,
            check_interval=0.02,
            max_restarts=50,
        )
        wd.start()
        deadline = time.monotonic() + 5
        while len(stamps) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        wd.stop()

        assert len(stamps) >= 2, "the watchdog did not restart twice in 5s"
        gap = stamps[1] - stamps[0]
        assert gap >= 0.55, (
            f"only {gap:.2f}s between restarts — the post-restart cooldown is "
            f"not being observed, so a broken engine gets a restart storm")


class TestTheGiveUpMessageStatesATrueDuration:
    def _giveup_message(self, recovery_interval):
        messages: list[str] = []
        wd = Watchdog(
            health_check=lambda: False,
            restart_action=lambda: False,     # never recovers, budget is spent
            on_failure=messages.append,
            check_interval=0.02,
            max_restarts=1,
            recovery_interval=recovery_interval,
        )
        wd.start()
        deadline = time.monotonic() + 5
        while not messages and time.monotonic() < deadline:
            time.sleep(0.02)
        wd.stop()
        assert messages, "the watchdog never announced that its budget was spent"
        return messages[0]

    def test_a_sub_minute_interval_is_not_announced_as_zero_minutes(self):
        msg = self._giveup_message(1)
        assert "0 minutes" not in msg, (
            f"the give-up message states a false wait: {msg!r}")
        assert "1 second" in msg, (
            f"a 1-second interval should be stated in seconds: {msg!r}")

    def test_the_shipped_default_still_reads_in_minutes(self):
        # 900s is the shipped default; the wording users actually see must not
        # regress into "900 seconds".
        assert wmod._format_interval(900) == "15 minutes"

    def test_interval_wording(self):
        cases = {
            1: "1 second",
            45: "45 seconds",
            60: "1 minute",
            120: "2 minutes",
            90: "1 minute 30 seconds",
            3600: "60 minutes",
        }
        for seconds, expected in cases.items():
            assert wmod._format_interval(seconds) == expected, (
                f"{seconds}s rendered as "
                f"{wmod._format_interval(seconds)!r}, expected {expected!r}")
