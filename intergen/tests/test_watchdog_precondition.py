# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Watchdog precondition gate — the provisioning-aware quiet-hold semantics.

Regression guard for option A of the embedding self-heal: the embed watchdog is
created even when the model is absent at startup, and a `precondition` (model
present on disk) gates it. While the prerequisite is unmet — the first-boot
provisioning window — the watchdog must HOLD quietly: no health probe, no failure
count, and crucially NO restart-budget spend, so a normal provisioning window
never burns retries or fires a false CRITICAL give-up. Only once the model is
present does a dead service count as a failure and draw on the bounded budget,
preserving the loud-critical signal for a genuine post-provisioning death.
"""
from __future__ import annotations

import time

import intergen.watchdog as wmod
from intergen.watchdog import Watchdog


def _wait_until(predicate, timeout: float = 3.0, poll: float = 0.02) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(poll)
    return predicate()


def test_precondition_false_holds_without_spending_budget():
    """Prerequisite never met (model still provisioning) → quiet hold: the
    watchdog never restarts, never gives up, and spends none of its budget,
    even though the health check would report unhealthy."""
    restart_calls: list[int] = []
    giveups: list[str] = []
    wd = Watchdog(
        precondition=lambda: False,                 # model not present yet
        health_check=lambda: False,                 # would be unhealthy IF probed
        restart_action=lambda: restart_calls.append(1) or True,
        on_failure=lambda m: giveups.append(m),
        check_interval=0.02,
        max_restarts=2,
    )
    wd.start()
    time.sleep(0.4)  # ~20 ticks — ample to act if the gate were not holding
    wd.stop()
    assert restart_calls == []        # never attempted a restart
    assert giveups == []              # never fired the loud give-up
    assert wd.total_restarts == 0     # no budget spent during the hold


def test_precondition_true_but_failing_gives_up_loud():
    """Prerequisite met (model present) and the service stays dead → the
    watchdog spends its bounded budget and fires the give-up, preserving the
    loud signal for a genuine post-provisioning death."""
    giveups: list[str] = []
    wd = Watchdog(
        precondition=lambda: True,                  # model present
        health_check=lambda: False,                 # embedder dead
        restart_action=lambda: False,               # restart keeps failing
        on_failure=lambda m: giveups.append(m),
        check_interval=0.02,
        max_restarts=2,
    )
    wd.start()
    fired = _wait_until(lambda: bool(giveups))
    wd.stop()
    assert fired, "a present-model embedder that stays dead must give up loudly"
    assert wd.total_restarts == 2     # budget bounded at max_restarts


def test_precondition_flip_starts_recovery_only_after_ready():
    """The provisioning-window → model-lands transition: no restart attempt
    while the prerequisite is unmet, then recovery begins the moment it is."""
    ready = {"v": False}
    restart_calls: list[int] = []

    def restart() -> bool:
        restart_calls.append(1)
        return True  # the bind succeeds once the model is present

    # Neutralize the 60s post-success cooldown so the success path does not
    # block the test.
    orig_cooldown = wmod._RESTART_COOLDOWN
    wmod._RESTART_COOLDOWN = 0
    wd = Watchdog(
        precondition=lambda: ready["v"],
        health_check=lambda: False,                 # not up until a restart binds it
        restart_action=restart,
        check_interval=0.02,
        max_restarts=3,
    )
    try:
        wd.start()
        time.sleep(0.3)  # holding — model absent
        assert restart_calls == [], "must not restart during the provisioning hold"
        ready["v"] = True  # model lands
        acted = _wait_until(lambda: bool(restart_calls))
        wd.stop()
        assert acted, "must begin recovery once the model is present"
    finally:
        wmod._RESTART_COOLDOWN = orig_cooldown
