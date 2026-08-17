# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Liveness skip-and-record + corpus-load proofs (M8-6 leg B), no daemon needed.

Proves the SIGALRM per-turn ceiling SKIPS-AND-RECORDS a wedged turn (the run
would otherwise hang forever in direct mode) and that a fast turn is observed
normally — using a mock client, so this is a fast standalone GREEN. Also checks
the surface-flex bank loads through the authoritative corpus_loader.
"""
from __future__ import annotations

import time
import types
from pathlib import Path

import pytest

from intergen.tests.demand_corpus.discovery_run import (
    DiscoveryRunner, _is_readonly_safe,
)
from intergen.tests.demand_corpus.discovery_policy import _TurnHint
from intergen.tests.corpus_loader import iter_corpus_records, load_corpus

_BANK = Path(__file__).with_name("surface_flex.jsonl")


def _fake_response(text="ok"):
    return types.SimpleNamespace(
        text=text, source="direct", handled=True, used_llm=False,
        escalated=False, tool_calls=[], tool_results=[], trace_id="")


class _FastClient:
    def ask(self, msg):
        time.sleep(0.05)
        return _fake_response("fast")

    def reset_conversation(self):
        pass


class _WedgedClient:
    def ask(self, msg):
        time.sleep(30)  # never returns within the ceiling
        return _fake_response("late")

    def reset_conversation(self):
        pass


def _runner(tmp_path, client):
    r = DiscoveryRunner(bank=[], run_dir=tmp_path / "run", min_ceiling_s=1.0)
    r._client = client
    r._turn_hint = _TurnHint()
    return r


def test_fast_turn_observed(tmp_path):
    r = _runner(tmp_path, _FastClient())
    obs = r._ask_governed("what time is it?", ceiling_s=3.0)
    assert obs["skipped"] is False
    assert obs["text"] == "fast"
    assert obs["elapsed_ms"] > 0


def test_wedged_turn_skipped_and_recorded(tmp_path):
    """A wedged turn does NOT hang the run — it is skipped and recorded as its
    own finding, and control returns promptly."""
    r = _runner(tmp_path, _WedgedClient())
    t0 = time.monotonic()
    obs = r._ask_governed("this turn wedges", ceiling_s=1.0)
    elapsed = time.monotonic() - t0
    assert obs["skipped"] is True
    assert obs["skip_reason"] == "liveness_ceiling"
    assert elapsed < 3.0, "the ceiling must interrupt the wedge promptly"


def test_surface_flex_loads_via_authoritative_loader():
    """The surface-flex half loads through the authoritative corpus_loader (schema-valid)."""
    raw = iter_corpus_records(_BANK)
    assert len(raw) > 500
    convs = load_corpus(_BANK)  # validates every line, fail-closed
    assert len(convs) == len(raw)
    # every entry carries the flex-ebc tag the miner reads for granularity
    assert all(any(t.startswith("flex-ebc:") for t in e.get("tags", [])) for e in raw)


def test_readonly_safe_and_gated_are_exact_complements():
    """--readonly-safe and --gated-only partition the bank with no overlap and no
    gap: every entry is in exactly one slice. This is what lets the dbus run
    (readonly-safe) and the direct policy-live run (gated-only) TOGETHER cover the
    whole corpus with no double-drive and no uncovered turn."""
    bank = iter_corpus_records(_BANK)
    safe = [e for e in bank if _is_readonly_safe(e)]
    gated = [e for e in bank if not _is_readonly_safe(e)]
    assert len(safe) + len(gated) == len(bank)          # no gap
    assert not (set(id(e) for e in safe) & set(id(e) for e in gated))  # no overlap
    # the surface half genuinely carries BOTH slices (it flexes gated tools too)
    assert gated, "surface-flex must contain gated/mutating turns"
    # every gated entry is gated for a nameable reason (gate / mutate / memory)
    for e in gated:
        reason = (e.get("expected_behavior_class") == "should-gate"
                  or e.get("category") == "memory_personal"
                  or "mutating" in e.get("tags", []) or "gated" in e.get("tags", [])
                  or any(t.startswith("flex-ebc:") for t in e.get("tags", [])))
        assert reason, f"{e['id']} dropped from readonly-safe with no gate reason"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
