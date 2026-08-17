# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Intercept-layer precedence: a broad route must not capture a specific ask.

Two capture classes, each pinned in both directions — the over-capture must
stop, and the legitimate route must survive. A fix that narrowed the broad
routes into uselessness would pass the first half and fail the second.

  system map   The whole-machine health phrases ("anything wrong", "what's
               wrong with", "anything broken") are objectless by intent, but
               matched as bare substrings. "Is anything wrong with my printer"
               was answered from the cached failed-services / recent-errors /
               top-processes blocks — data that contains nothing about a
               printer — instead of dispatching a live check of the device
               actually asked about.

  explain      The instructional prior ("how do I …") granted the how-to
               corpus its LOOSE default threshold. An orientation ask names no
               procedure, so with the loose threshold the nearest entry — any
               entry — captured it, and "How do I get started with this
               system?" was answered with a literal doc page.
"""
import unittest

import intergen.router as router_mod
from intergen.router import ConversationRouter


class SystemMapTargetTests(unittest.TestCase):
    """Whole-machine phrases must not swallow a specific-device question."""

    def setUp(self):
        self.r = ConversationRouter.__new__(ConversationRouter)

    def _sysmap(self, q):
        return ConversationRouter._is_system_map_query(self.r, q)

    def test_the_reported_turn_printer_health(self):
        """INV-OCE-tail-printer-model class: a printer ask is not the system map."""
        self.assertFalse(
            self._sysmap("is anything wrong with my printer"),
            "a printer question must reach a live printer check; the system "
            "map holds no printer data and would answer from unrelated blocks")

    def test_specific_devices_leave_the_system_map(self):
        for q in ("is the printer working",
                  "is anything broken with my bluetooth",
                  "is my wifi running",
                  "what's wrong with the scanner",
                  "is my microphone active",
                  "is anything wrong with my keyboard",
                  "is anything wrong with my headphones"):
            with self.subTest(q=q):
                self.assertFalse(self._sysmap(q))

    def test_the_plural_of_a_device_is_still_that_device(self):
        """"my printers" is no more a whole-machine question than "my printer".
        A bare word boundary loses the plural (the "s" is a word character), so
        the broad phrase recaptured every ask this gate exists to protect —
        which is also why the noun set had grown one hand-added plural."""
        for q in ("is anything wrong with my printers",
                  "is anything wrong with my monitors",
                  "what's wrong with the scanners",
                  "is anything broken with my speakers"):
            with self.subTest(q=q):
                self.assertFalse(
                    self._sysmap(q),
                    f"{q!r} names a device — the plural must route exactly as "
                    "the singular does")

    def test_whole_machine_health_still_routes_to_the_system_map(self):
        """The precision control — narrowing must not cost the real route."""
        for q in ("Is anything failing on this machine?",
                  "what's failing",
                  "is everything ok",
                  "what services are running",
                  "is anything wrong",
                  "why is it slow",
                  "recent errors",
                  "system health",
                  "status"):
            with self.subTest(q=q):
                self.assertTrue(
                    self._sysmap(q),
                    f"{q!r} is a whole-machine health question and must keep "
                    "the grounded system-map route")

    def test_generic_machine_nouns_are_not_specific_targets(self):
        """system/machine/computer ARE the whole-machine reading."""
        for q in ("is anything wrong with this system",
                  "is anything wrong with my computer",
                  "is anything wrong with the machine"):
            with self.subTest(q=q):
                self.assertTrue(self._sysmap(q))


class ExplainOrientationTests(unittest.TestCase):
    """An orientation ask must not take the corpus's loose threshold."""

    def _orientation(self, q):
        return bool(router_mod._EXPLAIN_ORIENTATION_RE.search(q.lower()))

    def _prior(self, q):
        return bool(router_mod._EXPLAIN_PRIOR_RE.search(q.lower()))

    def test_the_reported_turn_get_started(self):
        """CNV-BOOT-01: answered with a literal doc page."""
        q = "How do I get started with this system?"
        self.assertTrue(self._prior(q), "the instructional prior still matches")
        self.assertTrue(
            self._orientation(q),
            "and it must be recognised as an orientation ask, so the loose "
            "corpus threshold is withheld and a weak match cannot capture it")

    def test_orientation_forms(self):
        for q in ("how do I get started", "where do I begin", "how do I begin",
                  "how do I use this system", "how do I set this up",
                  "show me around", "what should I do first"):
            with self.subTest(q=q):
                self.assertTrue(self._orientation(q))

    def test_concrete_how_tos_keep_the_loose_threshold(self):
        """The precision control — a named procedure is still taught."""
        for q in ("how do I list files", "how do I install firefox",
                  "how do I start the ssh service", "how do I set up a printer",
                  "how do I get started with pkm",
                  "how do I get started with the installer"):
            with self.subTest(q=q):
                self.assertTrue(self._prior(q), f"{q!r} keeps its prior")
                self.assertFalse(
                    self._orientation(q),
                    f"{q!r} names a real procedure and must keep the loose "
                    "corpus threshold that teaches it")


if __name__ == "__main__":
    unittest.main()
