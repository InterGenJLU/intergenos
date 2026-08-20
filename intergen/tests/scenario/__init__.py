# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Scenario test harness for InterGen.

A harness that iterates scenario CLASSES against the full assistant stack and
regression-protects each, replacing per-turn dispatch point-fixes. Six grading
axes: context persistence over turns, memory persistence between sessions,
decomposer correctness, routing/decisioning, capability recall, and fabrication
guards. The authoritative design is recorded separately (the scenario-harness
design artifact); this package implements it phase by phase.

Phase 1 (this package so far):
  * schema     — Scenario / Turn / Assertion data model + the axis/posture/
                 assertion-type registries.
  * loader     — fail-closed JSON scenario loader with the build-time
                 zero-effective-assertion validator (no turn can be vacuous).
  * transport  — the direct + D-Bus daemon transports behind one interface,
                 plus a mock transport for harness self-tests.

Subsequent phases add the structural grader + trace join, the capability
inventory, the seed scenarios, the run-over-run comparator, and the discovery
sweep.
"""

from __future__ import annotations

from intergen.tests.scenario.schema import (
    ASSERTION_TYPES,
    AUTO_ASSERTION_TYPES,
    AXES,
    POSTURES,
    SESSION_MARKERS,
    SESSION_POLICIES,
    Assertion,
    Phrasing,
    Scenario,
    Turn,
    applicable_auto_assertions,
    effective_assertion_count,
)
from intergen.tests.scenario.family import (
    expand_families,
    expand_scenario,
)
from intergen.tests.scenario.loader import (
    ScenarioValidationError,
    load_scenarios,
    parse_scenario,
)
from intergen.tests.scenario.transport import (
    ClientTransport,
    MockTransport,
    ScenarioTransport,
    TurnResult,
)
from intergen.tests.scenario.runner import (
    MemoryWriteGap,
    ScenarioRun,
    run_scenario,
    run_scenarios,
)
from intergen.tests.scenario.responsiveness import (
    answer_topic,
    question_licenses,
    responsiveness_finding,
)
from intergen.tests.scenario.report import (
    build_results,
    write_run,
)
from intergen.tests.scenario.comparator import (
    compare,
)
from intergen.tests.scenario.judge import (
    annotate_run,
    annotate_turn,
    annotations_to_dict,
    calibration_catches,
)
from intergen.tests.scenario.promote import (
    Anomaly,
    mine_anomalies,
    promote,
    promote_run,
)

__all__ = [
    "ASSERTION_TYPES",
    "AUTO_ASSERTION_TYPES",
    "AXES",
    "POSTURES",
    "SESSION_MARKERS",
    "SESSION_POLICIES",
    "Assertion",
    "Phrasing",
    "Scenario",
    "Turn",
    "applicable_auto_assertions",
    "effective_assertion_count",
    "expand_families",
    "expand_scenario",
    "ScenarioValidationError",
    "load_scenarios",
    "parse_scenario",
    "ClientTransport",
    "MockTransport",
    "ScenarioTransport",
    "TurnResult",
    "MemoryWriteGap",
    "ScenarioRun",
    "run_scenario",
    "run_scenarios",
    "answer_topic",
    "question_licenses",
    "responsiveness_finding",
    "build_results",
    "write_run",
    "compare",
    "annotate_run",
    "annotate_turn",
    "annotations_to_dict",
    "calibration_catches",
    "Anomaly",
    "mine_anomalies",
    "promote",
    "promote_run",
]
