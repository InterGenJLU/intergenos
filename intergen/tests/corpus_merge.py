# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Demand-corpus merge + dedup tool (M8-6).

Merges any set of demand-corpus half-files (the demand-distribution half +
the surface-flex half) into one deduped `bank.jsonl` and emits a distribution
report so the bank's shape is visible at merge.

Design choices (grounded in the arc's constraints):
- **Deterministic dedup, no embedder.** Near-duplicate detection uses normalized
  signatures + token-set Jaccard — pure text, no model, no network, no daemon. That
  makes the RED/GREEN tests reproducible on any box and keeps the tool in the daemon-
  free lane. (The arc allowed "the embedder pattern OR deterministic normalization";
  determinism wins here because the merge must be reproducible and test-pinnable.)
- **Fail-closed validation.** Every line is schema-validated via corpus_loader; a
  duplicate id ACROSS halves is a hard error (a generation bug, not a near-duplicate).
- **Stable, order-independent-of-clock output.** On a near-dup collision the first-seen
  entry (input-file order, then line order) survives; the drop is logged. Same inputs
  always produce the same bank + same report.

Schema contract: `intergen/tests/demand_corpus/README.md`.

CLI:
    python3 -m intergen.tests.corpus_merge A.jsonl B.jsonl -o bank.jsonl
    # bare filenames resolve under intergen/tests/demand_corpus/
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, OrderedDict
from pathlib import Path

from intergen.tests.corpus_loader import CorpusError, iter_corpus_records, validate_entry

CORPUS_DIR = Path(__file__).resolve().parent / "demand_corpus"
GROUNDING_MD = CORPUS_DIR / "grounding_sources.md"
DEFAULT_JACCARD = 0.9

# A small filler/stopword set dropped from signatures so "what's my hostname" and
# "please could you tell me the hostname" collapse toward the same intent signature.
# Deliberately tiny — over-stripping would collapse genuinely-distinct asks.
_FILLER = frozenset({
    "the", "a", "an", "my", "me", "i", "you", "your", "is", "are", "am", "do", "does",
    "please", "could", "can", "would", "will", "to", "of", "on", "in", "for", "and",
    "so", "just", "what", "whats", "how", "s", "this", "that", "it", "with", "if",
    "be", "am", "was", "were", "there", "here", "get", "got", "now", "some", "any",
})

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def normalize_text(text: str) -> list[str]:
    """Lowercase -> extract alnum tokens -> drop filler. Returns the token list."""
    toks = _TOKEN_RE.findall(text.lower())
    return [t for t in toks if t not in _FILLER]


def entry_signature(obj: dict) -> tuple[str, frozenset[str]]:
    """A (canonical-string, token-set) signature over ALL of an entry's turn text.

    Multi-turn entries join their turns so a flow is compared as a whole. The
    canonical string catches exact/normalized-equal matches; the token set feeds
    Jaccard for near-duplicates.
    """
    all_tokens: list[str] = []
    for turn in obj.get("turns", []):
        all_tokens.extend(normalize_text(turn.get("user", "")))
    canonical = " ".join(all_tokens)
    return canonical, frozenset(all_tokens)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def read_grounding_keys(md_path: str | Path = GROUNDING_MD) -> set[str]:
    """Parse the registered grounding keys from grounding_sources.md.

    Keys are the backtick-wrapped headers, e.g. `## \\`openai-howpeopleuse-2025\\``.
    Returns an empty set (no cross-check) if the registry file is absent.
    """
    p = Path(md_path)
    if not p.exists():
        return set()
    keys: set[str] = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^##\s+`([^`]+)`\s*$", line.strip())
        if m:
            keys.add(m.group(1))
    return keys


def merge_records(
    files: list[str | Path], *, jaccard_threshold: float = DEFAULT_JACCARD,
    known_grounding_keys: set[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Validate, id-collision-check, and near-dup-dedup across `files`.

    Returns (kept_records, dedup_log). Raises CorpusError on a schema violation or a
    duplicate id across the input set. `known_grounding_keys=None` skips the grounding
    cross-check; pass `read_grounding_keys()` to enforce it.
    """
    seen_ids: dict[str, str] = {}          # id -> source locator (first seen)
    kept: list[dict] = []
    kept_sigs: list[tuple[str, frozenset[str]]] = []
    canonical_index: dict[str, int] = {}   # exact canonical -> kept index
    dedup_log: list[dict] = []

    for f in files:
        fp = Path(f)
        for lineno, obj in enumerate(iter_corpus_records(fp), start=1):
            locator = f"{fp.name}:{lineno}"
            validate_entry(obj, locator=locator,
                           known_grounding_keys=known_grounding_keys)

            oid = obj["id"]
            if oid in seen_ids:
                raise CorpusError(
                    f"{locator}: duplicate id {oid!r} (first seen at "
                    f"{seen_ids[oid]}) — ids must be globally unique across halves")
            seen_ids[oid] = locator

            canonical, tokens = entry_signature(obj)

            dup_of: str | None = None
            # Exact/normalized-equal match first (O(1)).
            if canonical in canonical_index:
                dup_of = kept[canonical_index[canonical]]["id"]
            else:
                # Near-dup via Jaccard against kept signatures.
                for idx, (_c, ksig) in enumerate(kept_sigs):
                    if jaccard(tokens, ksig) >= jaccard_threshold:
                        dup_of = kept[idx]["id"]
                        break

            if dup_of is not None:
                dedup_log.append({
                    "dropped_id": oid, "kept_id": dup_of, "source": locator,
                })
                continue

            canonical_index[canonical] = len(kept)
            kept.append(obj)
            kept_sigs.append((canonical, tokens))

    return kept, dedup_log


def distribution_report(kept: list[dict], dedup_log: list[dict]) -> dict:
    """Build the distribution report over the kept records."""
    by_category: Counter = Counter()
    by_generator: Counter = Counter()
    by_class: Counter = Counter()
    single_turn = 0
    multi_turn = 0
    for obj in kept:
        by_category[obj["category"]] += 1
        by_generator[obj.get("provenance", {}).get("generator", "unknown")] += 1
        ebc = obj.get("expected_behavior_class") or "unspecified"
        by_class[ebc] += 1
        if len(obj.get("turns", [])) > 1:
            multi_turn += 1
        else:
            single_turn += 1
    return {
        "total": len(kept),
        "dropped_as_duplicate": len(dedup_log),
        "single_turn": single_turn,
        "multi_turn": multi_turn,
        "by_category": OrderedDict(sorted(by_category.items())),
        "by_generator": OrderedDict(sorted(by_generator.items())),
        "by_expected_behavior_class": OrderedDict(sorted(by_class.items())),
        "dedup_log": dedup_log,
    }


def format_report(report: dict) -> str:
    """Human-readable rendering of the distribution report."""
    lines = ["=== Demand-corpus distribution report ==="]
    lines.append(f"total entries:        {report['total']}")
    lines.append(f"dropped as duplicate: {report['dropped_as_duplicate']}")
    lines.append(f"single-turn:          {report['single_turn']}")
    lines.append(f"multi-turn:           {report['multi_turn']}")
    lines.append("by generator:")
    for k, v in report["by_generator"].items():
        lines.append(f"  {k:<20} {v}")
    lines.append("by category:")
    for k, v in report["by_category"].items():
        lines.append(f"  {k:<20} {v}")
    lines.append("by expected-behavior-class:")
    for k, v in report["by_expected_behavior_class"].items():
        lines.append(f"  {k:<20} {v}")
    if report["dedup_log"]:
        lines.append("dedup drops:")
        for d in report["dedup_log"]:
            lines.append(f"  {d['dropped_id']} -> {d['kept_id']} ({d['source']})")
    return "\n".join(lines)


def write_bank(kept: list[dict], out_path: str | Path) -> None:
    """Write the merged bank as JSONL (one compact object per line, stable order)."""
    p = Path(out_path)
    with p.open("w", encoding="utf-8") as fh:
        for obj in kept:
            fh.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")


DEMAND_BANK_HALVES = ("demand_distribution.jsonl", "surface_flex.jsonl")


def regenerate_bank(out_path: str | Path | None = None) -> tuple[Path, dict]:
    """Merge the canonical half-files into bank.jsonl and return (path, report).

    This is the source of truth for the standing 'demand_bank' battery (runner
    --demand-bank): both halves deduped through the same merge the tests pin, so
    the battery drives exactly what corpus_merge validated. bank.jsonl stays
    generated (gitignored) — never committed. Whichever halves are present are
    merged (so the demand half alone still builds a bank before the surface half
    lands, and vice versa).
    """
    halves = [CORPUS_DIR / name for name in DEMAND_BANK_HALVES]
    present = [h for h in halves if h.exists()]
    if not present:
        raise CorpusError(
            f"no demand-corpus half-files present under {CORPUS_DIR} "
            f"(looked for {', '.join(DEMAND_BANK_HALVES)})")
    out = Path(out_path) if out_path else (CORPUS_DIR / "bank.jsonl")
    kept, dedup_log = merge_records(
        present, known_grounding_keys=read_grounding_keys())
    write_bank(kept, out)
    report = distribution_report(kept, dedup_log)
    out.with_suffix(".report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out, report


def _resolve(path: str) -> Path:
    """Bare filenames resolve under the demand_corpus dir; explicit paths pass through."""
    p = Path(path)
    if p.parent == Path("."):
        return CORPUS_DIR / p.name
    return p


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Merge + dedup demand-corpus half-files into one bank.")
    parser.add_argument("inputs", nargs="+", help="input JSONL half-files")
    parser.add_argument("-o", "--output", default="bank.jsonl",
                        help="output bank path (default: bank.jsonl under demand_corpus/)")
    parser.add_argument("--jaccard", type=float, default=DEFAULT_JACCARD,
                        help=f"near-dup token-set Jaccard threshold (default {DEFAULT_JACCARD})")
    parser.add_argument("--no-grounding-check", action="store_true",
                        help="skip the grounding-key registry cross-check")
    parser.add_argument("--report", default=None,
                        help="write the JSON report here (default: <output>.report.json)")
    args = parser.parse_args(argv)

    inputs = [_resolve(i) for i in args.inputs]
    for p in inputs:
        if not p.exists():
            print(f"error: input not found: {p}", file=sys.stderr)
            return 2

    known = None if args.no_grounding_check else read_grounding_keys()
    try:
        kept, dedup_log = merge_records(
            inputs, jaccard_threshold=args.jaccard, known_grounding_keys=known)
    except CorpusError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    out = _resolve(args.output)
    write_bank(kept, out)
    report = distribution_report(kept, dedup_log)
    report_path = Path(args.report) if args.report else out.with_suffix(".report.json")
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")

    print(format_report(report))
    print(f"\nwrote bank: {out}")
    print(f"wrote report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
