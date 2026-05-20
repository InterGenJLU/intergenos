"""Step 12 — D-008 RFC §5.2 injection-pattern corpus integration tests.

Consumes the SPOC-owned canonical corpus at
`tests/intergen/injection_corpus/patterns.json` + `fixtures/` (landed
2026-05-19T23:47:20Z at commit 7ca108fb).

Corpus format (SPOC's canonical, documented at
`tests/intergen/injection_corpus/README.md`):

    {
      "version": "0.1",
      "categories": {
        "<cat-id>": {
          "patterns": [
            {"id": "stable-kebab-id", "regex": "...", "fixture": "name.txt"}
          ]
        }
      },
      "benign_negatives": [
        {"id": "...", "fixture": "...", "should_not_match_any_pattern": true}
      ]
    }

Tests:
  - scanner shape: empty corpus + malformed regex + matched-text truncation
  - corpus integrity: every pattern's fixture file exists and is readable
  - self-consistency: every pattern's regex matches its referenced fixture
  - selectivity: every pattern does NOT match any benign-negative fixture
    (the false-positive regression guard SPOC's verify-corpus.py also checks)

Skip behavior: corpus-empty case (no patterns.json) skips the
corpus-dependent tests with an informative reason and continues to run
scanner-shape tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from intergen.pattern_detect import (
    InjectionPattern,
    scan_for_injection_patterns,
)

CORPUS_DIR = Path(__file__).parent / "injection_corpus"
PATTERNS_JSON = CORPUS_DIR / "patterns.json"
FIXTURES_DIR = CORPUS_DIR / "fixtures"


def _load_corpus_json() -> dict | None:
    """Load patterns.json or return None if the corpus is not in tree yet."""
    if not PATTERNS_JSON.exists():
        return None
    try:
        return json.loads(PATTERNS_JSON.read_text())
    except json.JSONDecodeError as exc:
        pytest.fail(f"patterns.json malformed: {exc}")


def _all_patterns(corpus: dict) -> list[dict]:
    """Flatten corpus['categories'][*]['patterns'] into one list, each
    entry annotated with its category id for test reporting.
    """
    rows: list[dict] = []
    for cat_id, cat in corpus.get("categories", {}).items():
        for pat in cat.get("patterns", []):
            row = dict(pat)
            row["_category"] = cat_id
            rows.append(row)
    return rows


def _benign_negatives(corpus: dict) -> list[dict]:
    return list(corpus.get("benign_negatives", []))


def _read_fixture(fixture_name: str) -> str:
    """Read a fixture file's content; raise if missing so tests fail loud."""
    path = FIXTURES_DIR / fixture_name
    if not path.exists():
        raise FileNotFoundError(
            f"Corpus fixture not found: {path}. "
            f"Either patterns.json references a missing fixture or the "
            f"fixtures/ directory is out of sync."
        )
    return path.read_text()


def _to_injection_pattern(entry: dict) -> InjectionPattern:
    return InjectionPattern(
        pattern_id=entry["id"],
        regex=entry["regex"],
        description=entry.get("description", entry.get("_category", "")),
    )


def _pattern_id(entry: dict) -> str:
    return entry.get("id", entry.get("_source_file", "unknown"))


_CORPUS = _load_corpus_json()
_ALL_PATTERNS = _all_patterns(_CORPUS) if _CORPUS else []
_BENIGN_NEGATIVES = _benign_negatives(_CORPUS) if _CORPUS else []


# ── Scanner-shape tests (run regardless of corpus state) ──


def test_scanner_returns_empty_list_for_empty_corpus():
    """Sanity: empty corpus + any content yields zero hits."""
    matches = scan_for_injection_patterns(
        "any content whatsoever — even an injection-shaped string",
        [],
    )
    assert matches == []


def test_scanner_skips_malformed_regex_gracefully():
    """A corpus entry with a syntactically invalid regex MUST NOT crash
    the scanner; the bad entry is silently skipped (debug-logged) so the
    rest of the corpus continues to function.
    """
    corpus = [
        InjectionPattern(pattern_id="bad-regex", regex="[unclosed"),
        InjectionPattern(
            pattern_id="good-regex",
            regex=r"deliberately match this text",
        ),
    ]
    matches = scan_for_injection_patterns(
        "the content contains deliberately match this text",
        corpus,
    )
    assert len(matches) == 1
    assert matches[0].pattern_id == "good-regex"


def test_scanner_returns_match_substring_truncated_to_200_chars():
    """Per RFC §9 + audit-log size constraints, matched_text is capped
    at 200 chars + ellipsis so a long match does not bloat the audit row.
    """
    pattern = InjectionPattern(
        pattern_id="long-match",
        regex=r"A{1,500}",
    )
    matches = scan_for_injection_patterns("A" * 500, [pattern])
    assert len(matches) == 1
    assert len(matches[0].matched_text) <= 203
    assert matches[0].matched_text.endswith("...")


# ── Corpus-dependent tests (skip cleanly when corpus is empty) ──


def test_corpus_loaded_or_skip():
    """One always-present test that emits a clear skip reason when the
    corpus is not in tree yet (so the test suite's skip count surfaces
    'corpus missing' as a visible signal rather than silent zero-tests).
    """
    if _CORPUS is None:
        pytest.skip(
            "injection corpus not in tree at "
            "tests/intergen/injection_corpus/patterns.json "
            "(SPOC Q6 deliverable)"
        )
    assert "categories" in _CORPUS
    assert _ALL_PATTERNS, "corpus has no patterns"


@pytest.mark.parametrize(
    "pattern_entry",
    _ALL_PATTERNS,
    ids=_pattern_id,
)
def test_each_pattern_matches_its_fixture(pattern_entry):
    """Self-consistency: each pattern's regex must match its referenced
    fixture file (the same invariant SPOC's verify-corpus.py asserts).
    """
    fixture_name = pattern_entry["fixture"]
    fixture_content = _read_fixture(fixture_name)
    pattern = _to_injection_pattern(pattern_entry)
    matches = scan_for_injection_patterns(fixture_content, [pattern])
    assert len(matches) == 1, (
        f"pattern {pattern_entry['id']} (category {pattern_entry['_category']}) "
        f"regex {pattern_entry['regex']!r} did not match its fixture "
        f"{fixture_name!r}"
    )


@pytest.mark.parametrize(
    "negative_entry",
    _BENIGN_NEGATIVES,
    ids=lambda e: e.get("id", "unknown"),
)
def test_benign_negative_matches_no_pattern(negative_entry):
    """Selectivity: each benign negative fixture must NOT be matched by
    ANY pattern from ANY category — the false-positive regression guard.
    Mirrors SPOC's verify-corpus.py assertion 3.
    """
    fixture_name = negative_entry["fixture"]
    fixture_content = _read_fixture(fixture_name)
    all_patterns = [_to_injection_pattern(p) for p in _ALL_PATTERNS]
    matches = scan_for_injection_patterns(fixture_content, all_patterns)
    assert matches == [], (
        f"benign negative {negative_entry['id']} (fixture {fixture_name!r}) "
        f"matched {len(matches)} pattern(s): "
        f"{[m.pattern_id for m in matches]} — selectivity regression"
    )
