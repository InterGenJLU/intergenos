#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Widen the posture declaration of every tier-independent scenario.

A scenario whose every ungated assertion reads a recorded decision, or a
property every tier owes, applies to all three tiers — but most of the corpus
declares ``["2B-locked"]`` alone, so 9B and 35B runs skip it. This applies the
rule in :mod:`intergen.tests.scenario.tier_independence` across the corpus, so
the change is mechanical and reviewable rather than 170-odd hand edits nobody
can check.

It rewrites ONLY the ``postures`` declaration of the scenarios the rule selects,
in place, in whichever of the corpus's two styles that scenario already uses —
the compact ``"postures": ["2B-locked"],`` and the expanded block are both
preserved, along with indentation and any trailing comma. Re-serialising the
JSON instead would have reflowed every compact array in the file and buried 172
real changes under a couple of thousand cosmetic ones.

It only ever WIDENS. No assertion is added, removed or re-gated, no scenario the
rule does not select is touched, and a declaration already naming all three
tiers is left exactly as it is.

    python3 scripts/declare-tier-independent-scenarios.py --check
    python3 scripts/declare-tier-independent-scenarios.py --apply
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from intergen.tests.scenario import tier_independence as ti  # noqa: E402

CORPUS_DIR = os.path.join(os.path.dirname(__file__), "..",
                          "intergen", "tests", "scenario", "corpus")

_ID_RE = re.compile(r'^(\s*)"id"\s*:\s*"([^"]+)"')
_POSTURES_COMPACT_RE = re.compile(r'^(\s*)"postures"\s*:\s*\[[^\]]*\](,?)\s*$')
_POSTURES_OPEN_RE = re.compile(r'^(\s*)"postures"\s*:\s*\[\s*$')
# Some scenarios carry no postures key at all. They get one inserted, in the
# style the rest of that scenario already uses.
_COMPACT_ARRAY_RE = re.compile(r'^(\s*)"(?:axis|tags|capabilities)"\s*:\s*\[[^\]]*\],?\s*$')
_TURNS_RE = re.compile(r'^(\s*)"turns"\s*:')


def _targets() -> tuple[dict[str, set[str]], int, int, dict[str, str]]:
    """Per file, the scenario ids whose declaration must be widened."""
    per_file: dict[str, set[str]] = {}
    total = independent = 0
    unknown: dict[str, str] = {}
    for path in sorted(glob.glob(os.path.join(CORPUS_DIR, "*.json"))):
        data = json.load(open(path, encoding="utf-8"))
        scns = data if isinstance(data, list) else data.get("scenarios", [])
        ids: set[str] = set()
        for s in scns:
            if not isinstance(s, dict) or "id" not in s:
                continue
            total += 1
            for ty in ti.unknown_assertion_types(s):
                unknown.setdefault(ty, f"{os.path.basename(path)}:{s['id']}")
            if not ti.scenario_is_tier_independent(s):
                continue
            independent += 1
            if set(s.get("postures") or []) != set(ti.ALL_POSTURES):
                ids.add(s["id"])
        per_file[path] = ids
    return per_file, total, independent, unknown



def _has_postures(lines: list[str], id_line: int) -> bool:
    """Does the scenario starting at this id line already declare postures?

    Scans forward only to that scenario's "turns" key, so a postures key
    belonging to a LATER scenario can never be mistaken for this one's.
    """
    for j in range(id_line + 1, len(lines)):
        if _TURNS_RE.match(lines[j]):
            return False
        if _POSTURES_COMPACT_RE.match(lines[j]) or _POSTURES_OPEN_RE.match(lines[j]):
            return True
    return False


def _scenario_is_compact(lines: list[str], turns_line: int) -> bool:
    """True when this scenario writes its short arrays on one line, so an
    inserted declaration matches the file it lands in."""
    for j in range(max(0, turns_line - 12), turns_line):
        if _COMPACT_ARRAY_RE.match(lines[j]):
            return True
    return False


def _rewrite(path: str, ids: set[str]) -> tuple[str, int]:
    """Return the file's new text and how many declarations were rewritten."""
    lines = open(path, encoding="utf-8").read().split("\n")
    out: list[str] = []
    current: str | None = None
    id_indent = "  "
    needs_insert = False
    changed = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        m_id = _ID_RE.match(line)
        if m_id:
            current = m_id.group(2)
            id_indent = m_id.group(1)
            needs_insert = current in ids and not _has_postures(lines, i)

        if current in ids and needs_insert and _TURNS_RE.match(line):
            # Insert immediately before "turns", which every scenario has, so
            # the placement is deterministic and does not depend on which
            # optional keys this particular scenario carries.
            compact = _scenario_is_compact(lines, i)
            joined = ", ".join(f'"{p}"' for p in ti.ALL_POSTURES)
            if compact:
                out.append(f'{id_indent}"postures": [{joined}],')
            else:
                out.append(f'{id_indent}"postures": [')
                for k, p in enumerate(ti.ALL_POSTURES):
                    tail = "," if k < len(ti.ALL_POSTURES) - 1 else ""
                    out.append(f'{id_indent}  "{p}"{tail}')
                out.append(f'{id_indent}],')
            changed += 1
            needs_insert = False
            current = None
            out.append(line)
            i += 1
            continue

        if current in ids:
            m_compact = _POSTURES_COMPACT_RE.match(line)
            if m_compact:
                indent, comma = m_compact.group(1), m_compact.group(2)
                joined = ", ".join(f'"{p}"' for p in ti.ALL_POSTURES)
                out.append(f'{indent}"postures": [{joined}]{comma}')
                changed += 1
                current = None
                i += 1
                continue
            m_open = _POSTURES_OPEN_RE.match(line)
            if m_open:
                indent = m_open.group(1)
                # Consume the existing block through its closing bracket, and
                # take the element indent from the first entry so the rewritten
                # block matches the file it lives in.
                j = i + 1
                elem_indent = indent + "  "
                first = True
                while j < len(lines) and not lines[j].lstrip().startswith("]"):
                    if first and lines[j].strip():
                        elem_indent = lines[j][: len(lines[j]) - len(lines[j].lstrip())]
                        first = False
                    j += 1
                if j >= len(lines):
                    raise SystemExit(
                        f"{path}: unterminated postures block for {current!r}")
                comma = "," if lines[j].rstrip().endswith("],") else ""
                out.append(f'{indent}"postures": [')
                for k, p in enumerate(ti.ALL_POSTURES):
                    tail = "," if k < len(ti.ALL_POSTURES) - 1 else ""
                    out.append(f'{elem_indent}"{p}"{tail}')
                out.append(f"{indent}]{comma}")
                changed += 1
                current = None
                i = j + 1
                continue
        out.append(line)
        i += 1
    return "\n".join(out), changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true",
                      help="report what would change; write nothing")
    mode.add_argument("--apply", action="store_true",
                      help="write the widened declarations back to the corpus")
    args = ap.parse_args()

    per_file, total, independent, unknown = _targets()
    changed = 0
    rows: list[tuple[str, int, int]] = []

    for path, ids in per_file.items():
        if not ids:
            continue
        text, n = _rewrite(path, ids)
        if n != len(ids):
            raise SystemExit(
                f"{os.path.basename(path)}: rewrote {n} declaration(s) but "
                f"{len(ids)} scenario(s) were selected — refusing to write a "
                "partial edit")
        # Prove the edit parsed back to the same corpus with only the
        # declarations moved, before anything is written.
        before = json.load(open(path, encoding="utf-8"))
        after = json.loads(text)
        b = before if isinstance(before, list) else before.get("scenarios", [])
        a = after if isinstance(after, list) else after.get("scenarios", [])
        assert len(a) == len(b), f"{path}: scenario count changed"
        for sb, sa in zip(b, a):
            for key in set(sb) | set(sa):
                if key == "postures":
                    continue
                assert sb.get(key) == sa.get(key), (
                    f"{path}: {sb.get('id')} field {key!r} changed")
        changed += n
        rows.append((os.path.basename(path), len(ids), n))
        if args.apply:
            open(path, "w", encoding="utf-8").write(text)

    print(f"scenarios examined        {total}")
    print(f"tier-independent by rule  {independent}")
    print(f"declarations widened      {changed}"
          f"{'  (written)' if args.apply else '  (not written; --check)'}")
    if rows:
        print("\nby corpus file (selected / rewritten):")
        for name, sel, n in rows:
            print(f"  {name:<28} {sel:4d} / {n:4d}")
    if unknown:
        print("\nUNCLASSIFIED assertion types — classify each in "
              "tier_independence.py before trusting this run:")
        for ty, where in sorted(unknown.items()):
            print(f"  {ty}  (e.g. {where})")
        return 2
    if args.check and changed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
