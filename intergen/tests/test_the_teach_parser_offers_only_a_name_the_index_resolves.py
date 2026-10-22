# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The package-action teach parser offers only a name the package index resolves.

An instructional package action the how-to corpus does not cover ("how do I
install htop") is taught the canonical pkm command, and running it is offered.
The parser took the word after the verb as the package whatever that word was:
"how do I check install on this machine?" was taught "To install on, run
`pkm install on`." and an offer to run that command was staged, on both floors.

The name is now taken only when the package index resolves it, asked of pkm's
own resolver (the lookup `pkm install` makes), and only where it closes the
verb's object: ordinary words such as "at", "time", "less" and "file" are real
packages, and "how do I get at my old files?" was offered `pkm install at`. A
name either rule turns away falls through as the stoplist's words do: the
instructional prior keeps the turn off the action path, and the model explains.
Every route() case drives the real model client through its one request
function, with the index stood in.
"""
from __future__ import annotations

import unittest
from unittest import mock

from intergen import router as router_module
from intergen.router import ConversationRouter
from intergen.tests.test_a_code_owned_reply_is_delivered_as_the_client_wrote_it import _turn

CHECK_INSTALL = "how do I check install on this machine?"
EXPLANATION = "To see what is installed, run pkm list."
# The stand-in index: the one name these cases resolve.
INDEX = frozenset({"htop"})
# A stand-in index holding ordinary words that are real packages (at, time,
# less and file resolve in the package index of a released machine).
WORD_INDEX = frozenset({"htop", "vim", "at", "time", "less", "file"})


def _stand_in(index):
    """pkm's resolver (pkm.repo.RepoManager) stood in: its index holds `index`."""

    class _StandIn:
        def get_package(self, name):
            return {"name": name, "repo": "stand-in"} if name in index else None

    return _StandIn


_StandInIndex = _stand_in(INDEX)


def _turn_with_index(user, *, locked, index=INDEX):
    """One turn through route() with pkm's resolver stood in by `index`; the
    model, when it is asked, explains."""
    with mock.patch("pkm.repo.RepoManager", _stand_in(index)):
        return _turn(user, EXPLANATION, locked=locked)


class OnlyANameTheIndexResolvesIsOffered(unittest.TestCase):
    """Through route() on both floors."""

    def test_a_word_after_the_verb_that_names_no_package_is_never_offered(self):
        for locked in (True, False):
            with self.subTest(locked=locked):
                result, router, asked, _ = _turn_with_index(CHECK_INSTALL, locked=locked)
                self.assertIsNone(router._conv.pending_action_offer)
                self.assertNotIn("pkm install on", result.text)
                # The turn is explained by the model, never dispatched.
                self.assertEqual(result.source, "llm_freeform")
                self.assertEqual(result.text, EXPLANATION)
                self.assertEqual(result.tool_calls, [])
                self.assertEqual(asked, 1)

    def test_a_name_the_index_does_not_resolve_is_neither_taught_nor_offered(self):
        for locked in (True, False):
            with self.subTest(locked=locked):
                result, router, _, _ = _turn_with_index(
                    "how do I install widget-editor", locked=locked)
                self.assertIsNone(router._conv.pending_action_offer)
                self.assertNotIn("pkm install widget-editor", result.text)
                self.assertEqual(result.source, "llm_freeform")
                self.assertEqual(result.tool_calls, [])

    def test_a_name_the_index_resolves_is_taught_and_offered(self):
        """CONTROL"""
        for locked in (True, False):
            with self.subTest(locked=locked):
                result, router, asked, _ = _turn_with_index(
                    "how do I install htop", locked=locked)
                self.assertTrue(result.text.startswith(
                    "To install htop, run `pkm install htop`."))
                # A pkm line is staged for the package manager's own tool.
                self.assertEqual(tuple(router._conv.pending_action_offer[:2]),
                                 ("pkm install htop", "manage_packages"))
                self.assertEqual((result.answer_linkage.kind,
                                  result.answer_linkage.renderer),
                                 ("code", "package_action_teach"))
                self.assertEqual(asked, 0)


# Sentences whose word after the verb is a real package that is not the thing
# asked about, with the command each was offered.
NOT_THE_OBJECT = (
    ("how do I get at my old files?", "pkm install at"),
    ("how do I add time to my calendar?", "pkm install time"),
    ("how do I get less noise from my fan?", "pkm install less"),
    ("how do I add file sharing between two computers?", "pkm install file"),
    ("how do I install at a set time each night?", "pkm install at"),
)
# Sentences whose name closes the verb's object, with the command offered.
CLOSES_THE_OBJECT = (
    ("how do I install htop", "pkm install htop"),
    ("how do I install htop?", "pkm install htop"),
    ("how do I install htop.", "pkm install htop"),
    ("how do I install htop on this machine?", "pkm install htop"),
    ("how do I install htop and vim?", "pkm install htop"),
    ("how do I remove vim from this computer", "pkm remove vim"),
    ("how do I install at", "pkm install at"),
)


class TheNameMustCloseTheVerbsObject(unittest.TestCase):
    """Through route() on both floors, with an index in which ordinary words
    are packages."""

    def test_a_package_word_inside_a_longer_phrase_is_never_offered(self):
        for locked in (True, False):
            for ask, command in NOT_THE_OBJECT:
                with self.subTest(locked=locked, ask=ask):
                    result, router, _, _ = _turn_with_index(
                        ask, locked=locked, index=WORD_INDEX)
                    # The teach parser offers nothing; a how-to answer the
                    # corpus holds for the sentence may offer its own command.
                    offer = router._conv.pending_action_offer
                    self.assertTrue(offer is None or offer[0] != command, offer)
                    self.assertNotIn(command, result.text)
                    self.assertNotEqual((result.answer_linkage.kind,
                                         result.answer_linkage.renderer),
                                        ("code", "package_action_teach"))
                    self.assertEqual(result.tool_calls, [])

    def test_a_name_that_closes_the_object_is_taught_and_offered(self):
        """CONTROL: the honest asks keep their offer."""
        for locked in (True, False):
            for ask, command in CLOSES_THE_OBJECT:
                with self.subTest(locked=locked, ask=ask):
                    result, router, _, _ = _turn_with_index(
                        ask, locked=locked, index=WORD_INDEX)
                    offer = router._conv.pending_action_offer
                    # A pkm line is staged for the package manager's own tool.
                    self.assertEqual(tuple(offer[:2]),
                                     (command, "manage_packages"))
                    self.assertIn(f"run `{command}`.", result.text)


class TheParserAsksTheIndexForTheWordAfterTheVerb(unittest.TestCase):

    def test_a_word_that_does_not_close_the_object_is_never_looked_up(self):
        looked_up = []
        for ask, _ in NOT_THE_OBJECT:
            self.assertIsNone(router_module._package_action_teach(
                ask.lower(), looked_up.append), ask)
        self.assertEqual(looked_up, [])

    def test_a_trailing_full_stop_is_not_part_of_the_name(self):
        self.assertEqual(router_module._package_action_teach(
            "how do i install htop.", lambda name: name == "htop"),
            ("pkm install htop", "htop", "install"))

    def test_each_closing_context_closes_the_object(self):
        # The end, a punctuation mark, a conjunction, the kind word, and the
        # machine the package goes on or comes off.
        for text in ("how do i install htop", "how do i install htop, please",
                     "how do i install htop or vim", "how do i install the htop package",
                     "how do i install the htop app", "how do i install htop onto my laptop",
                     "how do i install htop to the system", "how do i remove htop from my machine"):
            with self.subTest(text=text):
                self.assertIsNotNone(router_module._package_action_teach(
                    text, lambda name: name == "htop"))

    def test_the_word_is_looked_up_and_an_unresolved_one_yields_nothing(self):
        looked_up = []

        def resolves(name):
            looked_up.append(name)
            return False

        self.assertIsNone(router_module._package_action_teach(
            "how do i install widget-editor", resolves))
        self.assertEqual(looked_up, ["widget-editor"])
        # "on" in "check install on this machine" does not close the object,
        # so it is turned away before the index is asked.
        self.assertIsNone(router_module._package_action_teach(
            "how do i check install on this machine?", resolves))
        self.assertEqual(looked_up, ["widget-editor"])

    def test_a_resolved_name_maps_to_its_pkm_command(self):
        self.assertEqual(router_module._package_action_teach(
            "how do i uninstall htop", lambda name: True),
            ("pkm remove htop", "htop", "remove"))

    def test_a_stoplist_word_never_reaches_the_index(self):
        looked_up = []
        self.assertIsNone(router_module._package_action_teach(
            "how do i update my system", looked_up.append))
        self.assertEqual(looked_up, [])


class TheLookupIsPkmsOwnResolver(unittest.TestCase):
    """_package_in_index asks pkm's RepoManager, stood in here: the live index
    is never read by a case."""

    def setUp(self):
        self.router = ConversationRouter.__new__(ConversationRouter)

    def test_a_name_pkm_resolves_is_in_the_index_and_another_is_not(self):
        with mock.patch("pkm.repo.RepoManager", _StandInIndex):
            self.assertTrue(self.router._package_in_index("htop"))
            self.assertFalse(self.router._package_in_index("on"))

    def test_an_index_that_cannot_be_read_resolves_nothing(self):
        refused = PermissionError(13, "Permission denied", "/var/cache/pkm/db")
        with mock.patch("pkm.repo.RepoManager", side_effect=refused), \
                self.assertLogs("intergen.router", level="WARNING") as logs:
            self.assertFalse(self.router._package_in_index("htop"))
        self.assertIn("no install offer is staged", logs.output[0])


if __name__ == "__main__":
    unittest.main()
