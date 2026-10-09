# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The forget parser decides from the whole request.

THE DEFECTS, read on the tree in intergen/memory.py, ``is_forget_request``. The
forget patterns are searched anywhere in the sentence, and the first one that
matches decides from the part of the sentence it matched, never from the rest:

1. "clear all my facts about my car". The reset pattern matched "clear all my
   facts" and the words after its noun were never read, so a request that named
   one subject cleared every stored fact, and the reply told the person it had
   cleared all of their memories.
2. "forget my car." The forget pattern captured "my car." with its full stop,
   and "forget my car, please" captured "my car, please". No stored fact carries
   either as words, so nothing was forgotten and the reply said there was no
   such memory.
3. "I don't remember my password hint". The pattern written for the request
   "don't remember X" matched inside a sentence about the person not
   remembering, and the hint was deleted.
4. "clear all my facts except my car" and "clear all my memories and start
   over". The reset phrase was taken for the whole request it stood in, and
   every stored fact was cleared, the car the first one asked to keep with them.
5. "I never want you to clear all my facts". The negation guard read only the
   words just in front of the verb, so a negation a few words further back was
   not seen and every stored fact was cleared.
6. "forget my editor and clear all my memories", "undelete the memory about my
   car and clear all my facts". A request carrying the reset phrase was carried
   out in part: the second cleared every stored fact.
7. "forget __ALL__". The captured subject was the reset's own sentinel, and every
   stored fact was cleared. "forget that", "forget it", "forget my": a subject
   that names nothing took every fact holding the word.

THE RULE these cells hold the parser to: a destructive path decides from the
whole request or not at all. Every stored fact goes only when the request is a
lead such as "please" or "can you", the reset phrase, and after its noun nothing
but a closing courtesy. A reset phrase qualified by one subject ("about my car")
is a forget of that subject. A negation anywhere in front of the verb makes it
no forget. A subject ends where the request's closing marks and courtesy begin,
and a subject that names nothing is no subject. "don't remember X" is a forget
request only as an imperative. A request that carries the reset phrase and is
more than the reset, or whose subject names nothing, is declined: nothing is
forgotten, and the reply says so.

THE CONTROLS beside the cells are green at both ends: the bare resets still
reset, the named forgets still name their subject, a subject carrying one
content word still names it, the imperative "don't remember X" still forgets X,
and a negated forget is still no forget.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest

from intergen.interfaces.types import HardwareTierLevel
from intergen.memory import MemoryManager
from intergen.router import ConversationRouter

ALL = "__ALL__"
DECLINED = "__DECLINED__"
NOTHING_DONE = "I haven't forgotten or cleared anything:"


def _fresh_store() -> MemoryManager:
    """A MemoryManager on its own throwaway store, with nothing else in it."""
    tmp = tempfile.mkdtemp(prefix="forget-whole-request-")
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


def _live_keys(mm: MemoryManager) -> list[str]:
    return sorted(k for k, _v, deleted in _rows(mm) if not deleted)


def _store(*facts: tuple[str, str]) -> MemoryManager:
    mm = _fresh_store()
    for key, value in facts:
        mm.store(key, value)
    assert mm.count == len(facts), (
        "control: the facts were not all stored, so anything below would "
        "measure a forget against the wrong store (rows: %r)" % (_rows(mm),))
    return mm


def _four_facts() -> MemoryManager:
    """Facts whose values hold the words a subject that names nothing took."""
    return _store(("car", "the one that broke down"), ("phone", "my old pixel"),
                  ("bike", "your blue one"), ("editor", "vim, please"))


def _router(mm: MemoryManager) -> ConversationRouter:
    """The real router's memory turn, with only what that path reads set."""
    r = ConversationRouter.__new__(ConversationRouter)
    r._memory = mm
    r._hardware_tier = HardwareTierLevel.TIER_2
    r._lock_dispatch = False
    return r


# ── the cells: red before the correction ────────────────────────────────────

class AResetPhraseAboutASubjectForgetsThatSubject(unittest.TestCase):
    """The words around the reset phrase are part of the request."""

    def test_clear_all_my_facts_about_my_car_names_the_car(self) -> None:
        got = MemoryManager.is_forget_request("clear all my facts about my car")

        self.assertEqual(got, "my car", (
            "the request names one subject, yet the parser answered "
            f"{got!r}: the reset pattern matched 'clear all my facts' and the "
            "words after its noun were never read"))

    def test_each_way_of_naming_the_subject(self) -> None:
        for sentence, subject in (
                ("clear my memories about my car", "my car"),
                ("clear all my facts regarding my editor", "my editor"),
                ("clear all my facts concerning my old job.", "my old job"),
                ("clear all facts related to my backup drive", "my backup drive"),
                ("about my car, clear all my facts", "my car"),
                ("As for my editor: please clear all my facts.", "my editor")):
            with self.subTest(sentence=sentence):
                self.assertEqual(MemoryManager.is_forget_request(sentence), subject)

    def test_the_store_keeps_every_fact_but_the_one_named(self) -> None:
        mm = _store(("car", "a red hatchback"), ("editor", "vim"))

        reply = mm.format_forget_response(
            MemoryManager.is_forget_request("clear all my facts about my car"))

        self.assertEqual(_live_keys(mm), ["editor"], (
            "a request naming the car cleared facts it did not name; it said "
            f"{reply!r}. Rows: {_rows(mm)}"))
        self.assertEqual(reply, "Done. I've forgotten 1 thing about 'my car'.")

    def test_the_memory_turn_takes_only_the_car(self) -> None:
        mm = _store(("car", "a red hatchback"), ("editor", "vim"))

        result = _router(mm)._try_memory("clear all my facts about my car")

        self.assertEqual(_live_keys(mm), ["editor"], (
            "the memory turn cleared the whole store for a request that named "
            f"the car; it replied {getattr(result, 'text', result)!r}"))


class ASubjectEndsWhereItsPunctuationBegins(unittest.TestCase):
    """A subject with closing marks or a closing courtesy is that subject."""

    def test_forget_my_car_with_a_full_stop_names_my_car(self) -> None:
        got = MemoryManager.is_forget_request("forget my car.")

        self.assertEqual(got, "my car", (
            f"the parser took {got!r} as the subject, full stop included, and "
            "no stored fact carries that as words"))

    def test_each_closing_mark(self) -> None:
        for sentence in ("forget my car!", "forget about my car?",
                         "delete the memory about my car.",
                         "please forget my car...", "Don't remember my car."):
            with self.subTest(sentence=sentence):
                self.assertEqual(MemoryManager.is_forget_request(sentence), "my car")

    def test_a_closing_courtesy_is_not_part_of_the_subject(self) -> None:
        for sentence in ("forget my car, please", "forget my car please",
                         "forget about my car, thanks!",
                         "delete the memory about my car, thank you."):
            with self.subTest(sentence=sentence):
                self.assertEqual(MemoryManager.is_forget_request(sentence), "my car")

    def test_the_car_is_forgotten_and_the_reply_says_so(self) -> None:
        for sentence in ("forget my car.", "forget my car, please"):
            with self.subTest(sentence=sentence):
                mm = _store(("car", "a red hatchback"), ("editor", "vim"))

                reply = mm.format_forget_response(
                    MemoryManager.is_forget_request(sentence))

                self.assertEqual(_live_keys(mm), ["editor"], (
                    f"the car the person named was not forgotten; it said {reply!r}"))
                self.assertEqual(reply, "Done. I've forgotten 1 thing about 'my car'.")

    def test_punctuation_alone_names_nothing(self) -> None:
        mm = _store(("backup drive", "mounted at /dev/sdb1."), ("editor", "vim"))

        reply = mm.format_forget_response(MemoryManager.is_forget_request("forget ."))

        self.assertEqual(_live_keys(mm), ["backup drive", "editor"], (
            "a subject that is only punctuation names nothing, yet the forget "
            f"took a fact; it said {reply!r}"))
        self.assertTrue(reply.startswith(NOTHING_DONE), reply)


class NotRememberingIsNotARequestToForget(unittest.TestCase):
    """A sentence about someone not remembering asks for nothing."""

    def test_i_dont_remember_my_password_hint_is_not_a_forget(self) -> None:
        got = MemoryManager.is_forget_request("I don't remember my password hint")

        self.assertIsNone(got, (
            "the person said they do not remember their password hint, and "
            f"the parser read a request to forget {got!r}"))

    def test_others_not_remembering(self) -> None:
        for sentence in ("we don't remember the name of that place",
                         "you don't remember my birthday",
                         "honestly I don't remember my password hint"):
            with self.subTest(sentence=sentence):
                self.assertIsNone(MemoryManager.is_forget_request(sentence))

    def test_the_memory_turn_keeps_the_hint(self) -> None:
        mm = _store(("password hint", "the first pet's name"), ("editor", "vim"))

        result = _router(mm)._try_memory("I don't remember my password hint")

        self.assertEqual(_live_keys(mm), ["editor", "password hint"], (
            "the memory turn deleted the hint for a sentence that asked for "
            f"nothing; it replied {getattr(result, 'text', result)!r}"))


class TheResetClearsEverythingOnlyAsTheWholeRequest(unittest.TestCase):
    """A reset phrase with more after it than a closing courtesy is declined."""

    def test_clear_all_my_facts_except_my_car_is_declined(self) -> None:
        got = MemoryManager.is_forget_request("clear all my facts except my car")

        self.assertEqual(got, DECLINED, (
            f"the parser answered {got!r} for a request that asks to keep the "
            "car: the reset phrase was taken for the whole request"))

    def test_each_reset_phrase_with_more_after_it(self) -> None:
        for sentence in ("clear all my memories and start over",
                         "clear all my memories for me",
                         "clear my memories of my old job",
                         "clear all my facts on this machine",
                         "clear all my facts about"):
            with self.subTest(sentence=sentence):
                self.assertEqual(MemoryManager.is_forget_request(sentence), DECLINED)

    def test_the_store_keeps_every_fact_and_the_reply_says_so(self) -> None:
        mm = _store(("car", "a red hatchback"), ("editor", "vim"))

        reply = mm.format_forget_response(
            MemoryManager.is_forget_request("clear all my facts except my car"))

        self.assertEqual(_live_keys(mm), ["car", "editor"], (
            f"a request to keep the car cleared the store; it said {reply!r}"))
        self.assertTrue(reply.startswith(NOTHING_DONE), reply)
        self.assertNotIn("cleared all", reply)

    def test_the_memory_turn_keeps_the_car(self) -> None:
        mm = _store(("car", "a red hatchback"), ("editor", "vim"))

        result = _router(mm)._try_memory("clear all my facts except my car")

        self.assertEqual(_live_keys(mm), ["car", "editor"], (
            "the memory turn cleared the whole store for a request that asked "
            f"to keep the car; it replied {getattr(result, 'text', result)!r}"))


class ANegationAnywhereInFrontOfTheVerbIsNoForget(unittest.TestCase):
    """The negation guard reads the whole of the request before the verb."""

    def test_i_never_want_you_to_clear_all_my_facts_is_not_a_forget(self) -> None:
        got = MemoryManager.is_forget_request("I never want you to clear all my facts")

        self.assertIsNone(got, (
            f"the parser answered {got!r} for a request that the facts never "
            "be cleared"))

    def test_each_negation(self) -> None:
        for sentence in ("I never want you to forget my car",
                         "I would not like you to forget my car",
                         "I really don't think you should clear all my memories",
                         "we never asked you to delete the memory about my car"):
            with self.subTest(sentence=sentence):
                self.assertIsNone(MemoryManager.is_forget_request(sentence))

    def test_the_memory_turn_keeps_every_fact(self) -> None:
        mm = _store(("car", "a red hatchback"), ("editor", "vim"))

        result = _router(mm)._try_memory("I never want you to clear all my facts")

        self.assertEqual(_live_keys(mm), ["car", "editor"], (
            "the memory turn cleared the store for a request that it never be "
            f"cleared; it replied {getattr(result, 'text', result)!r}"))


class ARequestCarryingTheResetPhraseIsNotCarriedOutInPart(unittest.TestCase):
    """A request that asks for the reset and something more does neither."""

    def test_each_compound_is_declined(self) -> None:
        for sentence in ("forget my editor and clear all my memories",
                         "undelete the memory about my car and clear all my facts",
                         "clear all my facts about my car and clear all my memories",
                         "about my car, clear all my facts about my bike"):
            with self.subTest(sentence=sentence):
                self.assertEqual(MemoryManager.is_forget_request(sentence), DECLINED)

    def test_nothing_is_deleted_and_the_reply_says_what_was_not_done(self) -> None:
        mm = _store(("car", "a red hatchback"), ("editor", "vim"))

        reply = mm.format_forget_response(MemoryManager.is_forget_request(
            "undelete the memory about my car and clear all my facts"))

        self.assertEqual(_live_keys(mm), ["car", "editor"], (
            f"a compound request cleared the store; it said {reply!r}"))
        self.assertEqual(reply, (
            "I haven't forgotten or cleared anything: I couldn't tell exactly "
            "what to forget from that. Name the one thing to forget, or say "
            "\"clear all my memories\" on its own to clear everything."))


class ASubjectThatNamesNothingIsNoForget(unittest.TestCase):
    """The sentinel's text, and words that name nothing, are not subjects."""

    def test_the_sentinels_own_text_is_not_a_subject(self) -> None:
        mm = _four_facts()

        reply = mm.format_forget_response(
            MemoryManager.is_forget_request("forget __ALL__"))

        self.assertEqual(_live_keys(mm), ["bike", "car", "editor", "phone"], (
            f"the sentinel's text as a subject cleared the store; it said {reply!r}"))
        self.assertTrue(reply.startswith(NOTHING_DONE), reply)

    def test_function_words_alone_name_nothing(self) -> None:
        for sentence in ("forget that", "forget the", "forget my", "forget it",
                         "forget about it", "forget this", "forget me",
                         "forget you", "forget please", "forget my thanks",
                         "don't remember it", "forget __ALL__."):
            with self.subTest(sentence=sentence):
                self.assertEqual(MemoryManager.is_forget_request(sentence), DECLINED)

    def test_the_store_keeps_the_facts_holding_those_words(self) -> None:
        for sentence in ("forget that", "forget the", "forget my", "forget please"):
            with self.subTest(sentence=sentence):
                mm = _four_facts()

                reply = mm.format_forget_response(
                    MemoryManager.is_forget_request(sentence))

                self.assertEqual(_live_keys(mm), ["bike", "car", "editor", "phone"], (
                    f"a subject that names nothing took a fact; it said {reply!r}. "
                    f"Rows: {_rows(mm)}"))


# ── the controls: green at both ends ────────────────────────────────────────

class WhatTheParserStillReads(unittest.TestCase):
    """The correction reads more of the request; it must not stop a request
    being read for what it is."""

    def test_the_bare_resets(self) -> None:
        for sentence in ("clear all my memories", "clear my facts",
                         "please clear all my memories.", "Clear all my facts!",
                         "can you clear all my memories",
                         "clear all my memories please",
                         "Can you please clear all my memories? Thanks!"):
            with self.subTest(sentence=sentence):
                self.assertEqual(MemoryManager.is_forget_request(sentence), ALL)

    def test_the_named_forgets(self) -> None:
        for sentence, subject in (
                ("forget my car", "my car"),
                ("please forget about my car", "my car"),
                ("delete the memory about my car", "my car"),
                ("remove the fact about my car", "my car")):
            with self.subTest(sentence=sentence):
                self.assertEqual(MemoryManager.is_forget_request(sentence),
                                 subject)

    def test_a_subject_with_one_content_word_still_names_it(self) -> None:
        for sentence, subject in (("forget the car", "the car"),
                                  ("forget my car", "my car"),
                                  ("forget about my phone", "my phone")):
            with self.subTest(sentence=sentence):
                self.assertEqual(MemoryManager.is_forget_request(sentence),
                                 subject)

    def test_the_car_is_still_forgotten_by_the_car(self) -> None:
        mm = _four_facts()

        reply = mm.format_forget_response(
            MemoryManager.is_forget_request("forget the car"))

        self.assertEqual(_live_keys(mm), ["bike", "editor", "phone"], f"rows: {_rows(mm)}")
        self.assertEqual(reply, "Done. I've forgotten 1 thing about 'the car'.")

    def test_the_imperative_dont_remember(self) -> None:
        for sentence, subject in (
                ("don't remember my car", "my car"),
                ("please don't remember my address", "my address")):
            with self.subTest(sentence=sentence):
                self.assertEqual(MemoryManager.is_forget_request(sentence),
                                 subject)

    def test_a_negated_forget_is_still_not_a_forget(self) -> None:
        self.assertIsNone(MemoryManager.is_forget_request("don't forget my car"))

    def test_the_bare_reset_still_clears_the_store(self) -> None:
        mm = _store(("car", "a red hatchback"), ("editor", "vim"))

        reply = mm.format_forget_response(
            MemoryManager.is_forget_request("clear all my memories"))

        self.assertEqual(_live_keys(mm), [], f"rows: {_rows(mm)}")
        self.assertEqual(reply, "Done. I've cleared all 2 memories.")


if __name__ == "__main__":
    unittest.main()
