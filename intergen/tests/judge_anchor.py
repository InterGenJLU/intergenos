# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Judge anchor set — re-grade runner (measurement-honesty guard 2).

The anchor set is a frozen collection of historic assistant replies, each stored
with the verdict the harness recorded for it when it was captured. The replies
cannot change. So when the same judge is pointed at them again at the start of a
round and the scores come out different, the JUDGE moved — not the model under
test. Any round whose deltas are read without that check is reading judge drift
as progress.

This module does three things and nothing else:

* ``load_anchor_set`` — read a frozen set and REFUSE it unless its seal verifies.
  A silently mutated anchor is not a weaker measurement, it is a false one, so a
  bad seal raises rather than warns.
* ``regrade_anchor_set`` — grade every item through the SAME code path the
  harness uses (``quality_judge.judge_turn``), not a re-implementation of it. If
  the judge prompt or the verdict composition changes in that module, this
  runner changes with it automatically; a copied prompt would drift apart
  silently, which is the exact failure this guard exists to catch.
* ``compare_rounds`` — diff two rounds' outputs into per-item and per-band
  movement, refusing to compare two different sets.

Fidelity note, stated because it bounds what the number means: anchors carry the
recorded reply text, not the assembled prompt that produced it. Grading therefore
runs at the same fidelity as the harness's own no-glass path
(``quality_judge._inputs_for_turn`` when glass rows are absent) — the reply is
judged, its original prompt context is not reconstructed.

That note used to be the whole of the treatment: the limitation was written down
and then nothing enforced it, so a dimension the replay CANNOT reproduce still
counted as movement in the drift arithmetic. Measured consequence (2026-08-07):
one banked reply's recorded verdict was ``no_fabrication`` = fail, the replay
returned pass, every other dimension reproduced exactly, and the resulting
overall difference read as judge drift when it was a context difference. This
module now carries a DECLARED fidelity model instead — see
:data:`REPLAY_UNMEASURABLE_DIMENSIONS` — and the fail-closed rule that follows
from it: an overall difference explained entirely by dimensions the replay
cannot see is reported, but never counted as drift.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from intergen.tests.quality_judge import (
    JUDGE_MODEL_DEFAULT,
    JUDGE_TEMPERATURE,
    RUBRIC_IDS,
    JudgeInputs,
    judge_client_from_endpoint,
    judge_turn,
)

VERDICT_RANK = {"pass": 0, "flag": 1, "fail": 2}


# ---------------------------------------------------------------------------
# THE DECLARED FIDELITY MODEL — what a frozen-reply replay can actually measure.
# ---------------------------------------------------------------------------
# A replay hands the judge the user's question, the reply text and its source.
# It hands it NOTHING about what the assistant dispatched: not the tool calls
# that ran, and not the fact that none did. A rubric dimension whose question
# cannot be answered from what the replay supplies is NOT MEASURABLE here, and
# its verdict must never be read as the judge changing its mind.
#
# Which dimension that is, and why — the STRUCTURAL reason, which is the one that
# holds:
#
#   no_fabrication   its rubric asks whether the reply claims an action that was
#                    never dispatched. That question is unanswerable from a reply
#                    text alone: it is a question ABOUT the dispatch record, and
#                    the replay supplies none. A judge that answers it anyway is
#                    guessing, and a guess is not a measurement however it lands.
#                    NOT MEASURABLE.
#   correct          answerable from the reply against the rubric's own stated
#                    ground truth, which the judge is given in full. MEASURABLE —
#                    quarantining it would have thrown away the dimension this set
#                    measures best.
#   on_target · right_sized · not_asshole · honest
#                    each asks about the reply itself — what it addresses, its
#                    length, its tone, its hedging — and the reply is exactly what
#                    the replay supplies. MEASURABLE.
#
# The structural reason is stated alone because the empirical one that stood here
# first did not survive contact with a second machine. It read "no_fabrication
# disagrees with the record on 4 of 49 items and the direction is one-way", which
# was measured on one host and is a property of that host: on a second host, the
# same code with the same inputs returns 'fail' on the item that produced the
# clearest one-way disagreement, reproducing the recorded verdict instead of
# contradicting it. The two hosts differ on a fixed set of three frozen replies,
# with the direction reversing item by item, and neither is the reference. That
# cross-host result is itself the sharpest argument for the quarantine: two judges
# handed identical evidence answer this dimension differently and confidently,
# which is what guessing looks like from the outside.
#
# FAIL CLOSED, and note the direction of the derivation: the MEASURABLE set is
# the one written out by hand, and everything else in the rubric is unmeasurable
# by subtraction. A dimension added to the rubric therefore cannot silently start
# counting as drift — it is quarantined until somebody classifies it here. The
# reverse derivation would have been fail-open and is the easy mistake.
REPLAY_MEASURABLE_DIMENSIONS: frozenset[str] = frozenset({
    "correct", "on_target", "right_sized", "not_asshole", "honest",
})


def unmeasurable_dimensions(rubric_ids=RUBRIC_IDS) -> frozenset[str]:
    """Everything in the rubric that is not DECLARED measurable above.

    Written as a function of the rubric, not as a hand-listed constant, so the
    fail-closed direction is executable and can be tested against a rubric that
    grows: pass a rubric containing a dimension nobody has classified and it
    comes back quarantined. A hand-listed unmeasurable set would be fail-open —
    a new dimension would land in 'measurable' by omission, which is the error
    this shape exists to make impossible."""
    return frozenset(rubric_ids) - REPLAY_MEASURABLE_DIMENSIONS


REPLAY_UNMEASURABLE_DIMENSIONS: frozenset[str] = unmeasurable_dimensions()
REPLAY_UNMEASURABLE_REASON: dict[str, str] = {
    "no_fabrication":
        "asks whether the reply claims an action that was never dispatched; the "
        "replay supplies no dispatch record, not even the absence of one",
}
UNCLASSIFIED_DIMENSION_REASON = (
    "not classified in REPLAY_MEASURABLE_DIMENSIONS — quarantined until someone "
    "establishes, by measurement, that a replay can reproduce it")

# What the replay actually feeds the judge. Written into every round record so
# two rounds taken at DIFFERENT fidelity can be refused rather than silently
# compared — a change to these inputs moves verdicts, and a verdict moved by an
# input change is not drift.
REPLAY_INPUT_FIELDS: tuple[str, ...] = ("user_input", "response_text", "source")
REPLAY_WITHHELD_EVIDENCE: tuple[str, ...] = (
    "assembled_prompt (the model-facing prompt, including the system prompt)",
    "tool_calls (the dispatch record the bank holds but the replay does not feed)",
    "tool results (the bank does not hold these at all)",
)


def replay_fidelity_signature() -> dict:
    """A stable description of what this replay conveys to the judge.

    Two rounds are only comparable if this is identical in both. It is written
    into every round record and checked by :func:`compare_rounds`."""
    return {
        "input_fields": list(REPLAY_INPUT_FIELDS),
        "withheld_evidence": list(REPLAY_WITHHELD_EVIDENCE),
        "unmeasurable_dimensions": sorted(REPLAY_UNMEASURABLE_DIMENSIONS),
        "measurable_dimensions": sorted(REPLAY_MEASURABLE_DIMENSIONS),
    }


def _dimension_disagreements(recorded: dict, replay: dict) -> list[str]:
    """Dimensions present on BOTH sides whose verdicts differ. A dimension missing
    from either side is NOT silently treated as agreement — see
    :func:`classify_item_fidelity`, which refuses to attribute in that case."""
    return sorted(d for d in recorded if d in replay and recorded[d] != replay[d])


def classify_item_fidelity(*, recorded_dimensions: dict | None,
                           replay_dimensions: dict, moved: bool) -> dict:
    """Decide whether ONE item's recorded-vs-replay movement may count as drift.

    The rule, fail-closed in both of its branches:

    * If the item's recorded per-dimension verdicts are absent, nothing can be
      attributed, so the movement is NOT countable. (One banked item is in this
      state; treating an unattributable difference as drift is the error this
      whole change exists to stop.)
    * If the item moved and EVERY dimension that differs is one the replay
      cannot measure, the difference is explained by the missing evidence and is
      NOT countable.

    An item that did not move stays countable: its dimensions are still reported,
    including any unmeasurable one that differs, so an agreement that holds only
    by luck is visible rather than hidden."""
    unmeasurable = sorted(REPLAY_UNMEASURABLE_DIMENSIONS)
    if not recorded_dimensions:
        return {
            "not_measurable_dimensions": unmeasurable,
            "unmeasurable_dimensions_differing": [],
            "drift_countable": False,
            "reason": "the frozen item carries no recorded per-dimension verdicts, "
                      "so an overall difference cannot be attributed to any "
                      "dimension — not counted (fail closed)",
        }
    differing = _dimension_disagreements(recorded_dimensions, replay_dimensions)
    differing_unmeasurable = [d for d in differing if d in REPLAY_UNMEASURABLE_DIMENSIONS]
    if moved and differing and set(differing) <= REPLAY_UNMEASURABLE_DIMENSIONS:
        return {
            "not_measurable_dimensions": unmeasurable,
            "unmeasurable_dimensions_differing": differing_unmeasurable,
            "drift_countable": False,
            "reason": "every dimension that differs from the record is one the "
                      "replay cannot measure (" + ", ".join(
                          f"{d}: {REPLAY_UNMEASURABLE_REASON.get(d, UNCLASSIFIED_DIMENSION_REASON)}"
                          for d in differing) + ") — reported, not counted",
        }
    return {
        "not_measurable_dimensions": unmeasurable,
        "unmeasurable_dimensions_differing": differing_unmeasurable,
        "drift_countable": True,
        "reason": "",
    }


class AnchorSetError(Exception):
    """The anchor set cannot be trusted — a bad seal, a missing file, or two
    different sets being compared. Always fatal: a measurement guard that
    proceeds on a broken input is not a guard."""


@dataclass
class AnchorSet:
    root: Path
    set_id: str
    manifest: dict
    items: list[dict] = field(default_factory=list)
    manifest_sha256: str = ""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_anchor_set(root: str | Path) -> AnchorSet:
    """Load a frozen anchor set, verifying its seal file first.

    Raises AnchorSetError if SHA256SUMS is absent, if any listed file is missing,
    or if any file's digest does not match. The exception names the offending
    file so a tampered or half-copied set is identifiable, not merely rejected.
    """
    root = Path(root)
    seal = root / "SHA256SUMS"
    if not seal.is_file():
        raise AnchorSetError(f"anchor set {root} has no SHA256SUMS — refusing to "
                             "re-grade an unsealed set")
    for line in seal.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        digest, _, rel = line.partition("  ")
        target = root / rel
        if not target.is_file():
            raise AnchorSetError(f"anchor set {root}: sealed file missing: {rel}")
        actual = _sha256(target)
        if actual != digest:
            raise AnchorSetError(
                f"anchor set {root}: seal mismatch for {rel} "
                f"(sealed {digest[:12]}…, found {actual[:12]}…) — the set has been "
                "modified since it was frozen and cannot measure drift")

    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    items = []
    for entry in manifest.get("items", []):
        item_path = root / "items" / f"{entry['item_id']}.json"
        if not item_path.is_file():
            raise AnchorSetError(f"anchor set {root}: manifest lists "
                                 f"{entry['item_id']} but its file is absent")
        items.append(json.loads(item_path.read_text()))
    if not items:
        raise AnchorSetError(f"anchor set {root} contains no items")
    return AnchorSet(root=root, set_id=manifest.get("set_id", ""), manifest=manifest,
                     items=items, manifest_sha256=_sha256(manifest_path))


def _inputs_for_item(item: dict) -> JudgeInputs:
    """Mirror the harness's no-glass path exactly (quality_judge._inputs_for_turn).

    The bank's recorded ``tool_calls`` are deliberately NOT fed in here, and the
    reason is measured rather than assumed. They carry the tool NAME and its
    ARGUMENTS but no tool RESULT — the bank holds no results at all — so feeding
    them would tell the judge that a search was dispatched while leaving it blind
    to what came back. On the one banked item this was meant to fix, that is the
    wrong direction: knowing a package search ran makes an invented package name
    look MORE grounded, not less. It would also change what the judge sees on
    every item that carries tool calls, which moves verdicts and makes every
    already-banked round non-comparable. Fidelity is therefore DECLARED here, not
    faked; :func:`classify_item_fidelity` refuses to count what this cannot see.
    If the bank one day carries tool results, feeding them is the right change and
    :func:`replay_fidelity_signature` is what will make the old rounds refuse to
    compare against the new ones."""
    text = item.get("response_text", "") or ""
    return JudgeInputs(
        user_input=item.get("user_input", ""),
        assembled_prompt="",
        model_output=text,
        delivered=text,
        source=item.get("source", "") or "",
    )


def build_judge_client(endpoint: str, *, model: str = JUDGE_MODEL_DEFAULT,
                       temperature: float = JUDGE_TEMPERATURE,
                       timeout: float = 120.0) -> Callable[[str], str]:
    """The live judge client. Reuses the shared builder so the same-family refusal
    (a judge of the assistant's own family self-prefers) applies here unchanged."""
    return judge_client_from_endpoint(endpoint, model=model, temperature=temperature,
                                      timeout=timeout)


def regrade_anchor_set(anchor_set: AnchorSet, *,
                       judge_client: Callable[[str], str],
                       judge_model: str = "", endpoint: str = "") -> dict:
    """Re-grade every item and return a round record.

    The record's schema is stable on purpose: rounds are compared to each other,
    so a field that changes shape between rounds destroys the comparison the
    whole guard exists for.
    """
    rows = []
    for item in anchor_set.items:
        verdict = judge_turn(_inputs_for_item(item), judge_client=judge_client)
        recorded_verdicts = item.get("recorded_verdicts") or {}
        recorded = recorded_verdicts.get("judge_overall")
        overall = verdict.overall
        unparseable = verdict.reasoning.startswith("judge reply unparseable")
        replay_dims = {d: dv.verdict for d, dv in verdict.dimensions.items()}
        moved = recorded != overall
        fidelity = classify_item_fidelity(
            recorded_dimensions=recorded_verdicts.get("judge_dimensions") or {},
            replay_dimensions=replay_dims, moved=moved)
        rows.append({
            "item_id": item["item_id"],
            "band": item.get("band", ""),
            "provenance": item.get("provenance", {}),
            "recorded_judge_overall": recorded,
            "regrade_overall": overall,
            "moved": moved,
            "direction": (0 if recorded == overall else
                          VERDICT_RANK.get(overall, 0) - VERDICT_RANK.get(recorded, 0)),
            "regrade_dimensions": replay_dims,
            "regrade_evidence": {d: dv.evidence for d, dv in verdict.dimensions.items()},
            "unparseable": unparseable,
            "reasoning": verdict.reasoning[:500],
            "replay_fidelity": fidelity,
        })
    return {
        "set_id": anchor_set.set_id,
        "manifest_sha256": anchor_set.manifest_sha256,
        "judge_model": judge_model,
        "judge_endpoint": endpoint,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "replay_fidelity": replay_fidelity_signature(),
        "items": rows,
    }


def _countable(row: dict) -> bool:
    """Whether this row's recorded-vs-replay movement may be read as drift.

    A row written before the fidelity model existed has no ``replay_fidelity``
    key. Such a row is treated as countable so an old record still summarizes,
    and :func:`compare_rounds` is what refuses to read it as comparable with a
    new one."""
    return row.get("replay_fidelity", {}).get("drift_countable", True)


def summarize(round_record: dict) -> dict:
    """Round-level movement against the recorded verdicts.

    Two arithmetics are reported side by side and neither replaces the other.
    ``moved`` is the raw count, unchanged, so the number stays continuous with
    every previously banked round. ``moved_measurable`` is the count that may
    honestly be read as judge drift: it excludes the items whose difference from
    the record is explained by a dimension the replay cannot see, and it names
    them rather than dropping them quietly."""
    rows = round_record["items"]
    total = len(rows)
    moved = sum(1 for r in rows if r["moved"])
    countable = [r for r in rows if _countable(r)]
    not_countable = [r for r in rows if not _countable(r)]
    moved_measurable = sum(1 for r in countable if r["moved"])
    by_band: dict[str, dict] = {}
    for r in rows:
        b = by_band.setdefault(r["band"], {"items": 0, "moved": 0,
                                           "harsher": 0, "softer": 0})
        b["items"] += 1
        if r["moved"]:
            b["moved"] += 1
            if r["direction"] > 0:
                b["harsher"] += 1
            else:
                b["softer"] += 1
    return {
        "set_id": round_record["set_id"],
        "judge_model": round_record.get("judge_model", ""),
        "items_total": total,
        "moved": moved,
        "agreement_rate": round((total - moved) / total, 4) if total else 0.0,
        "harsher": sum(1 for r in rows if r["direction"] > 0),
        "softer": sum(1 for r in rows if r["direction"] < 0),
        "unparseable": sum(1 for r in rows if r["unparseable"]),
        "by_band": by_band,
        "verdict_counts": {
            v: sum(1 for r in rows if r["regrade_overall"] == v)
            for v in ("pass", "flag", "fail")
        },
        # --- the fidelity-aware read ---
        "items_drift_countable": len(countable),
        "moved_measurable": moved_measurable,
        "agreement_rate_measurable": (
            round((len(countable) - moved_measurable) / len(countable), 4)
            if countable else 0.0),
        "not_drift_countable": [
            {"item_id": r["item_id"],
             "reason": r.get("replay_fidelity", {}).get("reason", "")}
            for r in not_countable
        ],
        "unmeasurable_dimensions": sorted(REPLAY_UNMEASURABLE_DIMENSIONS),
    }


def compare_rounds(round_a: dict, round_b: dict) -> dict:
    """Per-item movement between two rounds of the SAME set.

    This is the drift read: the anchors are identical in both rounds, so every
    change here is the judge changing its mind about text that did not change.

    ROUND-TO-ROUND MOVEMENT IS NOT REDUCED BY THE FIDELITY MODEL, and that is
    deliberate. Both rounds are replays at the SAME fidelity, so whatever the
    replay withholds, it withholds from both sides equally; a verdict that
    differs between them is the judge changing its mind on identical input,
    which is exactly what this function is for. Excluding the unmeasurable
    dimensions here would discard real drift signal. What the fidelity model
    changes here instead is a refusal: two rounds taken at DIFFERENT fidelity
    are not comparable at all, because an input change moves verdicts and a
    verdict moved by an input change is not drift. The signature is checked with
    the same fail-loud posture as the set id, and a round written before the
    signature existed is refused by name rather than assumed to match.
    """
    if round_a.get("set_id") != round_b.get("set_id"):
        raise AnchorSetError(
            f"refusing to compare different anchor sets: "
            f"{round_a.get('set_id')!r} vs {round_b.get('set_id')!r}")
    fid_a, fid_b = round_a.get("replay_fidelity"), round_b.get("replay_fidelity")
    if fid_a is None or fid_b is None:
        missing = [n for n, f in (("A", fid_a), ("B", fid_b)) if f is None]
        raise AnchorSetError(
            f"refusing to compare rounds: round {' and '.join(missing)} carries no "
            "replay_fidelity signature, so there is no way to tell whether it was "
            "graded at the same fidelity as the other — re-run it on this code")
    if fid_a != fid_b:
        raise AnchorSetError(
            "refusing to compare rounds taken at DIFFERENT replay fidelity: the "
            "judge was given different evidence in each, so a verdict difference "
            "between them is an input change, not judge drift. "
            f"round A {json.dumps(fid_a, sort_keys=True)} vs "
            f"round B {json.dumps(fid_b, sort_keys=True)}")
    a_by = {r["item_id"]: r for r in round_a["items"]}
    b_by = {r["item_id"]: r for r in round_b["items"]}
    shared = [i for i in a_by if i in b_by]
    changes = []
    by_band: dict[str, dict] = {}
    for item_id in shared:
        a, b = a_by[item_id], b_by[item_id]
        band = a.get("band", "")
        slot = by_band.setdefault(band, {"items": 0, "changed": 0})
        slot["items"] += 1
        if a["regrade_overall"] != b["regrade_overall"]:
            slot["changed"] += 1
            changes.append({
                "item_id": item_id, "band": band,
                "from": a["regrade_overall"], "to": b["regrade_overall"],
                "trace_id": a.get("provenance", {}).get("trace_id"),
            })
    return {
        "set_id": round_a.get("set_id"),
        "items_compared": len(shared),
        "only_in_a": sorted(set(a_by) - set(b_by)),
        "only_in_b": sorted(set(b_by) - set(a_by)),
        "changed": len(changes),
        "stability_rate": round((len(shared) - len(changes)) / len(shared), 4) if shared else 0.0,
        "changes": sorted(changes, key=lambda c: c["item_id"]),
        "by_band": by_band,
        "replay_fidelity": fid_a,
        "fidelity_note": (
            "both rounds are replays at the identical fidelity above, so the "
            "movement counted here is judge drift and the unmeasurable dimensions "
            "are NOT excluded from it. The fidelity model applies to the "
            "recorded-verdict comparison in summarize(), where one side saw "
            "evidence the other did not."),
    }


def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Re-grade a frozen judge anchor set, or compare two rounds.")
    ap.add_argument("--set", dest="set_path",
                    help="path to the frozen anchor set directory")
    ap.add_argument("--judge-endpoint",
                    help="OpenAI-compatible /v1/chat/completions URL of the judge")
    ap.add_argument("--model", default=JUDGE_MODEL_DEFAULT,
                    help=f"judge model id (default {JUDGE_MODEL_DEFAULT})")
    ap.add_argument("--out", help="write the round record here as JSON")
    ap.add_argument("--compare", nargs=2, metavar=("ROUND_A", "ROUND_B"),
                    help="compare two previously written round records")
    args = ap.parse_args(argv)

    if args.compare:
        a = json.loads(Path(args.compare[0]).read_text())
        b = json.loads(Path(args.compare[1]).read_text())
        diff = compare_rounds(a, b)
        print(json.dumps(diff, indent=2))
        return 0

    if not args.set_path or not args.judge_endpoint:
        ap.error("--set and --judge-endpoint are required unless --compare is used")

    anchor_set = load_anchor_set(args.set_path)
    client = build_judge_client(args.judge_endpoint, model=args.model)
    record = regrade_anchor_set(anchor_set, judge_client=client,
                                judge_model=args.model, endpoint=args.judge_endpoint)
    summary = summarize(record)
    record["summary"] = summary
    if args.out:
        Path(args.out).write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
