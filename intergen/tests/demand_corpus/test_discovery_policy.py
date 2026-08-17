# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""RED/GREEN acceptance for the discovery-run policy review responder.

The security property the mass discovery run rests on:
  - a MUTATING/PRIVILEGED dispatch under the policy NEVER executes (zero side
    effects) AND its staged (tool, args) lands in the dispatch ledger — the
    routing observation is captured without the action happening;
  - a READ-ONLY dispatch executes normally and NEVER reaches the policy callback
    (read-only is implicit-ALLOW upstream);
  - privileged stays fail-closed-DENY even on a malformed decision shape.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from intergen.tool_registry import ToolRegistry
from intergen.interfaces.types import ToolCall
from intergen.interfaces.provenance import Provenance
from intergen.tests.demand_corpus.discovery_policy import (
    DispatchLedger,
    make_policy_review_callback,
)


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.discover_tools()
    return reg


def test_mutating_write_denied_and_recorded_zero_side_effect(tmp_path):
    """GREEN: write_file (privileged) is refused, the file is NOT written, and
    the staged (tool, args) is recorded in the ledger."""
    reg = _registry()
    ledger = DispatchLedger()
    cb = make_policy_review_callback(ledger)

    target = tmp_path / "should_not_exist.txt"
    call = ToolCall(
        name="write_file",
        arguments={"path": str(target), "content": "SIDE EFFECT"},
        source_of_request=Provenance.USER_DIRECT,
    )
    result = reg.execute(call, review_callback=cb)

    # zero side effect: the write never happened
    assert not target.exists(), "policy DENY must prevent the write (side effect leaked)"
    assert result.success is False
    assert getattr(result, "executed", False) is False
    # the routing observation was captured
    assert len(ledger) == 1
    staged = ledger.all()[0]
    assert staged.tool == "write_file"
    assert staged.arguments.get("path") == str(target)
    assert staged.verdict == "deny"
    assert staged.tier in ("privileged", "mutating")


def test_readonly_read_executes_and_never_calls_back(tmp_path):
    """GREEN: read_file (read-only) executes normally; the policy callback is
    never invoked (read-only is ALLOW upstream, never reaches the gate)."""
    reg = _registry()
    ledger = DispatchLedger()

    def _exploding_cb(call, decision):  # must never be called for read-only
        raise AssertionError("read-only reached the review callback")

    src = tmp_path / "readme.txt"
    src.write_text("hello from a real file\n")
    call = ToolCall(
        name="read_file",
        arguments={"path": str(src)},
        source_of_request=Provenance.USER_DIRECT,
    )
    result = reg.execute(call, review_callback=_exploding_cb)

    assert result.success is True
    assert "hello from a real file" in result.content
    assert len(ledger) == 0  # callback never fired -> nothing recorded


def test_privileged_install_denied_and_recorded(tmp_path):
    """GREEN: a privileged package install is refused + recorded (fail-closed),
    with no pkexec round-trip."""
    reg = _registry()
    ledger = DispatchLedger()
    cb = make_policy_review_callback(ledger)

    call = ToolCall(
        name="manage_packages",
        arguments={"action": "install", "package": "definitely-not-real-pkg"},
        source_of_request=Provenance.USER_DIRECT,
    )
    result = reg.execute(call, review_callback=cb)

    assert result.success is False
    assert getattr(result, "executed", True) is False
    assert len(ledger) == 1
    assert ledger.all()[0].tool == "manage_packages"
    assert ledger.all()[0].tier == "privileged"


def test_callback_fail_closed_on_malformed_decision():
    """A decision object missing needs_pkexec is treated as privileged (deny),
    never allowed."""
    ledger = DispatchLedger()
    cb = make_policy_review_callback(ledger)

    class _Call:
        name = "run_command"
        arguments = {"command": "rm -rf /tmp/x"}

    class _BadDecision:  # no needs_pkexec attribute
        reason = "malformed"

    verdict = cb(_Call(), _BadDecision())
    assert verdict == "deny"
    assert ledger.all()[0].tier == "privileged"  # fail-closed default


def test_ledger_persists_to_disk(tmp_path):
    """The ledger appends JSONL to the run dir for durable per-run-id banking."""
    path = tmp_path / "run1" / "dispatch-ledger.jsonl"
    ledger = DispatchLedger(path=path)
    cb = make_policy_review_callback(ledger)

    class _Call:
        name = "write_file"
        arguments = {"path": "/etc/passwd", "content": "x"}

    class _Decision:
        needs_pkexec = True
        reason = "system path"

    cb(_Call(), _Decision())
    assert path.exists()
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 1
    assert "write_file" in lines[0]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
