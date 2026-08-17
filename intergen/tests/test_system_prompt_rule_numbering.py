# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Every composed system prompt numbers its rules once, in order, with no gaps.

The defect this pins: the base prompt (llm._BASE_PROMPT) grew a fourth numbered
rule (the fencing convention) while every per-path modifier still hardcoded its
own text as "4.". Each assembled prompt therefore served TWO rules numbered 4 —
and on the conversational path, rules "4, 4, 5". Nothing in the code or the
tests noticed, because the collision is only visible in the assembled string.
A model reading "4." twice has no way to tell which rule 4 a later reference
means, and the numbering is the only structure the rule list has.

The rule numbers are now DERIVED at import from the base prompt's own rule
count (llm._MODIFIER_RULES holds bodies with no numbers of their own), so a
future base rule cannot silently collide again. These tests are the gate on
that: they read the ASSEMBLED text — the same bytes the model receives — rather
than the table the text is built from, so they fail whether the collision comes
back through the table, the base prompt, or the composition.

Daemon-free and execution-byte-identical: prompt-assembly surface only.
"""

from __future__ import annotations

import re
import unittest

from intergen import llm
from intergen.llm import LLMRouter, build_system_prompt

# A numbered rule is a line that BEGINS with "<digits>. " — the shape the rule
# lists use. Anchored per-line so prose that happens to contain "4." mid-sentence
# is not counted.
_RULE_LINE = re.compile(r"^(\d+)\.\s", re.MULTILINE)

# Every path the composer can be asked for, INCLUDING one that is not in the
# modifier table: an unlisted query_type falls back to the general modifier, and
# that fallback path assembles a prompt too.
_QUERY_TYPES = sorted(llm._MODIFIERS) + ["a_path_that_is_not_in_the_table"]

# Both tool states for every path — with_tools=False is a different assembly
# (it swaps in the toolless override for diagnostic and drops the provenance
# directive), so it is a distinct variant, not a formatting detail.
_VARIANTS = [(qt, wt) for qt in _QUERY_TYPES for wt in (True, False)]


def _rule_numbers(prompt: str) -> list[int]:
    return [int(n) for n in _RULE_LINE.findall(prompt)]


class SystemPromptRuleNumberingTests(unittest.TestCase):
    """No composed variant may repeat, skip, or reorder a rule number."""

    def test_every_variant_has_no_duplicate_rule_numbers(self):
        for query_type, with_tools in _VARIANTS:
            with self.subTest(query_type=query_type, with_tools=with_tools):
                numbers = _rule_numbers(
                    build_system_prompt(query_type, with_tools=with_tools))
                duplicates = sorted(
                    {n for n in numbers if numbers.count(n) > 1})
                self.assertEqual(
                    [], duplicates,
                    f"{query_type}/with_tools={with_tools} serves rule "
                    f"number(s) {duplicates} more than once: {numbers}")

    def test_every_variant_numbers_rules_1_to_n_in_order(self):
        for query_type, with_tools in _VARIANTS:
            with self.subTest(query_type=query_type, with_tools=with_tools):
                numbers = _rule_numbers(
                    build_system_prompt(query_type, with_tools=with_tools))
                self.assertEqual(
                    list(range(1, len(numbers) + 1)), numbers,
                    f"{query_type}/with_tools={with_tools} does not number its "
                    f"rules 1..N in order: {numbers}")

    def test_every_variant_carries_at_least_one_modifier_rule(self):
        """A prompt with only the base rules would pass numbering vacuously."""
        base_count = len(_rule_numbers(llm._BASE_PROMPT))
        self.assertGreater(base_count, 0, "the base prompt states no rules")
        for query_type, with_tools in _VARIANTS:
            with self.subTest(query_type=query_type, with_tools=with_tools):
                numbers = _rule_numbers(
                    build_system_prompt(query_type, with_tools=with_tools))
                self.assertGreater(
                    len(numbers), base_count,
                    f"{query_type}/with_tools={with_tools} adds no rule of its "
                    f"own — the modifier is missing from the assembled prompt")

    def test_modifier_rules_start_after_the_last_base_rule(self):
        """The derivation itself, not just its current output."""
        base_numbers = _rule_numbers(llm._BASE_PROMPT)
        first_modifier_number = max(base_numbers) + 1
        for name, modifier in list(llm._MODIFIERS.items()) + list(
                llm._TOOLLESS_MODIFIER_OVERRIDES.items()):
            with self.subTest(modifier=name):
                numbers = _rule_numbers(modifier)
                self.assertEqual(
                    first_modifier_number, numbers[0],
                    f"modifier {name!r} opens at rule {numbers[0]} but the base "
                    f"prompt's last rule is {max(base_numbers)}")

    def test_tool_result_synthesis_rules_are_numbered_once_each(self):
        """The other numbered rule list served to the model."""
        numbers = _rule_numbers(LLMRouter._SYNTHESIS_PROMPT)
        self.assertEqual(list(range(1, len(numbers) + 1)), numbers,
                         f"synthesis rules are misnumbered: {numbers}")


if __name__ == "__main__":
    unittest.main()
