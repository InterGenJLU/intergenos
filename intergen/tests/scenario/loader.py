# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Fail-closed scenario loader + the build-time zero-effective-assertion validator.

Scenarios are authored as JSON (a file is a single scenario object or a list of
them). The loader validates every field against the schema registries and
REFUSES to load anything malformed — a bad scenario is a loud error at load
time, never a silently-skipped or half-built test. The signature guarantee is
the zero-effective-assertion check: no turn may carry zero real checks once
auto-assertions are folded in, so a vacuous always-pass turn cannot be authored
(the rot class a prior apparatus fell into).

Every error names its locator (file + scenario id + turn index) so a failure is
diagnosable without guessing which entry was wrong.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from intergen.tests.scenario.schema import (
    ASSERTION_TYPES,
    AUTO_ASSERTION_TYPES,
    AXES,
    GATE_OUTCOMES,
    POSTURES,
    SESSION_MARKERS,
    SESSION_POLICIES,
    Assertion,
    Phrasing,
    Scenario,
    Turn,
    effective_assertion_count,
)


class ScenarioValidationError(ValueError):
    """A scenario file or entry failed validation. Carries a locator."""


def _err(locator: str, msg: str) -> ScenarioValidationError:
    return ScenarioValidationError(f"{locator}: {msg}")


def _require(cond: bool, locator: str, msg: str) -> None:
    if not cond:
        raise _err(locator, msg)


def _parse_assertion(raw: Any, locator: str) -> Assertion:
    """Accept either a dict form or a compact [type, value(, description)] list."""
    if isinstance(raw, (list, tuple)):
        _require(1 <= len(raw) <= 3, locator,
                 f"assertion list must be [type], [type,value], or "
                 f"[type,value,description], got {raw!r}")
        a_type = raw[0]
        value = raw[1] if len(raw) >= 2 else ""
        desc = raw[2] if len(raw) >= 3 else ""
        params: dict = {}
        postures: list = []
        gate = ""
    elif isinstance(raw, dict):
        a_type = raw.get("type")
        value = raw.get("value", "")
        params = raw.get("params", {}) or {}
        desc = raw.get("description", "")
        postures = raw.get("postures", []) or []
        gate = raw.get("gate", "") or ""
        _require(isinstance(params, dict), locator, "assertion 'params' must be an object")
        _require(isinstance(postures, list), locator, "assertion 'postures' must be a list")
    else:
        raise _err(locator, f"assertion must be a list or object, got {type(raw).__name__}")

    _require(isinstance(a_type, str) and a_type != "", locator, "assertion 'type' is required")
    _require(a_type in ASSERTION_TYPES, locator,
             f"unknown assertion type {a_type!r} (known: {sorted(ASSERTION_TYPES)})")
    _require(isinstance(value, str), locator, "assertion 'value' must be a string")
    _require(isinstance(desc, str), locator, "assertion 'description' must be a string")
    for p in postures:
        _require(p in POSTURES, locator,
                 f"assertion posture {p!r} unknown (known: {sorted(POSTURES)})")
    if a_type == "gate_outcome":
        _require(value in GATE_OUTCOMES, locator,
                 f"gate_outcome value must be one of {sorted(GATE_OUTCOMES)}, got "
                 f"{value!r} — a typo'd state would silently never match (fail-closed)")
    if a_type == "not_contains_any":
        _require(bool([x for x in value.split(",") if x.strip()]), locator,
                 "not_contains_any needs a non-empty comma-list of alternatives — "
                 "an empty list would vacuously pass (silent no-op, fail-closed)")
    if a_type == "routes_via_any":
        _require(len([x for x in value.split(",") if x.strip()]) >= 2, locator,
                 "routes_via_any needs at least TWO comma-joined sources — a "
                 "single-source disjunction is routes_via, and an empty one "
                 "would never match (fail-closed)")
    _require(isinstance(gate, str) and gate in ("", "A", "B"), locator,
             f"assertion 'gate' override must be 'A', 'B', or absent, got {gate!r}")
    if gate:
        _require(bool(desc.strip()), locator,
                 "an assertion that overrides its gate must carry a description "
                 "saying why — a silent re-scope is indistinguishable from a "
                 "suppression")
    return Assertion(type=a_type, value=value, params=params, description=desc,
                     postures=list(postures), gate=gate)


def _parse_turn(raw: Any, locator: str) -> Turn:
    _require(isinstance(raw, dict), locator, "turn must be an object")
    user = raw.get("user")
    _require(isinstance(user, str) and user != "", locator, "turn 'user' is required")

    marker = raw.get("session_marker")
    _require(marker is None or marker in SESSION_MARKERS, locator,
             f"session_marker must be null or one of {sorted(SESSION_MARKERS)}, got {marker!r}")

    skip_auto = raw.get("skip_auto", []) or []
    _require(isinstance(skip_auto, list), locator, "skip_auto must be a list")
    for s in skip_auto:
        _require(s in AUTO_ASSERTION_TYPES, locator,
                 f"skip_auto names unknown auto-assertion {s!r} "
                 f"(known: {sorted(AUTO_ASSERTION_TYPES)})")

    raw_assertions = raw.get("assertions", []) or []
    _require(isinstance(raw_assertions, list), locator, "assertions must be a list")
    assertions = [
        _parse_assertion(a, f"{locator} assertion[{i}]")
        for i, a in enumerate(raw_assertions)
    ]

    phrasings = _parse_phrasings(raw.get("phrasings", []) or [], locator)

    return Turn(
        user=user,
        assertions=assertions,
        speaker=raw.get("speaker", "user"),
        description=raw.get("description", ""),
        session_marker=marker,
        skip_auto=list(skip_auto),
        phrasings=phrasings,
    )


def _parse_phrasings(raw: Any, locator: str) -> list[Phrasing]:
    """Parse a turn's alternate wordings (WP-2.3 phrasing family).

    Each phrasing is either a bare string (the wording, label auto-assigned) or
    an object ``{"text": ..., "label": ...}``. Text is required and non-empty; a
    label is recommended for an auditable variant id but optional. Labels must be
    unique within the turn so expanded variant ids do not collide.
    """
    _require(isinstance(raw, list), locator, "'phrasings' must be a list")
    out: list[Phrasing] = []
    seen_labels: set[str] = set()
    for i, p in enumerate(raw):
        if isinstance(p, str):
            text, label = p, ""
        elif isinstance(p, dict):
            text = p.get("text", "")
            label = p.get("label", "")
            _require(isinstance(label, str), locator, "phrasing 'label' must be a string")
        else:
            raise _err(locator, f"phrasing[{i}] must be a string or object")
        _require(isinstance(text, str) and text != "", locator,
                 f"phrasing[{i}] 'text' is required and must be non-empty")
        label = label or f"v{i + 1}"
        _require(label not in seen_labels, locator,
                 f"duplicate phrasing label {label!r} within a turn")
        seen_labels.add(label)
        out.append(Phrasing(text=text, label=label))
    return out


def parse_scenario(raw: Any, *, source: str = "<memory>") -> Scenario:
    """Validate one scenario dict and return a Scenario, or raise loudly.

    This is the single validation chokepoint — load_scenarios() and any
    in-memory construction both go through here, so the zero-effective-assertion
    guarantee holds no matter how a scenario reaches the harness.
    """
    _require(isinstance(raw, dict), source, "scenario must be an object")
    sid = raw.get("id")
    _require(isinstance(sid, str) and sid != "", source, "scenario 'id' is required")
    locator = f"{source} [{sid}]"

    name = raw.get("name", "")
    _require(isinstance(name, str), locator, "'name' must be a string")

    axis = raw.get("axis")
    _require(isinstance(axis, list) and len(axis) >= 1, locator,
             "'axis' is required and must be a non-empty list")
    for a in axis:
        _require(a in AXES, locator, f"unknown axis {a!r} (known: {sorted(AXES)})")

    postures = raw.get("postures", ["2B-locked"]) or ["2B-locked"]
    _require(isinstance(postures, list) and len(postures) >= 1, locator,
             "'postures' must be a non-empty list")
    for p in postures:
        _require(p in POSTURES, locator, f"unknown posture {p!r} (known: {sorted(POSTURES)})")

    session_policy = raw.get("session_policy", "single-session")
    _require(session_policy in SESSION_POLICIES, locator,
             f"session_policy must be one of {sorted(SESSION_POLICIES)}, got {session_policy!r}")

    for key in ("capabilities", "tags", "cleanup_for"):
        val = raw.get(key, []) or []
        _require(isinstance(val, list), locator, f"'{key}' must be a list")

    cleanup = raw.get("cleanup", True)
    _require(isinstance(cleanup, bool), locator, "'cleanup' must be a boolean")

    raw_turns = raw.get("turns")
    _require(isinstance(raw_turns, list) and len(raw_turns) >= 1, locator,
             "'turns' is required and must be a non-empty list")

    category = raw.get("category", "")
    turns = [_parse_turn(t, f"{locator} turn[{i}]") for i, t in enumerate(raw_turns)]

    # The signature guarantee: no turn may be vacuous. A turn that authored no
    # explicit assertions AND suppressed every applicable auto-assertion would
    # always pass regardless of the response — a silent hole. Reject it here.
    for i, turn in enumerate(turns):
        eff = effective_assertion_count(turn, category)
        _require(eff > 0, f"{locator} turn[{i}]",
                 "turn has zero effective assertions (no explicit assertions and "
                 "every applicable auto-assertion suppressed via skip_auto) — a "
                 "vacuous always-pass turn is not allowed")

    return Scenario(
        id=sid,
        name=name,
        axis=list(axis),
        turns=turns,
        category=category,
        postures=list(postures),
        capabilities=list(raw.get("capabilities", []) or []),
        tags=list(raw.get("tags", []) or []),
        session_policy=session_policy,
        cleanup=cleanup,
        cleanup_for=list(raw.get("cleanup_for", []) or []),
    )


def load_scenarios(path: str | Path) -> list[Scenario]:
    """Load and validate every scenario under `path` (a JSON file or a directory).

    A directory is walked for *.json files (sorted, for determinism). Each file
    is a single scenario object or a list of them. Fails loud on: unreadable /
    non-JSON files, any schema violation, a duplicate scenario id across the
    whole load, or a cleanup_for that names a scenario id not present in the
    load. Returns the scenarios in a stable order.
    """
    p = Path(path)
    files: list[Path]
    if p.is_dir():
        files = sorted(p.rglob("*.json"))
    elif p.is_file():
        files = [p]
    else:
        raise ScenarioValidationError(f"{p}: no such file or directory")

    scenarios: list[Scenario] = []
    seen_ids: dict[str, str] = {}  # id -> source file, for duplicate detection

    for f in files:
        try:
            raw_text = f.read_text(encoding="utf-8")
        except OSError as e:
            raise ScenarioValidationError(f"{f}: cannot read: {e}") from e
        try:
            doc = json.loads(raw_text)
        except json.JSONDecodeError as e:
            raise ScenarioValidationError(f"{f}: invalid JSON: {e}") from e

        entries = doc if isinstance(doc, list) else [doc]
        for entry in entries:
            scenario = parse_scenario(entry, source=str(f))
            if scenario.id in seen_ids:
                raise ScenarioValidationError(
                    f"{f} [{scenario.id}]: duplicate scenario id — already "
                    f"defined in {seen_ids[scenario.id]}")
            seen_ids[scenario.id] = str(f)
            scenarios.append(scenario)

    # cleanup_for must reference ids that exist in the load set — a dangling
    # linked-pair producer means the consumer's cross-session shape is broken.
    for scenario in scenarios:
        for producer in scenario.cleanup_for:
            if producer not in seen_ids:
                raise ScenarioValidationError(
                    f"[{scenario.id}]: cleanup_for names {producer!r}, which is "
                    "not a scenario id in this load set (dangling linked pair)")

    return scenarios
