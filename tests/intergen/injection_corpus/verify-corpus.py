#!/usr/bin/env python3
"""verify-corpus.py — sanity-check the injection-pattern corpus.

Runs three classes of assertion:

1. Every pattern's regex compiles cleanly under re.compile().
2. Every pattern's fixture file exists AND the pattern's regex matches at
   least one substring in the fixture (positive case — proves the
   fixture exercises the pattern).
3. Every benign-negative fixture exists AND NO pattern from any
   category matches against it (negative case — proves the corpus
   does not generate false positives on legitimate content).

Exit codes:
  0 — all assertions pass
  1 — one or more assertions failed (diagnostic printed)
  2 — corpus loading failed (patterns.json missing or malformed)

Run from any working directory. Looks for patterns.json + fixtures/
relative to this script's own location, so the verify works from CI
checkouts that haven't installed the corpus to a system path.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

CORPUS_DIR = Path(__file__).resolve().parent
PATTERNS_FILE = CORPUS_DIR / "patterns.json"
FIXTURES_DIR = CORPUS_DIR / "fixtures"


def load_corpus() -> dict:
    if not PATTERNS_FILE.is_file():
        print(f"FAIL: patterns.json not found at {PATTERNS_FILE}", file=sys.stderr)
        sys.exit(2)
    try:
        return json.loads(PATTERNS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"FAIL: patterns.json malformed: {exc}", file=sys.stderr)
        sys.exit(2)


def load_fixture(name: str) -> str | None:
    path = FIXTURES_DIR / name
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def main() -> int:
    corpus = load_corpus()
    failures: list[str] = []
    pattern_count = 0
    fixture_count = 0
    negative_count = 0

    compiled_patterns: list[tuple[str, str, re.Pattern]] = []

    for cat_id, cat in corpus.get("categories", {}).items():
        for pat in cat.get("patterns", []):
            pat_id = pat["id"]
            pattern_count += 1

            try:
                regex = re.compile(pat["regex"])
            except re.error as exc:
                failures.append(f"[{cat_id}/{pat_id}] regex compile failed: {exc}")
                continue

            compiled_patterns.append((cat_id, pat_id, regex))

            fixture_name = pat.get("fixture")
            if not fixture_name:
                failures.append(f"[{cat_id}/{pat_id}] missing 'fixture' field")
                continue

            fixture_text = load_fixture(fixture_name)
            if fixture_text is None:
                failures.append(f"[{cat_id}/{pat_id}] fixture file missing: fixtures/{fixture_name}")
                continue

            fixture_count += 1

            if not regex.search(fixture_text):
                failures.append(
                    f"[{cat_id}/{pat_id}] regex did NOT match its fixture {fixture_name} "
                    f"(positive case failed — fixture does not exercise the pattern)"
                )

    for neg in corpus.get("benign_negatives", []):
        neg_id = neg["id"]
        fixture_name = neg.get("fixture")
        negative_count += 1

        if not fixture_name:
            failures.append(f"[benign/{neg_id}] missing 'fixture' field")
            continue

        fixture_text = load_fixture(fixture_name)
        if fixture_text is None:
            failures.append(f"[benign/{neg_id}] fixture file missing: fixtures/{fixture_name}")
            continue

        for cat_id, pat_id, regex in compiled_patterns:
            match = regex.search(fixture_text)
            if match:
                failures.append(
                    f"[benign/{neg_id}] pattern [{cat_id}/{pat_id}] unexpectedly matched: "
                    f"{match.group(0)!r} (false-positive — benign content should not match)"
                )

    summary = (
        f"verify-corpus: {pattern_count} patterns + {fixture_count} positive fixtures "
        f"+ {negative_count} benign negatives"
    )

    if failures:
        print(summary, file=sys.stderr)
        for f in failures:
            print(f"  FAIL: {f}", file=sys.stderr)
        print(f"{len(failures)} failure(s)", file=sys.stderr)
        return 1

    print(f"PASS: {summary}; all assertions hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
