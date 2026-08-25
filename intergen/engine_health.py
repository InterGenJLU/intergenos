# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Engine-health reaction ladder — sustained runtime corruption.

The runtime semantic-health detector (:mod:`intergen.semantic_health`) flags one
generation at a time. This monitor is the ENGINE-SIDE aggregate reaction: it
watches the rolling stream of per-generation verdicts and, when corruption is
sustained rather than a one-off, escalates.

The ladder:

* **1 flag** — the router-side per-flag fallback + glass + this counter. (The
  router reaction is a separate consumer of ``LLMResponse.semantic_flags``; this
  module owns only the counter and the escalation.)
* **3 flagged generations within a 5-generation window** — escalate. The handler
  is injected by the daemon, which owns the engine, and runs on a BACKGROUND
  thread so a corrupt turn never stalls the engine from inside the request path.

The monitor only counts and triggers; what escalation DOES lives in the daemon's
handler. Since the removal of the bring-up audition (decided 2026-07-31) that
handler reports the condition loudly and leaves the engine alone: it does not
move the user's model onto the CPU behind their back. A cooldown after each
trigger prevents re-escalating on every subsequent generation.
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass

log = logging.getLogger(__name__)

DEFAULT_WINDOW = 5
DEFAULT_THRESHOLD = 3


@dataclass(frozen=True)
class TriggerSnapshot:
    """What was true at the moment an escalation fired.

    record() clears its rolling window when it triggers, and the handler runs
    afterwards on another thread — so by the time anyone asks the monitor how
    many flags are in the window, the honest answer is zero. An installed
    machine printed exactly that: "sustained semantic-corruption flags (0 in the
    recent five-generation window)", an alarm whose own number denies it. The
    count that fired the alarm is kept here instead, with the threshold and
    window it was measured against, so the report can state all three.
    """

    flagged: int
    threshold: int
    window: int


def _thread_scheduler(fn) -> None:
    threading.Thread(target=fn, name="engine-health-escalation", daemon=True).start()


class EngineHealthMonitor:
    """Rolling per-generation corruption counter with a 3-in-5 escalation."""

    def __init__(self, on_degraded, *, window: int = DEFAULT_WINDOW,
                 threshold: int = DEFAULT_THRESHOLD, scheduler=None) -> None:
        """``on_degraded()`` is the daemon's escalation handler. ``scheduler(fn)``
        runs it off the request path; the default spawns a daemon thread. Tests
        inject a synchronous scheduler.
        """
        self._on_degraded = on_degraded
        self._window = window
        self._threshold = threshold
        self._scheduler = scheduler or _thread_scheduler
        self._recent: deque[bool] = deque(maxlen=window)
        self._cooldown = 0
        self._lock = threading.Lock()
        self._last_trigger: TriggerSnapshot | None = None

    def record(self, flags) -> bool:
        """Record one served generation's flags (empty == clean). Returns True
        when this record crossed the escalation threshold (and scheduled the
        handler). Cheap and never raises into the caller."""
        trigger = False
        with self._lock:
            self._recent.append(bool(flags))
            if self._cooldown > 0:
                # A report just fired — hold off re-escalating for a full window
                # rather than firing on every subsequent generation.
                self._cooldown -= 1
            elif sum(self._recent) >= self._threshold:
                trigger = True
                # Captured BEFORE the clear below, which is what makes the
                # report able to state the number that fired it.
                self._last_trigger = TriggerSnapshot(
                    flagged=sum(self._recent),
                    threshold=self._threshold,
                    window=self._window,
                )
                self._cooldown = self._window
                self._recent.clear()
        if trigger:
            try:
                self._scheduler(self._on_degraded)
            except Exception as e:  # scheduling must never break a turn
                log.error("engine-health escalation scheduling failed: %s", e)
                return False
        return trigger

    def flagged_in_window(self) -> int:
        """Flags in the window RIGHT NOW. Zero immediately after a trigger,
        because the trigger clears the window — read last_trigger() for the
        count that fired an escalation."""
        with self._lock:
            return sum(self._recent)

    def last_trigger(self) -> TriggerSnapshot | None:
        """The snapshot of the most recent escalation, or None if none fired."""
        with self._lock:
            return self._last_trigger
