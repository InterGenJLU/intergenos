# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A clause governed by a negation never yields an affirmative sub-query.

The defect (tracker gating row 22, seen on two installed machines): the
sentence

    Calculate 17 times 23 mentally. Reply with the number only. Do not use
    tools, run commands, access files, or contact external services.

was split by ``split_compound`` on the comma before "run commands" — a comma
followed by an action verb is a multi-action signal — so the tail
"run commands, access files, or contact external services" became a
sub-query of its own, routed as an affirmative request to run a command, and
the consent dialog opened for a sentence whose whole point was to forbid
exactly that.

The rule pinned here: the scope of "do not" / "don't" / "never" runs to the
end of its sentence, and "without" governs the phrase up to the next comma
or conjunction; a split point inside such a scope is not a split. The
COMPLETE original sentence is the first case, then the negation first, last,
mixed with a real compound, and the affirmative that legitimately follows a
sentence boundary. Detection, splitting and counting are tier-free (see
``test_decomposer_do_for_me_verbs``), so ``analyze_query`` is asserted under
every tier the same way.
"""

from __future__ import annotations

import re
import unittest

from intergen.decomposer import analyze_query, split_compound
from intergen.interfaces.types import HardwareTierLevel

TIERS = (HardwareTierLevel.TIER_1, HardwareTierLevel.TIER_2,
         HardwareTierLevel.TIER_3)

ROW22 = ("Calculate 17 times 23 mentally. Reply with the number only. Do not "
         "use tools, run commands, access files, or contact external "
         "services.")

# (sentence, the sub-queries the splitter must return)
CASES = [
    ("row-22 verbatim", ROW22, [ROW22]),
    ("negation first",
     "Do not use tools, run commands, or access files.",
     ["Do not use tools, run commands, or access files."]),
    ("negation last",
     "Tell me the time. Do not run commands, install packages, or open files.",
     ["Tell me the time. Do not run commands, install packages, or open "
      "files."]),
    ("negation inside a real compound",
     "Check my disk usage and then list my services, but do not delete "
     "anything, run commands, or install packages.",
     ["Check my disk usage",
      "list my services, but do not delete anything, run commands, or "
      "install packages"]),
    ("don't, then an affirmative after the sentence ends",
     "Don't restart the network, install packages, or delete files. Then "
     "tell me the hostname.",
     ["Don't restart the network, install packages, or delete files.",
      "tell me the hostname"]),
    ("never",
     "Never run commands, install packages, or delete files without asking "
     "me first.",
     ["Never run commands, install packages, or delete files without asking "
      "me first."]),
    ("without governs only its phrase",
     "Install vim without asking, and then open it.",
     ["Install vim without asking", "open it"]),
]

_NEG = re.compile(r"\b(?:do\s+not|don'?t|never|without)\b", re.IGNORECASE)


class NegationScopeIsPreserved(unittest.TestCase):

    def test_each_case_splits_as_a_person_would_read_it(self):
        for name, sentence, expected in CASES:
            with self.subTest(case=name):
                got = split_compound(sentence)
                self.assertEqual(
                    [g.rstrip(".") for g in got],
                    [e.rstrip(".") for e in expected], sentence)

    def test_no_sub_query_is_an_affirmative_cut_from_a_negated_list(self):
        # The property the defect violated, stated generally: a sub-query may
        # begin at a position in the original sentence only if no negation
        # governor precedes that position in the same sentence.
        for name, sentence, _expected in CASES:
            with self.subTest(case=name):
                cursor = 0
                for part in split_compound(sentence):
                    start = sentence.find(part, cursor)
                    self.assertGreaterEqual(start, 0, (part, sentence))
                    sentence_start = max(
                        (m.end() for m in re.finditer(r"[.;!?]\s+",
                                                      sentence[:start])),
                        default=0)
                    preceding = sentence[sentence_start:start]
                    governors = [m.group(0) for m in _NEG.finditer(preceding)
                                 if not m.group(0).lower().startswith("without")]
                    self.assertEqual(
                        governors, [],
                        f"{part!r} was cut out from under {governors} in "
                        f"{sentence!r}")
                    cursor = start + len(part)

    def test_the_row_22_sentence_is_not_decomposed_on_any_tier(self):
        for tier in TIERS:
            with self.subTest(tier=tier.value):
                result = analyze_query(ROW22, tier)
                self.assertFalse(result.needs_decomposition, result)
                self.assertEqual(result.sub_queries, [])

    def test_the_mixed_case_still_decomposes_into_exactly_two(self):
        sentence = CASES[3][1]
        for tier in TIERS:
            with self.subTest(tier=tier.value):
                result = analyze_query(sentence, tier)
                self.assertTrue(result.needs_decomposition, result)
                self.assertEqual(len(result.sub_queries), 2, result.sub_queries)
                self.assertTrue(
                    result.sub_queries[1].lower().startswith("list my services"),
                    result.sub_queries)

    def test_real_compounds_still_split(self):
        # The widening must not narrow what a genuine multi-action request is.
        self.assertEqual(split_compound("find a pdf editor and install it"),
                         ["find a pdf editor", "install it"])
        self.assertEqual(
            split_compound("search for a file manager app, install it, and "
                           "open it"),
            ["search for a file manager app", "install it", "open it"])
        self.assertEqual(
            split_compound("check if docker is installed and if not, install it"),
            ["check if docker is installed", "install it"])


if __name__ == "__main__":
    unittest.main()
