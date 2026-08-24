# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""WP-1.4 — the scenario harness's capability inventory + coverage report.

The inventory is the harness's SOURCE OF TRUTH, authored before scenarios and
graded against — coverage is designed against the inventory, not discovered by
whatever scenarios happen to exist. A capability with no asserting scenario is a
**coverage gap = a finding**, not a blank.

Five orthogonal registries enumerate the capability surface (§1):
  A — Tools (the dispatch surface): the 9 shipped tools + their class /
      reads-reality / fabrication-relevance. GROWN from
      :mod:`intergen.tests.capability_inventory` (the tool sets + gate outcomes
      + their drift guard are reused, never re-derived).
  B — Routing decisions (the decision surface): the route-source vocabulary the
      router actually emits, enumerated from router.py and DRIFT-GUARDED — a new
      route source the router grows but the inventory does not know is a loud
      load error, so coverage can never be computed against a stale route map.
  C — Gate outcomes (the consequence surface): the 8 outcomes (reused from A's
      module).
  D — Cross-cutting behaviors: the non-tool invariants that apply across turns.
  E — Tier / posture matrix: {2B-locked, 9B-native, 35B-native}; the locked-down
      2B is the coverage floor. The rows are derived from the schema's POSTURES,
      so a new tier appears here as an uncovered cell until scenarios declare it
      — an unasserted tier is a visible gap, never a silent pass.
  F — Safety / consent surface: command safety tiers, scanner dispositions,
      cloud-escalation modes.

The coverage report diffs ROWS ENUMERATED against ROWS WITH >=1 ASSERTING
SCENARIO TURN. A capability the harness cannot observe is written down as an
OBSERVABILITY gap (a telemetry finding), never silently dropped — the honest
denominator is the whole point.

This module imports no daemon/model code: the registries are derived from the
filesystem tool set, a static scan of router.py's route-source literals, and the
scenario-schema constants, so the inventory + its drift guards run headless in CI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from intergen.tests.capability_inventory import (
    ALL_TOOLS,
    GATE_OUTCOMES,
    GATED_TOOLS,
    READ_OUTCOMES,
    READ_TOOLS,
    capability_class,
)
from intergen.tests.scenario.schema import (
    AUTO_ASSERTION_TYPES,
    AXES,
    POSTURES,
    Scenario,
)
from intergen.tests.scenario.trace import READS_REALITY_TOOLS

_ROUTER_PY = Path(__file__).resolve().parent.parent.parent / "router.py"

# Route-source literals that appear in router.py but are not real route verdicts
# a scenario would assert against — the module-level default tag and the
# empty-input sentinel. Excluded from Registry B so the coverage denominator is
# the set of ROUTABLE decisions, not internal sentinels. Kept as a NAMED, small
# allowlist (never a silent filter): a new sentinel must be added here on
# purpose, which is itself a review point.
_ROUTE_SOURCE_NOISE: frozenset[str] = frozenset({"router", "empty_input"})


def route_sources_in_tree() -> frozenset[str]:
    """The route-source tags the router actually emits, scanned from router.py.

    The analog of the tool drift guard's filesystem scan: the route vocabulary is
    read from the one place it is authored (``source="..."`` / ``source='...'``
    literals on RouteResult construction), minus the named sentinels. This is what
    Registry B is drift-guarded against, so a route source the router grows cannot
    silently escape the coverage denominator.
    """
    try:
        text = _ROUTER_PY.read_text(encoding="utf-8")
    except OSError as e:  # pragma: no cover - router.py is always present in-tree
        raise RuntimeError(f"cannot read router.py for the route-source drift "
                           f"guard: {e}") from e
    found = set(re.findall(r"""source=["']([a-z_]+)["']""", text))
    return frozenset(found - _ROUTE_SOURCE_NOISE)


# ── Registry B — the inventoried route sources (drift-guarded against the tree) ──
# Enumerated dynamically from router.py at import, so it cannot rot: whatever the
# router emits (minus the named sentinels) IS the inventory. A hardcoded list
# would be exactly the drift this guard exists to catch.
ROUTE_SOURCES: frozenset[str] = route_sources_in_tree()

# ── Registry D — cross-cutting behaviors (the non-tool, across-turn invariants) ──
# The machine-checkable ones map to the grader's auto-assertions + the authored
# grounding/consistency assertions; the rest are declared invariants a scenario
# covers by asserting them. Kept as inventory rows so a behavior with no asserting
# scenario reads as a gap.
CROSS_CUTTING_BEHAVIORS: frozenset[str] = frozenset({
    "honesty_no_fabricated_success",
    "no_capability_denial",
    "confirmation_posture",
    "source_citation",
    "no_wrong_package_manager",
    "no_hallucinated_device_path",
    "no_fabricated_state",
    "no_invented_artifact",
    "self_consistent",
    "verbosity_ceiling",
})

# ── Registry F — safety / consent surface (a real gated surface, not an axis) ──
COMMAND_SAFETY_TIERS: frozenset[str] = frozenset({"AUTO", "CONFIRM", "BLOCKED"})
SCANNER_DISPOSITIONS: frozenset[str] = frozenset({"ALLOW", "FLAG", "BLOCK"})
SCANNER_DIRECTIONS: frozenset[str] = frozenset({"INGRESS", "EGRESS"})
CLOUD_ESCALATION_MODES: frozenset[str] = frozenset({"NEVER", "FALLBACK", "ASK", "AUTO"})

# The coverage FLOOR posture: the locked-down 2B. A 9B-only capability is not
# counted against 2B coverage; a capability with no posture is assumed floor.
FLOOR_POSTURE = "2B-locked"


@dataclass(frozen=True)
class InventoryRow:
    """One enumerated capability cell across the registries.

    ``registry`` is A..F; ``row`` is the stable id (a tool name, a route source,
    a (tool, outcome) pair rendered as "tool:outcome", a behavior, a posture, a
    safety cell). ``testable`` mirrors the schema's testability flag; a row the
    harness cannot observe is annotated, never dropped.
    """
    registry: str
    row: str
    testable: str = "yes"          # yes | requires-setup | not-corpus-viable-on-2B | unobservable
    note: str = ""


def _tool_rows() -> list[InventoryRow]:
    rows: list[InventoryRow] = []
    for tool in sorted(ALL_TOOLS):
        cls = capability_class(tool)
        reads = tool in READS_REALITY_TOOLS
        note = f"class={cls}; reads_live_state={reads}; " \
               f"fabrication_relevant={reads}"
        rows.append(InventoryRow("A", tool, "yes", note))
    return rows


# Route sources a scenario turn cannot produce, with the reason. A scenario
# drives UTTERANCES; a verdict that depends on how the daemon is wired rather
# than on what was typed is annotated here rather than counted as an
# un-annotated gap or quietly dropped from the denominator.
_ROUTE_SOURCE_TESTABILITY: dict[str, tuple[str, str]] = {
    "conversation_unbound": (
        "requires-setup",
        "produced only when a router serving several frontends is asked to "
        "route a turn that did not name its conversation — a wiring fault, not "
        "an utterance. Asserted in the unit suite "
        "(intergen/tests/test_conversation_isolation.py)."),
}


def _route_rows() -> list[InventoryRow]:
    rows: list[InventoryRow] = []
    for s in sorted(ROUTE_SOURCES):
        testable, note = _ROUTE_SOURCE_TESTABILITY.get(s, ("yes", ""))
        rows.append(InventoryRow("B", s, testable, note))
    return rows


def _gate_outcome_rows() -> list[InventoryRow]:
    # (tool, outcome) cells: gated tools reach the full gate-outcome set; read
    # tools reach only the executed_* pair. Rendered "tool:outcome".
    rows: list[InventoryRow] = []
    for tool in sorted(GATED_TOOLS):
        for oc in GATE_OUTCOMES:
            rows.append(InventoryRow("C", f"{tool}:{oc}"))
    for tool in sorted(READ_TOOLS):
        for oc in READ_OUTCOMES:
            rows.append(InventoryRow("C", f"{tool}:{oc}"))
    return rows


def _cross_cutting_rows() -> list[InventoryRow]:
    return [InventoryRow("D", b) for b in sorted(CROSS_CUTTING_BEHAVIORS)]


def _posture_rows() -> list[InventoryRow]:
    return [InventoryRow("E", p) for p in sorted(POSTURES)]


def _safety_rows() -> list[InventoryRow]:
    rows: list[InventoryRow] = []
    for t in sorted(COMMAND_SAFETY_TIERS):
        rows.append(InventoryRow("F", f"command_safety:{t}"))
    for disp in sorted(SCANNER_DISPOSITIONS):
        for direction in sorted(SCANNER_DIRECTIONS):
            # A stock box has no cloud provider, so the deep/cloud scanner tier and
            # cloud escalation are requires-config, not drivable on a bare install.
            rows.append(InventoryRow("F", f"scanner:{direction}:{disp}"))
    for mode in sorted(CLOUD_ESCALATION_MODES):
        rows.append(InventoryRow("F", f"cloud_escalation:{mode}", "requires-setup",
                                 "no cloud provider on a stock box"))
    return rows


def enumerate_inventory() -> list[InventoryRow]:
    """Every enumerated capability row across all five registries (the denominator)."""
    return (_tool_rows() + _route_rows() + _gate_outcome_rows()
            + _cross_cutting_rows() + _posture_rows() + _safety_rows())


# ── coverage: what the scenario corpus actually asserts against ──

# Which authored assertion type covers which registry, and how its value/params
# map to an inventory row id.
def _covered_rows_for_scenario(scenario: Scenario) -> set[tuple[str, str]]:
    """The (registry, row) cells this scenario's turns assert against.

    A capability is covered when a turn ASSERTS it (the authoritative signal),
    with the scenario's declared ``capabilities`` and ``postures`` folded in.
    """
    covered: set[tuple[str, str]] = set()

    # Registry E — declared postures.
    for p in scenario.postures:
        if p in POSTURES:
            covered.add(("E", p))

    # Registry A — declared capabilities that name a tool (tool:... or bare tool).
    for cap in scenario.capabilities:
        tool = cap.split(":", 1)[1] if cap.startswith("tool:") else cap
        if tool in ALL_TOOLS:
            covered.add(("A", tool))

    for turn in scenario.turns:
        for a in turn.assertions:
            t, v, params = a.type, a.value, a.params
            if t in ("uses_tool", "tool_result_nonempty") and v in ALL_TOOLS:
                covered.add(("A", v))
            elif t == "uses_any_tool":
                for name in (x.strip() for x in v.split(",")):
                    if name in ALL_TOOLS:
                        covered.add(("A", name))
            elif t in ("tool_arg_contains", "tool_output_contains", "dispatch_outcome"):
                tool = params.get("tool", "")
                if tool in ALL_TOOLS:
                    covered.add(("A", tool))
                if t == "dispatch_outcome" and tool in ALL_TOOLS and v in GATE_OUTCOMES:
                    covered.add(("C", f"{tool}:{v}"))
            elif t == "answer_consistent_with_tool":
                tool = params.get("tool", "") or v
                if tool in ALL_TOOLS:
                    covered.add(("A", tool))
            elif t == "routes_via" and v in ROUTE_SOURCES:
                covered.add(("B", v))
            elif t == "routes_via_any":
                # A disjunction covers every route it names: the turn genuinely
                # exercises whichever handler the architecture picks, and the
                # assertion fails hard on anything outside the set. Dropping it
                # from coverage would under-report routes that ARE asserted.
                for alt in (x.strip() for x in v.split(",")):
                    if alt in ROUTE_SOURCES:
                        covered.add(("B", alt))
            elif t == "decomposes_into":
                # The decomposer-tree assertion exercises the compound-decompose
                # route (Registry B) whether or not routes_via is also asserted.
                if "decomposed" in ROUTE_SOURCES:
                    covered.add(("B", "decomposed"))
            # Registry D — the cross-cutting invariants, whether authored or auto.
            elif t == "no_fabricated_state":
                covered.add(("D", "no_fabricated_state"))
            elif t == "no_invented_artifact":
                covered.add(("D", "no_invented_artifact"))
            elif t == "self_consistent":
                covered.add(("D", "self_consistent"))
            elif t in ("source", "source_any"):
                covered.add(("D", "source_citation"))
            elif t == "no_fabricated_success":
                covered.add(("D", "honesty_no_fabricated_success"))

        # Auto-assertions cover their cross-cutting rows on every non-suppressing
        # turn (the grader appends them; here we credit the coverage the same way).
        autos = AUTO_ASSERTION_TYPES - set(turn.skip_auto)
        if "no_capability_denial" in autos:
            covered.add(("D", "no_capability_denial"))
        if "no_wrong_package_manager" in autos:
            covered.add(("D", "no_wrong_package_manager"))
        if "no_hallucinated_device_path" in autos:
            covered.add(("D", "no_hallucinated_device_path"))
    return covered


def scenario_coverage(scenarios: list[Scenario]) -> set[tuple[str, str]]:
    """The union of (registry, row) cells the whole scenario corpus asserts."""
    covered: set[tuple[str, str]] = set()
    for s in scenarios:
        covered |= _covered_rows_for_scenario(s)
    return covered


@dataclass
class CoverageReport:
    """The coverage matrix: enumerated rows vs asserted rows, gaps annotated."""
    total: int
    covered: int
    rows: list[InventoryRow] = field(default_factory=list)
    covered_cells: set[tuple[str, str]] = field(default_factory=set)

    @property
    def gaps(self) -> list[InventoryRow]:
        return [r for r in self.rows if (r.registry, r.row) not in self.covered_cells]

    def by_registry(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for r in self.rows:
            d = out.setdefault(r.registry, {"total": 0, "covered": 0})
            d["total"] += 1
            if (r.registry, r.row) in self.covered_cells:
                d["covered"] += 1
        return out

    def render(self) -> str:
        lines = [f"scenario coverage: {self.covered}/{self.total} rows asserted"]
        for reg, d in sorted(self.by_registry().items()):
            lines.append(f"  registry {reg}: {d['covered']}/{d['total']}")
        gaps = self.gaps
        if gaps:
            lines.append(f"  GAPS ({len(gaps)}):")
            for g in gaps:
                suffix = f"  [{g.testable}: {g.note}]" if g.testable != "yes" else ""
                lines.append(f"    {g.registry} {g.row}{suffix}")
        return "\n".join(lines)


def coverage_report(scenarios: list[Scenario]) -> CoverageReport:
    """Diff the enumerated inventory against what the scenario corpus asserts."""
    rows = enumerate_inventory()
    covered = scenario_coverage(scenarios)
    covered_in_inventory = {(r.registry, r.row) for r in rows} & covered
    return CoverageReport(total=len(rows), covered=len(covered_in_inventory),
                          rows=rows, covered_cells=covered)


# ── drift guards (fail loud at import; extend the tool guard to route sources) ──

def _assert_route_sources_current() -> None:
    """Registry B drift guard: the inventory's route-source set must equal the
    router's emitted set. ROUTE_SOURCES is derived from the tree at import, so
    this normally holds by construction; the assertion is the tripwire for a
    future refactor that decouples them (e.g. someone hardcodes ROUTE_SOURCES)."""
    tree = route_sources_in_tree()
    if ROUTE_SOURCES != tree:
        raise RuntimeError(
            "scenario inventory Registry B is out of sync with router.py: "
            f"inventory-only={sorted(ROUTE_SOURCES - tree)} "
            f"tree-only={sorted(tree - ROUTE_SOURCES)} — the route-source "
            "vocabulary drifted.")


def _assert_posture_matrix_current() -> None:
    """Registry E drift guard: the tier/posture matrix must match the schema's
    fixed POSTURES and include the declared coverage floor."""
    if FLOOR_POSTURE not in POSTURES:
        raise RuntimeError(
            f"scenario inventory floor posture {FLOOR_POSTURE!r} is not in the "
            f"schema POSTURES {sorted(POSTURES)} — the tier matrix drifted.")
    if not AXES:  # the six axes must exist for the coverage map to mean anything
        raise RuntimeError("scenario schema AXES is empty — coverage map undefined.")


_assert_route_sources_current()
_assert_posture_matrix_current()
