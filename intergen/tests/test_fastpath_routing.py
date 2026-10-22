# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Fast-path over/under-match regressions surfaced by the first dyno pull.

Two measured defects, both where a single-value fast-path intercepted a query
it could not fully answer:

1. self_privacy — the identity fast-path's alias fall-through was globally
   broken: a `None` value meant "use a sibling's answer," but the resolver
   ALWAYS fell through to "what are you", so every privacy/OS/capability alias
   ("is my data sent anywhere?", "what OS is this?", "what are your
   capabilities?") silently returned the generic identity blurb instead of the
   intended answer. Aliases now resolve to their documented sibling.

2. compound_mixed — a genuine two-part question ("What's my hostname and what
   year was Linux created?") was decomposed only when action_count crossed a
   TIER-specific threshold. On a TIER_2/TIER_3 box it stayed below threshold,
   was NOT decomposed, and the single-value cache answered only the hostname,
   dropping the second clause. Decomposition now triggers on any genuine
   multi-part split (>= 2 sub-queries), regardless of tier.
"""

from __future__ import annotations

import unittest

from intergen.decomposer import analyze_query
from intergen.interfaces.types import HardwareTierLevel
from intergen.router import ConversationRouter


def _identity(query: str) -> str | None:
    return ConversationRouter._try_self_awareness(query.lower().strip())


class IdentityAliasResolution(unittest.TestCase):
    """The alias fall-through resolves to the documented sibling, not "what are you"."""

    _GENERIC = "manage your system"  # signature phrase of the "what are you" blurb

    def test_privacy_aliases_get_the_privacy_answer(self):
        for q in ("Is my data sent anywhere?", "is my data private?",
                  "where does my data go", "do you send my data",
                  "are you private?", "data stays local"):
            ans = _identity(q)
            self.assertIsNotNone(ans, q)
            self.assertIn("local", ans.lower(), q)
            self.assertNotIn(self._GENERIC, ans.lower(), q)

    def test_os_aliases_get_the_os_answer(self):
        for q in ("what OS is this?", "what os are you?"):
            ans = _identity(q)
            self.assertIsNotNone(ans, q)
            self.assertIn("InterGenOS", ans, q)

    def test_capability_aliases_get_the_capability_answer(self):
        for q in ("what are your capabilities?", "what can you help me with?",
                  "what can you help with?"):
            ans = _identity(q)
            self.assertIsNotNone(ans, q)
            self.assertIn("check system status", ans.lower(), q)

    def test_maker_aliases_get_the_maker_answer(self):
        for q in ("who built you?", "who created you?"):
            ans = _identity(q)
            self.assertIsNotNone(ans, q)
            self.assertIn("InterGenJLU", ans, q)

    def test_local_aliases_confirm_local_operation(self):
        for q in ("are you local?", "where do you run?"):
            ans = _identity(q)
            self.assertIsNotNone(ans, q)
            self.assertIn("local", ans.lower(), q)

    def test_canonical_answers_unchanged(self):
        # Aliases that already worked (fell through to "what are you") still do.
        self.assertIn("InterGen", _identity("tell me about yourself"))
        self.assertIn("InterGen", _identity("who are you?"))
        # And a real privacy question answers privacy.
        self.assertIn("local", _identity("what about privacy?").lower())

    def test_non_identity_query_returns_none(self):
        self.assertIsNone(_identity("install firefox"))
        self.assertIsNone(_identity("what year was Linux created"))


class ThePrivacyAnswersNameWhatLeavesTheMachine(unittest.TestCase):
    """The answers served to a privacy or a run-locally question say what
    leaves the machine instead of denying it.

    They said "Everything stays local ... no queries, responses, or system
    data are sent anywhere" and "No data leaves this system unless you
    explicitly configure cloud escalation", while a web search sends its words
    to the search service, a network check reaches the host it names, and the
    IP answer asks a public resolver. Each answer now says what holds on every
    lane (nothing in the background; something goes out only while it works on
    something the person asked), then names those paths and the conditions they
    run under, as the setup surfaces do."""

    _QUESTIONS = ("what about privacy?", "Is my data sent anywhere?",
                  "is my data private?", "where does my data go",
                  "do you send my data", "is my data sent", "data stays local",
                  "are you private?", "do you run locally?", "are you local?",
                  "where do you run?")
    _NAMED = (
        "is sent in the background — no telemetry",
        "only while I work on something you asked",
        "A web search you ask for, or accept when I offer it",
        "sends its search words to the search service",
        "fetches the first result's page",
        "A network check such as ping or dig reaches the host it names",
        "runs without asking only on your direct request in a conversation "
        "where nothing from outside has been read; otherwise I ask you first",
        "Your IP address is looked up at a public DNS resolver when you ask "
        "for it",
        "a question goes to a cloud provider only if you set one up and "
        "approve each send",
    )
    _DENIALS = ("stays local", "never leaves", "no data leaves",
                "sent anywhere", "entirely on", "everything runs locally",
                "no queries")
    # The texts each answer opens with, whole: the run-locally and privacy
    # answers as they were before the paths were named.
    _OPENINGS = {
        "do you run locally": (
            "Yes — I run locally, on this machine, and so does the model that "
            "answers you. Nothing is sent in the background — no telemetry; "
            "something goes out only while I work on something you asked: a "
            "web search, a network lookup such as your public IP, or a "
            "Phone-A-Friend question you approve."),
        "what about privacy": (
            "The model that answers you runs locally, on this machine, and "
            "nothing is sent in the background — no telemetry. Something "
            "leaves only while I work on something you asked: a web search, a "
            "network lookup such as your public IP, a software download from "
            "the package mirror, or a Phone-A-Friend question you approve."),
    }

    def test_each_answer_names_every_path_and_its_condition(self):
        for q in self._QUESTIONS:
            ans = _identity(q)
            self.assertIsNotNone(ans, q)
            for words in self._NAMED:
                self.assertIn(words, ans, (q, words))

    def test_no_answer_denies_an_egress(self):
        for q in self._QUESTIONS:
            ans = _identity(q).lower()
            for denial in self._DENIALS:
                self.assertNotIn(denial, ans, (q, denial))

    def test_each_answer_opens_with_what_holds_on_every_lane(self):
        # These texts are fixed and cannot read the lane, so neither says that
        # something goes out only on a request or that nothing goes out on its
        # own; each says what goes out while it works on something asked.
        for key, opening in self._OPENINGS.items():
            with self.subTest(key=key):
                ans = _identity(key)
                self.assertTrue(ans.startswith(opening + " "), ans)
                self.assertIn("web search", ans)
                self.assertNotIn("unless you explicitly configure", ans.lower())
                self.assertNotIn("only when you ask", ans)
                # The same meaning in the passive: the IP lookup runs when the
                # person asks for it, and the sentence says no more than that.
                self.assertNotIn("only when asked", ans)
                self.assertNotIn("on its own", ans)


class CompoundDecomposition(unittest.TestCase):
    """A genuine multi-part request decomposes at every tier so no clause drops."""

    _MULTIPART = "What's my hostname and what year was Linux created?"

    def test_multipart_decomposes_at_all_tiers(self):
        for tier in HardwareTierLevel:
            d = analyze_query(self._MULTIPART, tier)
            self.assertTrue(d.needs_decomposition, tier)
            self.assertEqual(len(d.sub_queries), 2, tier)
            self.assertTrue(d.response_prefix, tier)

    def test_tier2_was_the_regression_tier(self):
        # The defect lived here: below the TIER_2 threshold (3), so it used to
        # NOT decompose and the cache ate the second clause.
        d = analyze_query(self._MULTIPART, HardwareTierLevel.TIER_2)
        self.assertTrue(d.needs_decomposition)
        self.assertEqual(d.sub_queries,
                         ["What's my hostname", "what year was Linux created?"])

    def test_single_action_with_and_does_not_decompose(self):
        # "Show disk space and usage" is one action; must not split.
        for tier in HardwareTierLevel:
            d = analyze_query("Show disk space and usage", tier)
            self.assertFalse(d.needs_decomposition, tier)
            self.assertEqual(d.sub_queries, [], tier)

    def test_non_compound_does_not_decompose(self):
        for tier in HardwareTierLevel:
            d = analyze_query("Hello there", tier)
            self.assertFalse(d.needs_decomposition, tier)
            self.assertFalse(d.is_compound, tier)

    def test_verbose_single_request_does_not_over_decompose(self):
        # "look up and tell me X" splits on "and tell" but "...look up" is a
        # contentless fragment — it is ONE request, must not decompose (the
        # over-decomposition the first full pull surfaced on lex_hostname_verbose).
        q = ("Could you please look up and tell me what the hostname of this "
             "particular system is currently set to?")
        for tier in HardwareTierLevel:
            d = analyze_query(q, tier)
            self.assertFalse(d.needs_decomposition, tier)

    def test_substance_guard_preserves_real_compounds(self):
        # Each clause carries a content noun → still a genuine compound.
        for q in ("check memory then restart nginx",
                  "show me my disk space and list running services",
                  "install firefox and tell me the time"):
            d = analyze_query(q, HardwareTierLevel.TIER_2)
            self.assertTrue(d.needs_decomposition, q)


class FragmentRouting(unittest.TestCase):
    """Bare-noun fragments route deterministically via the keyword layer, not the
    flaky LLM tool path. The router calls _match_keywords directly (bypassing
    match()), so the fragment expansion must live in _match_keywords."""

    def setUp(self):
        from intergen.semantic import SemanticMatcher
        from intergen.intents import register_all_intents
        self.m = SemanticMatcher()
        register_all_intents(self.m)

    def test_bare_fragments_match_a_keyword_intent(self):
        # "storage?" used to miss every pattern and fall to the 2B, which
        # fabricated "I can't access storage" (lex_disk_terse).
        for q in ("storage?", "disk?", "ram?", "cpu?", "memory?", "uptime?"):
            r = self.m._match_keywords(q)
            self.assertEqual(r.intent_id, "system_info", q)
            self.assertEqual(r.tool_name, "run_command", q)

    def test_full_phrasings_still_match(self):
        r = self.m._match_keywords("How much space is left on my drive?")
        self.assertEqual(r.intent_id, "system_info")

    def test_non_system_text_does_not_match(self):
        self.assertIsNone(self.m._match_keywords("hello there").intent_id)
        self.assertIsNone(self.m._match_keywords("tell me a joke").intent_id)

    def test_room_disk_synonym_routes_but_metaphors_do_not(self):
        # "room" as a disk synonym must route to system_info ONLY when anchored to
        # a disk/quantity-left signal. The bare "how much room"/"room left"/"enough
        # room" patterns over-matched metaphors and routed them to the disk readout
        # (room-collision finding, LOW). Legit disk-room still routes:
        for q in ("how much room do I have left",
                  "how much room is free",
                  "room left on my disk"):
            self.assertEqual(self.m._match_keywords(q).intent_id, "system_info", q)
        # Metaphorical "room" must NOT classify as system_info (it should fall to
        # the conversational LLM, not return a disk table):
        for q in ("how much room for improvement is there",
                  "there is no room left in the schedule",
                  "make room on the couch please",
                  "is there room in the budget for this",
                  "we have enough room to dance"):
            self.assertNotEqual(self.m._match_keywords(q).intent_id, "system_info", q)


if __name__ == "__main__":
    unittest.main()
