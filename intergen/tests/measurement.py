# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Reading a pass rate honestly — the stated noise floor, in code.

At roughly 140 scenarios, a difference of a few points between two runs is
variance, not improvement. The ratified method says so and asks for the
arithmetic to travel with the number: every rate carries a bootstrap confidence
interval, and two runs whose intervals overlap are NOT a claimed improvement.

The resampling is deterministic. A confidence interval that moves when you
re-run the same comparison is one more thing to argue about; here the sampler
is seeded from the data itself, so the same run pair always yields the same
interval, on any machine, without storing anything.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

# Enough resamples for a stable 95% interval on corpora of this size without
# making a summary slow to produce.
DEFAULT_ITERATIONS = 2000
DEFAULT_CONFIDENCE = 0.95


@dataclass(frozen=True)
class Interval:
    """A pass rate with the range the evidence actually supports."""
    rate: float
    low: float
    high: float
    n: int
    confidence: float = DEFAULT_CONFIDENCE

    def overlaps(self, other: "Interval") -> bool:
        return not (self.high < other.low or other.high < self.low)

    def to_dict(self) -> dict:
        return {"rate": round(self.rate, 4), "low": round(self.low, 4),
                "high": round(self.high, 4), "n": self.n,
                "confidence": self.confidence}


class _Sampler:
    """A small deterministic pseudo-random source.

    Seeded from a digest of the data being resampled, so the interval for a
    given set of outcomes is reproducible by anyone who has those outcomes —
    no global random state, no seed to remember, nothing stored.
    """

    __slots__ = ("_state",)

    def __init__(self, seed_material: str) -> None:
        digest = hashlib.sha256(seed_material.encode()).digest()
        self._state = int.from_bytes(digest[:8], "big") | 1

    def next_index(self, upper: int) -> int:
        # xorshift64*, which is short, fast, and has a long enough period for a
        # few million draws.
        x = self._state
        x ^= (x >> 12) & 0xFFFFFFFFFFFFFFFF
        x ^= (x << 25) & 0xFFFFFFFFFFFFFFFF
        x ^= (x >> 27) & 0xFFFFFFFFFFFFFFFF
        self._state = x & 0xFFFFFFFFFFFFFFFF
        return ((self._state * 2685821657736338717) & 0xFFFFFFFFFFFFFFFF) % upper


def bootstrap_interval(outcomes: list[bool], *,
                       iterations: int = DEFAULT_ITERATIONS,
                       confidence: float = DEFAULT_CONFIDENCE,
                       label: str = "") -> Interval:
    """The pass rate of ``outcomes`` and its bootstrap confidence interval.

    ``outcomes`` is one boolean per graded unit — per family when families are
    the unit of measurement, per conversation otherwise. An empty list yields a
    zero rate with a full-width interval, which is the honest answer to "what do
    we know" when nothing was measured.
    """
    n = len(outcomes)
    if n == 0:
        return Interval(rate=0.0, low=0.0, high=1.0, n=0, confidence=confidence)
    passes = sum(1 for o in outcomes if o)
    rate = passes / n
    sampler = _Sampler(f"{label}|{n}|{passes}|"
                       + "".join("1" if o else "0" for o in outcomes))
    rates: list[float] = []
    for _ in range(iterations):
        hits = 0
        for _ in range(n):
            if outcomes[sampler.next_index(n)]:
                hits += 1
        rates.append(hits / n)
    rates.sort()
    tail = (1.0 - confidence) / 2.0
    low = rates[min(len(rates) - 1, int(tail * len(rates)))]
    high = rates[min(len(rates) - 1, int((1.0 - tail) * len(rates)))]
    return Interval(rate=rate, low=low, high=high, n=n, confidence=confidence)


@dataclass(frozen=True)
class Comparison:
    """Two runs, read against the noise floor rather than against each other."""
    before: Interval
    after: Interval

    @property
    def delta(self) -> float:
        return self.after.rate - self.before.rate

    @property
    def separated(self) -> bool:
        """True only when the intervals do NOT overlap — the one case where a
        difference may be called an improvement or a regression."""
        return not self.before.overlaps(self.after)

    def verdict(self) -> str:
        if not self.separated:
            return "within the noise floor — no change claimed"
        return "improved" if self.delta > 0 else "regressed"

    def to_dict(self) -> dict:
        return {"before": self.before.to_dict(), "after": self.after.to_dict(),
                "delta": round(self.delta, 4), "separated": self.separated,
                "verdict": self.verdict()}


def compare_runs(before: list[bool], after: list[bool], *,
                 iterations: int = DEFAULT_ITERATIONS,
                 confidence: float = DEFAULT_CONFIDENCE) -> Comparison:
    """Compare two runs' outcomes with the noise floor built in."""
    return Comparison(
        before=bootstrap_interval(before, iterations=iterations,
                                  confidence=confidence, label="before"),
        after=bootstrap_interval(after, iterations=iterations,
                                 confidence=confidence, label="after"))


def summarize_rate(outcomes: list[bool], *, unit: str = "conversation",
                   iterations: int = DEFAULT_ITERATIONS) -> str:
    """One line stating a rate WITH its interval, for a run summary.

    The interval is not decoration: a summary that prints 78% and nothing else
    invites a reader to compare it with last round's 74% and believe something
    happened.
    """
    interval = bootstrap_interval(outcomes, iterations=iterations, label=unit)
    if interval.n == 0:
        return f"  Pass rate ({unit}): nothing measured"
    return (f"  Pass rate ({unit}): {interval.rate * 100:.1f}% "
            f"[{interval.low * 100:.1f}–{interval.high * 100:.1f}% "
            f"at {int(interval.confidence * 100)}% confidence, n={interval.n}]")
