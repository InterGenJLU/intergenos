# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A named forget keeps its subject, and only a reset request resets.

WHY A SECOND MODULE. ``test_zz192_p4_03.py`` pins the finding as the verifier
raised it: three sentences whose accidental substrings turned a named forget into
a full wipe, the fact a user did not name surviving, and the reply not claiming a
reset. It is the right case and it stops there. Four things this defect owns are
outside it, and one of them is a deliberate behaviour change that must be written
down where the next reader will find it rather than left to be rediscovered.

WHAT IS MEASURED HERE.

  * THE CLASS, not the three sentences. The defect was two bare substring tests,
    so the sentences that trigger it are every sentence carrying a word that
    merely CONTAINS "clear" or "all". Those words are ordinary English —
    "unclear", "nuclear", "clearance", "cleartext", "wallpaper", "install",
    "fallback", "allocation", "actually", "really" — and the carriers measured
    here were taken from the project's own text rather than invented.

  * THE COMPOUND SENTENCE, which this correction DELIBERATELY CHANGES. A sentence
    that names something to forget and also asks for a reset used to wipe the
    store. It no longer does: the named subject stands, matches nothing, and the
    user is told so. That is this finding's own rule applied — a forget that names
    a subject is not a wipe — and not collateral. It is pinned here so that a
    later reader who finds it surprising reads the reason before changing it back.

  * WHAT THE USER IS TOLD, on the path a named forget now takes when its subject
    matches nothing. "I don't have any memories about X" is a true sentence; the
    reply this replaces was "Done. I've cleared all N memories.", which was false
    about the user's own data in the one direction that cannot be undone.

  * THAT NO SUBSTRING TEST IS LEFT on the destructive path, read off the module's
    syntax tree. This is the shape of the defect rather than one of its sentences,
    and it is the part a later edit is most likely to reintroduce.

  * AND THE SHIPPED CORPUS AS A GUARD: no user turn in any training bank shipped
    in this tree may reach the full-reset path. That leg is green at the base too
    and is named below as the no-regression leg it is, not counted as a red.

WHERE THE CONTROLS ARE. Every genuine reset phrasing the parser's own reset
pattern can express is asserted to still reset, including the polite forms that
reach it past a leading "please" or "can you". If those fail, the assertions above
are measuring a parser that has stopped recognising resets at all rather than one
that has stopped over-reaching.
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from intergen import memory as memory_module
from intergen.memory import MemoryManager

ALL = "__ALL__"

# Words that merely CONTAIN one of the two needles the defect searched for. Every
# one of these was found in the project's own text by the corpus builder sealed
# with this item; none is invented for the occasion.
_CLEAR_CARRIERS = ("unclear", "nuclear", "clearance", "cleared", "clearer",
                   "clearest", "clearing", "clearly", "clears", "cleartext")
_ALL_CARRIERS = ("wallpaper", "install", "fallback", "allocation", "actually",
                 "additionally", "small", "really", "allocate", "abnormally")


def _fresh_store() -> MemoryManager:
    """A MemoryManager on its own throwaway store, with nothing else in it.

    The same shape the verifier's own case uses, so both modules measure a store
    that cannot be this machine's.
    """
    tmp = tempfile.mkdtemp(prefix="named-forget-")
    os.environ["XDG_DATA_HOME"] = os.path.join(tmp, "data")
    os.environ["XDG_STATE_HOME"] = os.path.join(tmp, "state")
    os.makedirs(os.environ["XDG_DATA_HOME"], exist_ok=True)
    os.makedirs(os.environ["XDG_STATE_HOME"], exist_ok=True)
    return MemoryManager()


def _rows(mm: MemoryManager) -> list[tuple[str, str, int]]:
    """Every row in the store, asked of the database rather than of the API."""
    conn = sqlite3.connect(str(mm._db_path))
    try:
        return [(k, v, d) for k, v, d in
                conn.execute("SELECT key, value, deleted FROM facts")]
    finally:
        conn.close()


def _two_facts() -> MemoryManager:
    mm = _fresh_store()
    mm.extract_and_store("remember that my editor is vim")
    mm.extract_and_store("remember that my wallpaper is dark")
    assert mm.count == 2, (
        "control: the two facts were not stored, so anything below would "
        "measure a forget with nothing to lose (rows: %r)" % (_rows(mm),))
    return mm


# ── the class, not the three sentences ──────────────────────────────────────

class AWordIsNotASubstringTests(unittest.TestCase):
    """Ten carriers of each needle, in the two sentence forms the finding names.

    WHICH OF THESE ARE REDS AND WHICH ARE GUARDS, measured rather than assumed.
    The defect needed a carrier of BOTH needles in one sentence: the old test was
    "clear" in the message AND ("all" or "memories") in it. So the two
    one-needle-at-a-time cases below are GREEN AT THE BASE — "forget my unclear
    setting" carries no "all" and no "memories", so the base left its subject
    alone. They are guards against a future half-fix that re-reads one needle, not
    reds, and they are not counted as reds anywhere in this delivery. The two
    cases that DO bite at the base are the paired one and the literal-words one
    beneath them.
    """

    def test_a_named_forget_survives_every_measured_clear_carrier(self):
        """A GUARD, green at the base: one needle alone never triggered it."""
        for word in _CLEAR_CARRIERS:
            for fmt in ("forget my %s setting",
                        "delete the memory about my %s setting"):
                sentence = fmt % word
                with self.subTest(sentence=sentence):
                    got = MemoryManager.is_forget_request(sentence)
                    self.assertNotEqual(
                        got, ALL,
                        "the word %r merely CONTAINS 'clear'; this sentence "
                        "names one thing and asked for no reset" % word)
                    self.assertIn(word, got or "",
                                  "the subject the pattern captured was lost")

    def test_a_named_forget_survives_every_measured_all_carrier(self):
        """A GUARD, green at the base, for the same reason as the one above."""
        for word in _ALL_CARRIERS:
            for fmt in ("forget my %s setting",
                        "delete the memory about my %s setting"):
                sentence = fmt % word
                with self.subTest(sentence=sentence):
                    got = MemoryManager.is_forget_request(sentence)
                    self.assertNotEqual(got, ALL)
                    self.assertIn(word, got or "")

    def test_both_needles_in_one_sentence_still_name_their_subject(self):
        """A RED AT THE BASE, over all ten pairs. This is the shape the defect
        needed and the shape the verifier's own three sentences have: one carrier
        of each needle in the same sentence. At the base every one of these ten
        deleted the user's entire memory."""
        for a, b in zip(_CLEAR_CARRIERS, _ALL_CARRIERS):
            sentence = "forget my %s %s" % (a, b)
            with self.subTest(sentence=sentence):
                self.assertEqual(MemoryManager.is_forget_request(sentence),
                                 "my %s %s" % (a, b))

    def test_the_literal_word_clear_in_a_named_subject_is_not_a_reset(self):
        """The hardest case of the class: the needle IS a word here, and the
        sentence still names a subject. A word-boundary version of the old test
        would have passed every case above and failed this one, which is why the
        test was removed rather than tightened."""
        self.assertEqual(
            MemoryManager.is_forget_request("forget my clear plan for all days"),
            "my clear plan for all days",
            "a named forget was turned into a wipe by two real words standing "
            "elsewhere in the sentence")


# ── the compound sentence: a deliberate change, pinned with its reason ───────

class ACompoundSentenceFailsClosedTests(unittest.TestCase):
    """READ THIS BEFORE CHANGING IT BACK.

    A sentence that names something to forget AND asks for a reset in the same
    breath used to wipe the store. It no longer does. This is DELIBERATE and it is
    this finding's own rule applied: a forget that names a subject is not executed
    as a full wipe. The two cannot both hold — keeping these as wipes requires a
    whole-sentence word hunt that overrides a captured subject, which is the
    defect itself.

    What the user gets instead: nothing is deleted, and the reply says that
    nothing was forgotten or cleared, so the reset can be asked for on its own
    line and works. (The reply once named the whole sentence as a subject that
    matched nothing; a request carrying the reset phrase is now declined before
    any subject is looked for, and the reply says what was not done.)
    Measured against the alternative: the old behaviour deleted every stored fact
    on a sentence whose subject happened to contain "unclear". Failing closed on
    an ambiguous destructive request costs one turn; failing open costs the user
    everything they ever told the assistant, unrecoverably.
    """

    COMPOUND = (
        "forget everything, clear all my memories",
        "forget my editor and clear all my memories",
        "delete the memory about my editor, then clear all my facts",
    )

    def test_a_compound_sentence_is_not_a_wipe(self):
        for sentence in self.COMPOUND:
            with self.subTest(sentence=sentence):
                self.assertNotEqual(
                    MemoryManager.is_forget_request(sentence), ALL,
                    "a sentence that NAMES a subject was executed as a full "
                    "wipe; that is the finding")

    def test_a_compound_sentence_deletes_nothing_and_says_so(self):
        for sentence in self.COMPOUND:
            with self.subTest(sentence=sentence):
                mm = _two_facts()
                reply = mm.format_forget_response(
                    MemoryManager.is_forget_request(sentence))
                self.assertEqual(mm.count, 2, (
                    "the store was changed by a sentence this parser could not "
                    "resolve to one subject (rows: %r)" % (_rows(mm),)))
                self.assertNotIn("cleared all", reply)
                self.assertIn("haven't forgotten or cleared anything", reply)

    def test_the_reset_asked_for_on_its_own_still_works(self):
        """What makes failing closed acceptable: the user's next sentence works."""
        mm = _two_facts()
        mm.format_forget_response(MemoryManager.is_forget_request(
            "forget my editor and clear all my memories"))
        self.assertEqual(mm.count, 2)
        reply = mm.format_forget_response(
            MemoryManager.is_forget_request("clear all my memories"))
        self.assertIn("cleared", reply)
        self.assertEqual(mm.count, 0, _rows(mm))


# ── what the user is told ───────────────────────────────────────────────────

class TheReplyIsTrueAboutTheUsersOwnDataTests(unittest.TestCase):

    def test_a_named_forget_that_matched_nothing_says_exactly_that(self):
        mm = _two_facts()
        reply = mm.format_forget_response(
            MemoryManager.is_forget_request("forget my unclear wallpaper"))
        self.assertIn("my unclear wallpaper", reply,
                      "the reply does not name what was looked for")
        self.assertNotIn("cleared all", reply)
        self.assertEqual([r for r in _rows(mm) if r[2]], [],
                         "a row was marked deleted by a forget that matched "
                         "nothing")

    def test_every_stored_fact_is_still_readable_through_the_api(self):
        mm = _two_facts()
        mm.format_forget_response(
            MemoryManager.is_forget_request("forget my nuclear fallback plan"))
        self.assertEqual(mm.get("editor"), "vim")
        self.assertEqual(mm.count, 2, _rows(mm))


# ── the controls: a reset request still resets ──────────────────────────────

class ControlEveryResetPhrasingStillResetsTests(unittest.TestCase):
    """If any of these fails, every assertion above is measuring a parser that
    has stopped recognising resets rather than one that has stopped over-reaching."""

    RESETS = ("clear all my memories", "clear my memories", "clear all my facts",
              "clear my facts", "clear memories", "clear facts",
              "clear all memories", "clear all facts",
              "please clear all my memories", "can you clear all my memories")

    def test_every_reset_phrasing_returns_the_reset_sentinel(self):
        for sentence in self.RESETS:
            with self.subTest(sentence=sentence):
                self.assertEqual(MemoryManager.is_forget_request(sentence), ALL)

    def test_a_reset_actually_empties_the_store(self):
        for sentence in self.RESETS:
            with self.subTest(sentence=sentence):
                mm = _two_facts()
                reply = mm.format_forget_response(
                    MemoryManager.is_forget_request(sentence))
                self.assertIn("cleared", reply)
                self.assertEqual(mm.count, 0, _rows(mm))

    def test_an_ordinary_named_forget_still_names_its_subject(self):
        self.assertEqual(
            MemoryManager.is_forget_request("forget about my backup drive"),
            "my backup drive")

    def test_a_negated_forget_is_still_not_a_forget(self):
        """The guard standing in front of these patterns is untouched by this
        change and is asserted here so a later edit cannot quietly drop it."""
        self.assertIsNone(
            MemoryManager.is_forget_request("don't forget my keyboard layout"))


# ── the shape of the defect, not its sentences ──────────────────────────────

class NoSubstringTestRemainsOnTheDestructivePathTests(unittest.TestCase):
    """The part a later edit is most likely to reintroduce.

    Read off the module's syntax tree rather than by searching its text, so the
    instrument cannot match its own caller: the strings it looks for appear in
    this file too.
    """

    def setUp(self):
        self.src = inspect.getsource(memory_module)
        self.tree = ast.parse(self.src)
        self.parser = None
        for fn in ast.walk(self.tree):
            if (isinstance(fn, ast.FunctionDef)
                    and fn.name == "is_forget_request"):
                self.parser = fn
        self.assertIsNotNone(self.parser, "memory.py has no is_forget_request")

    def test_the_parser_contains_no_bare_substring_test(self):
        """A Compare node whose operator is In and whose left side is a string
        literal is a substring test. There must be none in this function."""
        found = []
        for node in ast.walk(self.parser):
            if not isinstance(node, ast.Compare):
                continue
            for op, comparator in zip(node.ops, node.comparators):
                if isinstance(op, ast.In) and isinstance(node.left, ast.Constant) \
                        and isinstance(node.left.value, str):
                    found.append((node.left.value,
                                  ast.unparse(comparator)))
        self.assertEqual(
            found, [],
            "a substring test is back on the destructive path: %r. A word is "
            "not a substring, and this is the exact shape that made a named "
            "forget delete every stored fact." % (found,))

    def test_the_reset_sentinel_is_returned_from_exactly_one_place(self):
        """One decision, not two that can disagree. The defect was a second
        decision disagreeing with the first by deleting everything."""
        returns = [n for n in ast.walk(self.parser)
                   if isinstance(n, ast.Return)
                   and ALL in ast.unparse(n)]
        self.assertEqual(
            len(returns), 1,
            "the full-reset sentinel is returned from %d places in the parser: "
            "%r" % (len(returns), [ast.unparse(n) for n in returns]))

    def test_that_one_place_is_the_pattern_that_captured_nothing(self):
        """And the one decision is structural: it stands in the branch of the
        pattern that captures nothing, the reset, and nowhere else.

        No capture is not, on its own, the whole request. This case once read
        "no capture means no subject was named, so the whole sentence is the
        request" off the parser, and "clear all my facts except my car" cleared
        the car it asked to keep. The branch now reads the rest of the request
        before it answers with the sentinel, and the sentences below hold it to
        that."""
        branches = [n for n in ast.walk(self.parser)
                    if isinstance(n, ast.If)
                    and ast.unparse(n.test) == "pattern is _RESET_RE"]
        self.assertEqual(len(branches), 1,
                         "the parser has no single branch for the reset pattern")
        inside = [n for n in ast.walk(branches[0])
                  if isinstance(n, ast.Return) and ALL in ast.unparse(n)]
        self.assertEqual(len(inside), 1,
                         "the full-reset sentinel is returned outside the branch "
                         "of the pattern that captures nothing")
        for sentence in ("clear all my facts except my car",
                         "clear all my memories and start over"):
            with self.subTest(sentence=sentence):
                self.assertNotEqual(
                    MemoryManager.is_forget_request(sentence), ALL,
                    "the reset phrase was taken for the whole request it "
                    "stands in")

    def test_the_dedicated_reset_pattern_is_still_the_last_pattern(self):
        """It must be tried last, so a named-subject pattern is never shadowed by
        it, and it must capture nothing, or it would return a subject."""
        import re as _re
        patterns = memory_module._FORGET_PATTERNS
        self.assertTrue(patterns, "there are no forget patterns")
        last = patterns[-1]
        self.assertEqual(last.groups, 0,
                         "the last pattern captures a group, so it can no "
                         "longer stand for a reset: %r" % last.pattern)
        self.assertIsNotNone(last.search("clear all my memories"))
        for p in patterns[:-1]:
            self.assertGreater(
                p.groups, 0,
                "a pattern before the last captures nothing, so it would "
                "return the reset sentinel for whatever it matches: %r"
                % p.pattern)
        self.assertIsInstance(last, _re.Pattern)


# ── the shipped corpus as a guard ───────────────────────────────────────────

class NoShippedUserTurnReachesTheResetPathTests(unittest.TestCase):
    """THE NO-REGRESSION LEG, named as one: this is green at the base too.

    It cannot be a red, because at the base no shipped turn reached the reset
    path either — the defect needed a forget verb, and these turns are ordinary
    conversation. It is here as a guard: the banks are the sentences the project
    itself says a user says, and if a future edit to this parser makes any of them
    delete the user's whole memory, this is where it is caught.
    """

    def _bank_turns(self):
        d = Path(memory_module.__file__).parent / "tests" / "training_bank"
        if not d.is_dir():
            self.skipTest("no training bank in this tree")
        turns = []
        for path in sorted(d.glob("*.jsonl")):
            with path.open() as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    for turn in rec.get("turns", []) or []:
                        user = (turn.get("user") or "").strip()
                        if user:
                            turns.append((path.name, user))
        return turns

    def test_not_one_shipped_user_turn_wipes_the_store(self):
        turns = self._bank_turns()
        self.assertGreater(len(turns), 500,
                           "the bank read back far fewer turns than it holds, "
                           "so this guard is measuring almost nothing")
        offenders = [(name, t) for name, t in turns
                     if MemoryManager.is_forget_request(t) == ALL]
        self.assertEqual(
            offenders, [],
            "a sentence the project ships as an ordinary user turn now deletes "
            "every stored fact: %r" % (offenders[:5],))


if __name__ == "__main__":
    unittest.main()
