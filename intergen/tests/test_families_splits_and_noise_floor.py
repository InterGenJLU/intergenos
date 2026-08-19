# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Families, splits, and the noise floor.

Three measurement guards from the ratified method, pinned:

  * A class is not one sentence. Alternate wordings of a cell are expanded into
    sibling conversations that share its assertions, and the family passes on
    four of five — so one wording landed by keyword accident is not a pass, and
    one unlucky wording is not a regression.
  * Splits are assigned per FAMILY. Putting one wording of a request in the
    training-visible set and another in the held-out set leaks the answer.
  * A pass rate travels with the range the evidence supports, and two runs whose
    intervals overlap are not an improvement.
"""

from __future__ import annotations

import unittest

import importlib

from intergen.tests.conversations import Assertion, Conversation, Turn


def _module(name: str):
    """Import one of the new modules INSIDE a case, never at module level.

    A module-level import of a module the tree does not have yet makes the whole
    file fail to COLLECT, and "cannot import" is not a statement about behavior —
    it also takes every other file in the same pytest invocation down with it.
    Reaching the module here means each case reports its own verdict.
    """
    try:
        return importlib.import_module(f"intergen.tests.{name}")
    except ImportError as exc:
        raise AssertionError(
            f"intergen.tests.{name} does not exist in this tree: {exc}") from exc


def _need(module_or_name, name):
    module = (_module(module_or_name) if isinstance(module_or_name, str)
              else module_or_name)
    attr = getattr(module, name, None)
    if attr is None:
        raise AssertionError(f"{module.__name__}.{name} does not exist in this tree")
    return attr


class _LazyModule:
    """Stands in for a module reference at import time, resolving on first use."""

    def __init__(self, name: str) -> None:
        self.__dict__["_name"] = name

    def __getattr__(self, attr: str):
        return getattr(_module(self.__dict__["_name"]), attr)


_families = _LazyModule("families")
_measurement = _LazyModule("measurement")


def _cell(cell_id: str, phrasings=()) -> Conversation:
    Phrasing = _need(__import__("intergen.tests.conversations", fromlist=["x"]),
                     "Phrasing")
    return Conversation(
        id=cell_id, name="Install request", category="package_management",
        turns=[Turn(
            user="Install htop for me",
            assertions=[Assertion("tool_used", "manage_packages", "routes to pkm")],
            phrasings=[Phrasing(label=l, text=t) for l, t in phrasings],
        )],
    )


class ExpansionTests(unittest.TestCase):
    def test_a_cell_with_no_wordings_passes_through(self):
        expand = _need(_families, "expand_paraphrase_families")
        out = expand([_cell("pkg_install")])
        self.assertEqual([c.id for c in out], ["pkg_install"])

    def test_each_wording_becomes_a_sibling_carrying_the_same_assertions(self):
        expand = _need(_families, "expand_paraphrase_families")
        out = expand([_cell("pkg_install", [
            ("casual", "get me htop"),
            ("polite", "could you install htop please"),
            ("typo", "instal htop"),
            ("emotional", "I really need htop, can you sort it"),
        ])])
        self.assertEqual(len(out), 5, "base plus four wordings")
        self.assertEqual(out[0].id, "pkg_install")
        self.assertEqual(
            [c.id for c in out[1:]],
            ["pkg_install#casual", "pkg_install#polite",
             "pkg_install#typo", "pkg_install#emotional"])
        for member in out[1:]:
            self.assertEqual(member.paraphrase_of, "pkg_install")
            self.assertEqual(
                [(a.type, a.value) for a in member.turns[0].assertions],
                [("tool_used", "manage_packages")],
                "a wording must inherit the class invariant, not restate it")
        self.assertEqual(out[1].turns[0].user, "get me htop")

    def test_expansion_is_idempotent(self):
        expand = _need(_families, "expand_paraphrase_families")
        once = expand([_cell("pkg_install", [("casual", "get me htop")])])
        twice = expand(once)
        self.assertEqual([c.id for c in once], [c.id for c in twice])

    def test_two_wordings_sharing_a_label_are_refused(self):
        expand = _need(_families, "expand_paraphrase_families")
        with self.assertRaises(ValueError):
            expand([_cell("pkg_install", [("casual", "get me htop"),
                                          ("casual", "grab htop")])])

    def test_a_family_is_recoverable_from_a_member_id(self):
        family_id_of = _need(_families, "family_id_of")
        self.assertEqual(family_id_of("pkg_install#casual"), "pkg_install")
        self.assertEqual(family_id_of("pkg_install"), "pkg_install")


class FamilyGradingTests(unittest.TestCase):
    def _results(self, grades: dict[str, str]) -> list[dict]:
        return [{"id": cid, "grade": g,
                 "paraphrase_of": _families.family_id_of(cid) if "#" in cid else ""}
                for cid, g in grades.items()]

    def test_four_of_five_passes_the_family(self):
        grade_families = _need(_families, "grade_families")
        out = grade_families(self._results({
            "pkg_install": "PASS", "pkg_install#casual": "PASS",
            "pkg_install#polite": "PASS", "pkg_install#typo": "PASS",
            "pkg_install#emotional": "FAIL",
        }))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].grade, "PASS")
        self.assertEqual((out[0].passed, out[0].total), (4, 5))
        self.assertFalse(out[0].unanimous)

    def test_three_of_five_fails_the_family(self):
        grade_families = _need(_families, "grade_families")
        out = grade_families(self._results({
            "pkg_install": "PASS", "pkg_install#casual": "PASS",
            "pkg_install#polite": "PASS", "pkg_install#typo": "FAIL",
            "pkg_install#emotional": "FAIL",
        }))
        self.assertEqual(out[0].grade, "FAIL")

    def test_one_lucky_wording_is_not_a_family_pass(self):
        """Path luck, which is the reason families exist."""
        grade_families = _need(_families, "grade_families")
        out = grade_families(self._results({
            "pkg_install": "FAIL", "pkg_install#casual": "PASS",
            "pkg_install#polite": "FAIL", "pkg_install#typo": "FAIL",
            "pkg_install#emotional": "FAIL",
        }))
        self.assertEqual(out[0].grade, "FAIL")
        self.assertEqual(out[0].passed, 1)

    def test_a_mixed_member_does_not_count_as_a_pass(self):
        grade_families = _need(_families, "grade_families")
        out = grade_families(self._results({
            "pkg_install": "PASS", "pkg_install#casual": "MIXED",
            "pkg_install#polite": "PASS", "pkg_install#typo": "PASS",
            "pkg_install#emotional": "PASS",
        }))
        self.assertEqual(out[0].passed, 4)
        self.assertEqual(out[0].grade, "PASS")

    def test_variance_names_the_families_that_did_not_hold(self):
        grade_families = _need(_families, "grade_families")
        family_variance = _need(_families, "family_variance")
        out = grade_families(self._results({
            "a": "PASS", "a#one": "PASS",
            "b": "PASS", "b#one": "FAIL",
        }))
        varied = [r.family for r in family_variance(out)]
        self.assertEqual(varied, ["b"])

    def test_an_ungrouped_cell_is_its_own_family(self):
        grade_families = _need(_families, "grade_families")
        out = grade_families(self._results({"solo": "PASS"}))
        self.assertEqual(out[0].family, "solo")
        self.assertEqual(out[0].grade, "PASS")


class SplitTests(unittest.TestCase):
    def test_every_wording_of_a_request_lands_in_the_same_split(self):
        """The leak this prevents: the same request in training and held out."""
        split_of_conversation = _need(_families, "split_of_conversation")
        base = split_of_conversation("pkg_install")
        for label in ("casual", "polite", "typo", "emotional"):
            self.assertEqual(split_of_conversation(f"pkg_install#{label}"), base)

    def test_assignment_is_stable_across_calls(self):
        assign_splits = _need(_families, "assign_splits")
        families = [f"cell_{i}" for i in range(50)]
        self.assertEqual(assign_splits(families), assign_splits(families))

    def test_the_three_splits_land_near_their_ratios(self):
        assign_splits = _need(_families, "assign_splits")
        assigned = assign_splits([f"cell_{i}" for i in range(600)])
        counts = {name: 0 for name, _ in _families.SPLIT_RATIOS}
        for split in assigned.values():
            counts[split] += 1
        total = sum(counts.values())
        self.assertEqual(total, 600)
        for name, ratio in _families.SPLIT_RATIOS:
            share = counts[name] / total
            self.assertAlmostEqual(
                share, ratio, delta=0.05,
                msg=f"{name} took {share:.2%} of the corpus, wanted {ratio:.0%}")

    def test_a_salt_reshuffles_deliberately(self):
        assign_splits = _need(_families, "assign_splits")
        families = [f"cell_{i}" for i in range(200)]
        first = assign_splits(families)
        second = assign_splits(families, salt="round-2")
        self.assertNotEqual(first, second)

    def test_the_refresh_window_rolls_and_covers_everything(self):
        due = _need(_families, "families_due_for_refresh")
        families = [f"cell_{i}" for i in range(20)]
        seen: set[str] = set()
        for round_index in range(5):
            picked = due(families, round_index=round_index)
            self.assertEqual(len(picked), 4, "20% of 20 families")
            seen.update(picked)
        self.assertEqual(seen, set(families),
                         "five rounds of a fifth must cover the corpus once")


class NoiseFloorTests(unittest.TestCase):
    def test_a_rate_carries_the_range_the_evidence_supports(self):
        bootstrap_interval = _need(_measurement, "bootstrap_interval")
        outcomes = [True] * 70 + [False] * 30
        interval = bootstrap_interval(outcomes, iterations=500)
        self.assertAlmostEqual(interval.rate, 0.70, places=6)
        self.assertLess(interval.low, 0.70)
        self.assertGreater(interval.high, 0.70)
        self.assertEqual(interval.n, 100)

    def test_the_interval_is_reproducible(self):
        bootstrap_interval = _need(_measurement, "bootstrap_interval")
        outcomes = [True] * 45 + [False] * 15
        first = bootstrap_interval(outcomes, iterations=400)
        second = bootstrap_interval(outcomes, iterations=400)
        self.assertEqual((first.low, first.high), (second.low, second.high))

    def test_a_small_gain_at_this_corpus_size_claims_nothing(self):
        """The stated floor: a few points at ~140 units is variance."""
        compare_runs = _need(_measurement, "compare_runs")
        before = [True] * 98 + [False] * 42        # 70.0%
        after = [True] * 103 + [False] * 37        # 73.6%
        comparison = compare_runs(before, after, iterations=800)
        self.assertFalse(comparison.separated)
        self.assertIn("noise floor", comparison.verdict())

    def test_a_large_gain_is_allowed_to_be_called_one(self):
        compare_runs = _need(_measurement, "compare_runs")
        before = [True] * 60 + [False] * 80        # 42.9%
        after = [True] * 120 + [False] * 20        # 85.7%
        comparison = compare_runs(before, after, iterations=800)
        self.assertTrue(comparison.separated)
        self.assertEqual(comparison.verdict(), "improved")

    def test_a_large_loss_is_called_a_regression(self):
        compare_runs = _need(_measurement, "compare_runs")
        comparison = compare_runs([True] * 130 + [False] * 10,
                                  [True] * 40 + [False] * 100, iterations=800)
        self.assertTrue(comparison.separated)
        self.assertEqual(comparison.verdict(), "regressed")

    def test_nothing_measured_says_so(self):
        bootstrap_interval = _need(_measurement, "bootstrap_interval")
        interval = bootstrap_interval([], iterations=100)
        self.assertEqual((interval.rate, interval.low, interval.high, interval.n),
                         (0.0, 0.0, 1.0, 0))

    def test_the_summary_line_states_the_interval(self):
        summarize_rate = _need(_measurement, "summarize_rate")
        line = summarize_rate([True] * 70 + [False] * 30, unit="family",
                              iterations=300)
        self.assertIn("70.0%", line)
        self.assertIn("confidence", line)
        self.assertIn("n=100", line)


if __name__ == "__main__":
    unittest.main()
