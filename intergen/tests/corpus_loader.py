# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Demand-corpus loader (M8-6) — JSONL bank -> Conversation objects.

The demand corpus (`intergen/tests/demand_corpus/`) is a JSONL bank of user asks that
flex every aspect of InterGen. This loader is the translation-free bridge: it reads a
bank file and returns `conversations.Conversation` dataclasses — the exact objects the
test runner already drives — so `runner.py --corpus <bank>` consumes the bank with no
adapter.

Schema contract (authoritative): `intergen/tests/demand_corpus/README.md`.

Fail-closed by design: an invalid line raises `CorpusError` with a precise `file:line`
locator rather than silently dropping an entry (a dropped entry would read as a coverage
gap the mass run never notices — the silent-loss class the build doctrine forbids).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from intergen.tests.conversations import Assertion, Conversation, Turn

# The behavior CLASSES an entry may declare (analysis metadata, not graded on the
# discovery run). None/absent means "not derivable" and is valid.
VALID_BEHAVIOR_CLASSES = frozenset(
    {"route-shape", "should-dispatch", "should-gate", "should-teach"}
)


class CorpusError(ValueError):
    """A demand-corpus entry violated the schema. Message carries file:line."""


def _require(cond: bool, locator: str, msg: str) -> None:
    if not cond:
        raise CorpusError(f"{locator}: {msg}")


def validate_entry(
    obj: Any, *, locator: str = "<entry>", known_grounding_keys: set[str] | None = None
) -> None:
    """Raise CorpusError if `obj` is not a well-formed demand-corpus entry.

    `known_grounding_keys`, when given, additionally enforces that every
    `provenance.grounding` key resolves to a registered source
    (grounding_sources.md) — an unregistered key is an unprovenanced entry.
    """
    _require(isinstance(obj, dict), locator, "entry must be a JSON object")

    _require(
        isinstance(obj.get("id"), str) and obj["id"].strip() != "",
        locator, "'id' must be a non-empty string",
    )
    _require(
        isinstance(obj.get("category"), str) and obj["category"].strip() != "",
        locator, "'category' must be a non-empty string",
    )
    _require(
        isinstance(obj.get("intent"), str) and obj["intent"].strip() != "",
        locator, "'intent' must be a non-empty string",
    )

    turns = obj.get("turns")
    _require(isinstance(turns, list) and len(turns) >= 1, locator,
             "'turns' must be a non-empty list")
    for ti, turn in enumerate(turns):
        tloc = f"{locator} turn[{ti}]"
        _require(isinstance(turn, dict), tloc, "turn must be a JSON object")
        # 'user' may be the empty string (the empty-input edge cell) but must be present
        # and a string.
        _require("user" in turn and isinstance(turn["user"], str), tloc,
                 "turn must carry a string 'user' field")
        asserts = turn.get("assertions", [])
        _require(isinstance(asserts, list), tloc, "'assertions' must be a list if present")
        for ai, a in enumerate(asserts):
            aloc = f"{tloc} assertion[{ai}]"
            _require(isinstance(a, dict), aloc, "assertion must be a JSON object")
            _require(isinstance(a.get("type"), str) and a["type"] != "", aloc,
                     "assertion needs a non-empty 'type'")
            _require(isinstance(a.get("value", ""), str), aloc,
                     "assertion 'value' must be a string")

    ebc = obj.get("expected_behavior_class")
    _require(
        ebc is None or ebc == "" or ebc in VALID_BEHAVIOR_CLASSES,
        locator,
        f"'expected_behavior_class' must be null or one of {sorted(VALID_BEHAVIOR_CLASSES)}",
    )

    prov = obj.get("provenance")
    _require(isinstance(prov, dict), locator, "'provenance' must be a JSON object")
    _require(isinstance(prov.get("generator"), str) and prov["generator"] != "",
             locator, "provenance.generator must be a non-empty string")
    _require(isinstance(prov.get("lens"), str) and prov["lens"] != "",
             locator, "provenance.lens must be a non-empty string")
    grounding = prov.get("grounding")
    _require(isinstance(grounding, list) and len(grounding) >= 1
             and all(isinstance(g, str) and g for g in grounding),
             locator, "provenance.grounding must be a non-empty list of strings")
    _require(isinstance(prov.get("method"), str) and prov["method"] != "",
             locator, "provenance.method must be a non-empty string")

    if known_grounding_keys is not None:
        for g in grounding:
            _require(g in known_grounding_keys, locator,
                     f"grounding key {g!r} is not registered in grounding_sources.md")


def entry_to_conversation(obj: dict) -> Conversation:
    """Map a validated entry dict to the Conversation dataclass the runner drives."""
    turns: list[Turn] = []
    for turn in obj["turns"]:
        assertions = [
            Assertion(
                type=a["type"],
                value=a.get("value", ""),
                description=a.get("description", ""),
            )
            for a in turn.get("assertions", [])
        ]
        turns.append(Turn(user=turn["user"], assertions=assertions))

    # Multi-turn flows must keep memory/session state across their own turns.
    persist = len(turns) > 1
    ebc = obj.get("expected_behavior_class") or ""

    return Conversation(
        id=obj["id"],
        name=obj.get("intent", obj["id"]),
        category=obj["category"],
        turns=turns,
        persist_state=persist,
        expected_behavior_class=ebc,
    )


def iter_corpus_records(path: str | Path) -> list[dict]:
    """Parse a JSONL bank into a list of raw dicts (no schema validation).

    Blank lines are skipped; a JSON parse error raises CorpusError with file:line.
    """
    p = Path(path)
    records: list[dict] = []
    with p.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            if raw.strip() == "":
                continue
            try:
                records.append(json.loads(raw))
            except json.JSONDecodeError as exc:
                raise CorpusError(f"{p}:{lineno}: invalid JSON — {exc}") from exc
    return records


def load_corpus(
    path: str | Path, *, known_grounding_keys: set[str] | None = None
) -> list[Conversation]:
    """Load + validate a JSONL bank and return runner-ready Conversation objects.

    Every line is schema-validated (fail-closed); the first offending line aborts
    the load with a precise locator so a malformed bank never grades as a partial run.
    """
    p = Path(path)
    records = iter_corpus_records(p)
    conversations: list[Conversation] = []
    for lineno, obj in enumerate(records, start=1):
        validate_entry(obj, locator=f"{p}:entry#{lineno}",
                       known_grounding_keys=known_grounding_keys)
        conversations.append(entry_to_conversation(obj))
    return conversations
