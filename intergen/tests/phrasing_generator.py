# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Expand a labelled JSON grammar into deterministic, capped JSONL records.

Usage: python intergen/tests/phrasing_generator.py grammar.json --seed 7 --cap 50
For example, grammar.json can contain this synthetic colour-reading contract::

    {"class": "fixture-colours", "expected_answerer": "fixture-reader",
     "clauses": [{"alternatives": ["show the {colour} tile", "describe {colour}"]},
                 {"alternatives": ["on the table"], "optional": true}],
     "synonyms": {"colour": ["blue", "green"]},
     "forms": {"negation": ["do not {text}"],
               "compound": ["{text}; tell me a story"],
               "follow_up": ["what about that tile?"]},
     "answerers": {"negation": "fixture-refusal", "compound": "fixture-compound"}}

Each clause chooses an alternative; optional clauses may be absent. Synonym
names use plain {name} placeholders. A synonym has one value throughout a base
phrasing. Negation and compound forms contain {text} exactly once. A follow-up
carries the base question in its context field. Answerer labels are the grammar
author's contract, including any per-operator overrides; this tool does not infer
semantics or establish route invariance. Review those labels when writing a
grammar, especially for negation, compounds and dropped clauses.

The seed permutes the finite Cartesian product without materializing it. Each
base emits the selected operators in order, up to the total record cap. At most
10 * cap base combinations are examined, so inapplicable operators cannot cause
an unbounded search. An operator that cannot change the text is omitted. The cap
is an upper bound, not a requested sample size. Alternatives can emit duplicate text;
base_index and source_clauses preserve the provenance of each record. No real
class grammar, model, network connection or non-stdlib dependency is used here.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import string
import sys

OPERATORS = ("base", "typo_transpose", "typo_drop", "ellipsis",
             "negation", "compound", "follow_up")
_FORMATTER = string.Formatter()


def _strings(value, label):
    if (not isinstance(value, list) or not value
            or any(not isinstance(s, str) or not s.strip() for s in value)
            or len(set(value)) != len(value)):
        raise ValueError(f"{label} must be a nonempty list of distinct nonempty strings")
    return value


def _fields(template):
    names = []
    for _, name, spec, conversion in _FORMATTER.parse(template):
        if name is not None:
            if not name.isidentifier() or spec or conversion:
                raise ValueError("only plain {name} placeholders are supported")
            names.append(name)
    return names


def _validate(grammar):
    allowed = {"class", "expected_answerer", "clauses", "synonyms", "forms", "answerers"}
    if not isinstance(grammar, dict) or set(grammar) - allowed:
        raise ValueError("grammar must be an object with only documented fields")
    for key in ("class", "expected_answerer"):
        if not isinstance(grammar.get(key), str) or not grammar[key].strip():
            raise ValueError(f"{key} must be a nonempty string")
    synonyms = grammar.get("synonyms", {})
    if not isinstance(synonyms, dict):
        raise ValueError("synonyms must be an object")
    for name, values in synonyms.items():
        if not isinstance(name, str) or not name.isidentifier():
            raise ValueError("synonym names must be identifiers")
        _strings(values, f"synonyms.{name}")
    clauses = grammar.get("clauses")
    if not isinstance(clauses, list) or not clauses:
        raise ValueError("clauses must be a nonempty list")
    dimensions = []
    for clause in clauses:
        if (not isinstance(clause, dict) or set(clause) - {"alternatives", "optional"}
                or type(clause.get("optional", False)) is not bool):
            raise ValueError("a clause has alternatives and an optional boolean")
        choices = _strings(clause.get("alternatives"), "clause.alternatives")
        for choice in choices:
            if set(_fields(choice)) - synonyms.keys():
                raise ValueError("clause references an unknown synonym")
        dimensions.append([*choices, None] if clause.get("optional") else list(choices))
    if all(clause.get("optional", False) for clause in clauses):
        raise ValueError("at least one clause must be required")
    forms = grammar.get("forms")
    if not isinstance(forms, dict) or set(forms) != {"negation", "compound", "follow_up"}:
        raise ValueError("forms must specify negation, compound and follow_up")
    for operator, values in forms.items():
        for value in _strings(values, f"forms.{operator}"):
            names = _fields(value)
            if set(names) - {"text"} or (operator != "follow_up" and names != ["text"]):
                raise ValueError("negation/compound forms require exactly one {text}")
    answerers = grammar.get("answerers", {})
    if (not isinstance(answerers, dict) or set(answerers) - set(OPERATORS)
            or any(not isinstance(v, str) or not v.strip() for v in answerers.values())):
        raise ValueError("answerers must map operator names to nonempty labels")
    return dimensions, synonyms


def _variant(operator, clauses, forms, rng):
    text = " ".join(clauses)
    if operator == "base":
        return text
    if operator == "typo_transpose":
        candidates = [i for i in range(len(text) - 1)
                      if text[i:i+2].isalpha() and text[i] != text[i+1]]
        if candidates:
            i = rng.choice(candidates)
            return text[:i] + text[i:i+2][::-1] + text[i+2:]
    elif operator == "typo_drop":
        candidates = [i for i, char in enumerate(text) if char.isalpha()]
        if candidates:
            i = rng.choice(candidates)
            return text[:i] + text[i+1:]
    elif operator == "ellipsis":
        if len(clauses) > 1:
            i = rng.randrange(len(clauses))
            return " ".join(clauses[:i] + clauses[i+1:])
    else:
        return rng.choice(forms[operator]).format(text=text)
    return None


def generate(grammar, *, seed: int, cap: int, operators=OPERATORS):
    """Return at most cap records, using only a local seeded random generator."""
    if type(seed) is not int or type(cap) is not int or cap < 0:
        raise ValueError("seed must be an integer and cap a nonnegative integer")
    if (not isinstance(operators, (tuple, list)) or not operators
            or any(op not in OPERATORS for op in operators)
            or len(set(operators)) != len(operators)):
        raise ValueError("operators must be distinct supported operator names")
    dimensions, synonyms = _validate(grammar)
    names = sorted(synonyms)
    dimensions.extend(synonyms[name] for name in names)
    total = math.prod(len(values) for values in dimensions)
    rng = random.Random(seed)
    offset = rng.randrange(total)
    stride = rng.randrange(1, total) if total > 1 else 1
    while math.gcd(stride, total) != 1:
        stride = rng.randrange(1, total)
    rows = []
    if cap == 0:
        return rows
    for index in range(min(total, 10 * cap)):
        rank = (offset + index * stride) % total
        remaining = rank
        chosen = []
        for values in reversed(dimensions):
            remaining, position = divmod(remaining, len(values))
            chosen.append(values[position])
        chosen.reverse()
        count = len(grammar["clauses"])
        substitutions = dict(zip(names, chosen[count:]))
        clauses = [value.format(**substitutions).strip()
                   for value in chosen[:count] if value is not None]
        text = " ".join(clauses)
        for operator in operators:
            variant = _variant(operator, clauses, grammar["forms"], rng)
            if not variant or not variant.strip() or (operator != "base" and variant == text):
                continue
            rows.append({"class": grammar["class"], "operator": operator,
                         "expected_answerer": grammar.get("answerers", {}).get(
                             operator, grammar["expected_answerer"]),
                         "text": variant, "source_text": text,
                         "source_clauses": clauses, "seed": seed, "base_index": rank,
                         "context": [text] if operator == "follow_up" else []})
            if len(rows) == cap:
                return rows
    if not rows:
        raise ValueError("no selected operator applies within the base search limit")
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("grammar")
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--cap", type=int, default=1000)
    parser.add_argument("--operators", nargs="+", choices=OPERATORS, default=OPERATORS)
    args = parser.parse_args(argv)
    try:
        with open(args.grammar, encoding="utf-8") as stream:
            grammar = json.load(stream)
        rows = generate(grammar, seed=args.seed, cap=args.cap, operators=args.operators)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    for row in rows:
        print(json.dumps(row, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
