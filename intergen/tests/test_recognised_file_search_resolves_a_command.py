# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A file-search clause the assistant recognises also resolves to a command.

THE DEFECT, and what it costs the person using the machine. The run_command
keyword pattern claims any clause of the form "find the <largest|biggest|big|
hidden> …" — that is what makes the assistant answer it as a command rather
than as conversation. The selector that turns the clause into an actual command
only knows the disk-usage phrases, so two of the pattern's four arms resolve
nothing: the clause is recognised, claimed, and then quietly dropped to the
freeform path. The person asked a plain question the assistant advertises it
can answer and got the slow, uncertain answer instead of the fast, certain one.

MEASURED ON THE UNMODIFIED TREE, before a line was written (sealed in the
lane's evidence, step 0). Sixteen simple forms — four lead-ins over each of the
pattern's four arms — walked against the selector:

    arm       simple forms claimed    resolved to a command
    largest   4                       4
    biggest   4                       4
    big       4                       0   <-- claimed, then dropped
    hidden    4                       0   <-- claimed, then dropped

The cut named the hidden arm. The "big" arm was found by walking the
alternation and is the same defect with a different word: the selector's map
carries "largest files" and "biggest files" and nothing for "big files".

WHAT THIS FILE PINS.

1. EVERY ARM OF THE ALTERNATION RESOLVES. The first class below reads the
   pattern itself — not a copy of it — and walks its arms. Adding a fifth word
   to the pattern without giving the selector an answer for it fails this test,
   so recognised-then-dropped cannot recur for this tool. The pattern therefore
   has to be a shared module constant rather than a literal buried in the
   registration function, exactly as BOOT_PERF_COMPLAINT_PATTERN already is for
   the boot-performance gate, and for the same stated reason: the gate and the
   selector must not be able to drift apart.

2. THE HIDDEN CASE RESOLVES TO A BOUNDED, READ-ONLY COMMAND over the directory
   the person named, or their home directory when they named none. It is
   depth-limited, so it answers "what is hidden HERE" rather than walking an
   entire filesystem.

3. A DIRECTORY THIS CODE CANNOT READ AS A PATH IS NOT GUESSED AT. "the
   downloads folder" is a human name for a place, and turning it into a path is
   a judgement, not a lookup — so the selector returns nothing and the clause
   goes to the rung that can make that judgement. Answering about the home
   directory instead would answer a question nobody asked.

Nothing here runs a command, touches a model server, or reads any file outside
the source tree.
"""

from __future__ import annotations

import re
import unittest

from intergen import intents, safety
from intergen.router import ConversationRouter as R


# The four lead-ins the pattern's own optional group allows.
LEAD_INS = ("find the {} files", "find all {} files",
            "find my {} files", "find {} files")


def alternation_arms(pattern: str) -> list[str]:
    """The words inside the pattern's ``(?:a|b|c)`` alternation.

    Read from the pattern itself so this file cannot describe an alternation
    the product no longer has.
    """
    m = re.search(r"\(\?:([a-z]+(?:\|[a-z]+)+)\)\\b", pattern)
    assert m, f"the file-search pattern has no word alternation: {pattern!r}"
    return m.group(1).split("|")


class ThePatternIsSharedNotCopied(unittest.TestCase):
    """RED at base: the pattern is a literal inside _register_run_command, so
    nothing outside that function can walk it."""

    def test_the_pattern_is_a_module_constant(self) -> None:
        self.assertTrue(
            hasattr(intents, "FILE_SEARCH_PATTERN"),
            "the file-search pattern must be a shared module constant, so the "
            "gate that claims a clause and the selector that resolves it "
            "cannot drift apart — the same reason "
            "BOOT_PERF_COMPLAINT_PATTERN is one")

    def test_the_registration_uses_that_constant(self) -> None:
        """A constant nothing uses would drift the moment somebody edited the
        literal instead."""
        from pathlib import Path
        src = (Path(intents.__file__)).read_text(encoding="utf-8")
        body = src.split("def _register_run_command", 1)
        self.assertEqual(len(body), 2, "_register_run_command not found")
        registration = body[1].split("\ndef ", 1)[0]
        self.assertIn("FILE_SEARCH_PATTERN", registration,
                      "the registration must use the shared constant, not a "
                      "second copy of the same expression")


class EveryArmOfThePatternResolvesToACommand(unittest.TestCase):
    """RED at base: the 'big' and 'hidden' arms resolve to None.

    This is the anti-recurrence case. It does not name the arms — it reads them
    out of the pattern — so a new arm added tomorrow is covered the day it is
    added."""

    def test_every_arm_and_lead_in_resolves(self) -> None:
        pattern = getattr(intents, "FILE_SEARCH_PATTERN", None)
        self.assertIsNotNone(
            pattern, "the pattern must be readable as intents."
                     "FILE_SEARCH_PATTERN before its arms can be walked")
        dropped = []
        for arm in alternation_arms(pattern):
            for lead_in in LEAD_INS:
                phrase = lead_in.format(arm)
                self.assertRegex(
                    phrase, pattern,
                    f"{phrase!r} must be claimed by the pattern, or this "
                    f"walk is testing the wrong forms")
                if R._natural_language_to_command(phrase) is None:
                    dropped.append(phrase)
        self.assertEqual(
            dropped, [],
            "these clauses are CLAIMED by the run_command pattern and then "
            "resolve to no command, so each is recognised and dropped to the "
            "freeform path")


class TheHiddenSearchResolvesABoundedReadOnlyFind(unittest.TestCase):
    """RED at base: every one of these returns None."""

    def test_with_no_directory_named_it_is_the_home_directory(self) -> None:
        cmd = R._natural_language_to_command("find the hidden files")
        self.assertIsNotNone(cmd)
        self.assertIn("find ", cmd)
        self.assertIn("~", cmd, "no directory named means the person's own "
                                "home directory")
        self.assertIn('-name ".*"', cmd)

    def test_it_is_depth_limited(self) -> None:
        """Walking an entire filesystem to answer 'what is hidden in my home'
        is a different, much slower question."""
        cmd = R._natural_language_to_command("find the hidden files in my home")
        self.assertIsNotNone(cmd)
        self.assertIn("-maxdepth 1", cmd)

    def test_a_named_absolute_directory_is_the_one_searched(self) -> None:
        cmd = R._natural_language_to_command("find the hidden files in /etc")
        self.assertIsNotNone(cmd)
        self.assertIn("/etc", cmd)
        self.assertNotIn("~", cmd, "the person named a directory; searching "
                                   "their home instead answers a different "
                                   "question")

    def test_a_home_relative_directory_is_kept(self) -> None:
        cmd = R._natural_language_to_command(
            "find hidden files in ~/Documents")
        self.assertIsNotNone(cmd)
        self.assertIn("~/Documents", cmd)

    def test_saying_home_in_words_is_the_home_directory(self) -> None:
        for phrase in ("find the hidden files in my home directory",
                       "find all hidden files in my home folder"):
            cmd = R._natural_language_to_command(phrase)
            self.assertIsNotNone(cmd, phrase)
            self.assertIn("~", cmd, phrase)

    def test_a_place_named_in_words_is_not_guessed_at(self) -> None:
        """"the downloads folder" is a human name for a place. Turning it into
        a path is a judgement, so the clause goes to the rung that can make
        one — answering about the home directory would answer a question
        nobody asked."""
        self.assertIsNone(R._natural_language_to_command(
            "find the hidden files in the downloads folder"))
        # The control that keeps the assertion above from passing for the
        # wrong reason: the SAME sentence shape with a real path does resolve.
        # Without this, the case above passes on a tree where nothing resolves
        # at all, which is exactly the state it is meant to detect.
        self.assertIsNotNone(R._natural_language_to_command(
            "find the hidden files in /var/log"))

    def test_a_directory_carrying_shell_characters_is_refused(self) -> None:
        """A path is built into a command string, so anything that is not a
        plain path resolves to nothing rather than to a command nobody
        inspected."""
        for phrase in ("find the hidden files in /tmp; rm -rf /",
                       "find the hidden files in $(whoami)",
                       "find the hidden files in /tmp && reboot",
                       "find the hidden files in `id`"):
            self.assertIsNone(R._natural_language_to_command(phrase), phrase)
        # Same control as above, for the same reason: a plain path in the same
        # position must resolve, or "refused" would be indistinguishable from
        # "nothing resolves here at all".
        self.assertIsNotNone(
            R._natural_language_to_command("find the hidden files in /tmp"))


class TheBigArmMeansWhatItsSynonymsMean(unittest.TestCase):
    """RED at base: 'big' resolves to None while 'largest' and 'biggest'
    resolve to the disk-usage breakdown."""

    def test_big_resolves_the_same_way_as_largest(self) -> None:
        largest = R._natural_language_to_command("find the largest files")
        big = R._natural_language_to_command("find the big files")
        self.assertIsNotNone(big)
        self.assertEqual(big, largest,
                         "the three words name the same request")

    def test_biggest_still_wins_where_both_could_match(self) -> None:
        self.assertEqual(
            R._natural_language_to_command("find the biggest files"),
            R._natural_language_to_command("find the largest files"))


class WhatIsResolvedStaysReadOnly(unittest.TestCase):
    """A selector that resolved a destructive command would run it without the
    person seeing it, because this path is the AUTO tier."""

    def test_every_resolved_file_search_command_is_auto(self) -> None:
        pattern = getattr(intents, "FILE_SEARCH_PATTERN", None)
        self.assertIsNotNone(pattern)
        checked = 0
        for arm in alternation_arms(pattern):
            for lead_in in LEAD_INS:
                cmd = R._natural_language_to_command(lead_in.format(arm))
                if cmd is None:
                    continue
                self.assertEqual(
                    safety.classify_command(cmd), safety.SafetyTier.AUTO,
                    f"{cmd!r} runs without the person confirming it")
                checked += 1
        self.assertEqual(checked, len(alternation_arms(pattern)) * len(LEAD_INS),
                         "every arm must have resolved something to classify")

    def test_the_classifier_really_would_have_objected(self) -> None:
        """The true-positive control for the case above: a destructive find is
        NOT AUTO, so the assertion there is measuring something."""
        self.assertNotEqual(
            safety.classify_command('find ~ -maxdepth 1 -name ".*" -delete'),
            safety.SafetyTier.AUTO)


if __name__ == "__main__":
    unittest.main()
