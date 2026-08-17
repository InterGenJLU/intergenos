# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""RED-provable, daemon-free tests for the judge anchor re-grade runner.

The anchor set is a frozen set of historic responses carrying the verdicts
recorded for them when they were captured. Re-grading it with the pinned judge at
the start of every round is how judge drift is detected: if these scores move,
the judge moved, because the responses cannot.

That makes the runner an instrument, and an instrument needs its own negative
controls:

  * a set whose seal does not verify is REFUSED, not quietly re-graded — a
    mutated anchor measures nothing;
  * an unchanged judge produces zero movement (the negative control), and a
    changed verdict is reported as movement (the positive control) — a drift
    report that cannot show drift is worse than none;
  * a same-family judge is refused, reusing the shared guard;
  * an unparseable judge reply escalates its own item and the run still
    completes, mirroring judge_turn's blast radius.

Every judge call here is stubbed. No model, no network, green on any box.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from intergen.tests.judge_anchor import (
    AnchorSetError,
    compare_rounds,
    load_anchor_set,
    regrade_anchor_set,
    summarize,
)

_DIMS = ["correct", "on_target", "no_fabrication", "right_sized", "not_asshole", "honest"]


def _reply(verdict: str) -> str:
    return json.dumps({
        "reasoning": "stub",
        "dimensions": {d: {"verdict": verdict, "evidence": "\"quoted span\""} for d in _DIMS},
    })


def _write_set(root: Path, items, *, seal: bool = True) -> Path:
    (root / "items").mkdir(parents=True, exist_ok=True)
    index = []
    for item in items:
        (root / "items" / f"{item['item_id']}.json").write_text(
            json.dumps(item, indent=2) + "\n")
        index.append({
            "item_id": item["item_id"], "band": item["band"],
            "leg": item["provenance"]["leg"],
            "conversation_id": item["provenance"]["conversation_id"],
            "turn_num": item["provenance"]["turn_num"],
            "trace_id": item["provenance"]["trace_id"],
            "recorded_judge_overall": item["recorded_verdicts"]["judge_overall"],
            "recorded_grade": item["recorded_verdicts"]["grade"],
        })
    (root / "manifest.json").write_text(json.dumps({
        "set_id": "test-anchor-v1", "immutable": True,
        "items_total": len(index), "items": index,
        "counts": {"broken": sum(1 for i in index if i["band"] == "broken")},
    }, indent=2) + "\n")
    if seal:
        lines = []
        for p in [root / "manifest.json"] + sorted((root / "items").glob("*.json")):
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            lines.append(f"{digest}  {p.relative_to(root)}")
        (root / "SHA256SUMS").write_text("\n".join(lines) + "\n")
    return root


def _item(n: int, band: str, recorded: str) -> dict:
    return {
        "item_id": f"anchor-{n:04d}", "set_id": "test-anchor-v1", "band": band,
        "provenance": {"leg": "leg2", "tier": "2B", "run_id": "run_x",
                       "source_capture": "/banked/results.json",
                       "source_sha256": "0" * 64,
                       "conversation_id": f"conv_{n}", "conversation_name": "c",
                       "category": "cat", "turn_num": 1, "trace_id": f"trace{n:04d}"},
        "user_input": "get me htop",
        "response_text": "some historic reply",
        "source": "llm",
        "tool_calls": [],
        "recorded_verdicts": {"grade": "PASS", "gate_a": "PASS", "gate_b": "PASS",
                              "judge_overall": recorded, "judge_dimensions": {}},
        "measures": {"letter_ratio": 0.8, "response_chars": 19,
                     "non_linguistic_screen": False},
    }


class LoadingRefusesAMutatedSet(unittest.TestCase):
    def test_seal_must_verify(self):
        with tempfile.TemporaryDirectory() as td:
            root = _write_set(Path(td), [_item(1, "broken", "fail")])
            loaded = load_anchor_set(root)
            self.assertEqual(len(loaded.items), 1)

            # mutate one byte of an item after sealing
            p = root / "items" / "anchor-0001.json"
            obj = json.loads(p.read_text())
            obj["response_text"] = "tampered"
            p.write_text(json.dumps(obj, indent=2) + "\n")
            with self.assertRaises(AnchorSetError) as ctx:
                load_anchor_set(root)
            self.assertIn("anchor-0001", str(ctx.exception))

    def test_missing_seal_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = _write_set(Path(td), [_item(1, "broken", "fail")], seal=False)
            with self.assertRaises(AnchorSetError):
                load_anchor_set(root)


class RegradeReportsMovement(unittest.TestCase):
    def _set(self, td):
        return _write_set(Path(td), [
            _item(1, "broken", "fail"),
            _item(2, "mediocre", "flag"),
            _item(3, "excellent", "pass"),
        ])

    def test_no_movement_when_the_judge_agrees(self):
        """NEGATIVE CONTROL: a judge that reproduces the recorded verdicts must
        report zero drift. If this ever reports movement, the instrument is
        manufacturing it."""
        with tempfile.TemporaryDirectory() as td:
            aset = load_anchor_set(self._set(td))

            def client(prompt: str, _seq=iter(["fail", "flag", "pass"])) -> str:
                return _reply(next(_seq))

            result = regrade_anchor_set(aset, judge_client=client)
            s = summarize(result)
            self.assertEqual(s["items_total"], 3)
            self.assertEqual(s["moved"], 0, s)
            self.assertEqual(s["agreement_rate"], 1.0)

    def test_a_changed_verdict_is_reported(self):
        """POSITIVE CONTROL: the instrument must be able to see drift at all."""
        with tempfile.TemporaryDirectory() as td:
            aset = load_anchor_set(self._set(td))
            result = regrade_anchor_set(aset, judge_client=lambda p: _reply("pass"))
            s = summarize(result)
            self.assertEqual(s["moved"], 2, s)          # fail->pass, flag->pass
            self.assertLess(s["agreement_rate"], 1.0)
            moved = {r["item_id"]: (r["recorded_judge_overall"], r["regrade_overall"])
                     for r in result["items"] if r["moved"]}
            self.assertEqual(moved["anchor-0001"], ("fail", "pass"))
            self.assertEqual(moved["anchor-0002"], ("flag", "pass"))

    def test_every_item_is_graded_and_carries_provenance(self):
        with tempfile.TemporaryDirectory() as td:
            aset = load_anchor_set(self._set(td))
            result = regrade_anchor_set(aset, judge_client=lambda p: _reply("pass"))
            self.assertEqual(len(result["items"]), 3)
            for row in result["items"]:
                self.assertIn("trace_id", row["provenance"])
                self.assertIn("regrade_dimensions", row)
                self.assertEqual(len(row["regrade_dimensions"]), len(_DIMS))

    def test_unparseable_reply_escalates_its_item_and_the_run_completes(self):
        with tempfile.TemporaryDirectory() as td:
            aset = load_anchor_set(self._set(td))
            calls = {"n": 0}

            def client(prompt: str) -> str:
                calls["n"] += 1
                # first item's two attempts return junk; everything after is clean
                return "not json at all" if calls["n"] <= 2 else _reply("pass")

            result = regrade_anchor_set(aset, judge_client=client)
            self.assertEqual(len(result["items"]), 3)
            self.assertIn(result["items"][0]["regrade_overall"], {"flag", "fail"})
            self.assertTrue(result["items"][0]["unparseable"])


class ComparingRounds(unittest.TestCase):
    def test_compare_reports_per_item_and_per_band_movement(self):
        with tempfile.TemporaryDirectory() as td:
            root = _write_set(Path(td), [_item(1, "broken", "fail"),
                                         _item(2, "excellent", "pass")])
            aset = load_anchor_set(root)
            seq = iter(["fail", "pass"])
            round_a = regrade_anchor_set(aset, judge_client=lambda p: _reply(next(seq)))
            round_b = regrade_anchor_set(aset, judge_client=lambda p: _reply("pass"))
            diff = compare_rounds(round_a, round_b)
            self.assertEqual(diff["items_compared"], 2)
            self.assertEqual(diff["changed"], 1)
            self.assertEqual(diff["changes"][0]["item_id"], "anchor-0001")
            self.assertEqual(diff["changes"][0]["from"], "fail")
            self.assertEqual(diff["changes"][0]["to"], "pass")
            self.assertIn("broken", diff["by_band"])

    def test_compare_refuses_mismatched_sets(self):
        with tempfile.TemporaryDirectory() as td:
            root = _write_set(Path(td), [_item(1, "broken", "fail")])
            aset = load_anchor_set(root)
            a = regrade_anchor_set(aset, judge_client=lambda p: _reply("fail"))
            b = dict(a, set_id="a-different-set")
            with self.assertRaises(AnchorSetError):
                compare_rounds(a, b)


class JudgeFamilyGuard(unittest.TestCase):
    def test_same_family_judge_is_refused(self):
        from intergen.tests.judge_anchor import build_judge_client
        with self.assertRaises(ValueError):
            build_judge_client("http://127.0.0.1:8090/v1/chat/completions",
                               model="qwen3-4b-instruct")


if __name__ == "__main__":
    unittest.main()
