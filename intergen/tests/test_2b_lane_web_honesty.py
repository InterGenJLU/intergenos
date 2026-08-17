# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""2B-LANE — web-capability honesty on the LOCKED floor (operator-found live).

Operator gut-test 2026-07-09 (web session, production 2B on the development machine): asked the
Dow Jones' current price and pressed twice on web-search capability. Under the
LOCKED posture (the model never decides a tool; OUR intercepts must catch these
pre-model) the tip's positive-frame-only web/capability intercepts missed all three
turns, so they fell to freeform where the 2B FABRICATED a figure or FALSELY DENIED
a capability it HAS (web_search ships). Live confirmation at the tip: T1 invented
"33,323.25"; T2/T3 denied web search.

CAUSE (RULE #11, OUR layer): (a) _WEB_CAP_Q_RE / _CAP_Q_FRAME_RE match only POSITIVE
frames ("can you …") — a NEGATIVE-framed challenge ("you can't web search?") and a
bare back-reference press ("are you SURE you can't do that?") miss; (b) no intercept
routes a CURRENT external-live-data ask to web (or an honest offer) on the locked
floor. FIX (extends coverage to the lane; native path untouched — the current-data
offer is locked-only, so NATIVE keeps its model-driven web decision):
  - _WEB_CAP_CHALLENGE_RE + _CAP_CHALLENGE_FRAME_RE + _recent_topic_is_web_search
    fold negative/challenge/back-reference frames into _try_capability_question.
  - _try_current_data_offer meets an external live-data ask with an HONEST web
    offer, grounded on the live registry, gated to the locked floor and excluding
    system current-state asks.

Live-9B / live-2B answer text is the box-seat leg; here we pin the routing + the
grounded intercept text (deterministic, daemon-free).

2B-LANE GAP-C FOLLOW-ON (2026-07-09): the same false-denial surface existed for
SYSTEM-TOOL capability questions, not just web. _TOOL_CAP_Q_SPECS covered only three
tools (services / apps / read_file), so positive-frame questions about the rest
(manage_packages, run_command, write_file, analyze_file, take_screenshot) fell
through to the locked freeform floor and false-denied a capability the system HAS
(a peer intercept-layer sweep measured 10 caught / 10 fallthrough at r56, split
purely by coverage).
FIX: the spec set spans EVERY registered tool; the negative/challenge + back-reference
frames built for web (r57) are generalized across the whole tool surface; and the
consent-gated tail is derived from each tool's OWN declared SafetyTier instead of a
hardcoded flag (which had open_application — an AUTO tool — promising a prompt that
never fires). The ToolCapabilityCoverage guard asserts completeness so a newly-added
tool cannot silently re-open the gap.
"""

from __future__ import annotations

import unittest
from unittest import mock

from intergen.interfaces.types import SafetyTier
from intergen.router import ConversationRouter, _TOOL_CAP_Q_SPECS
from intergen.semantic import SemanticMatcher
from intergen.llm import LLMRouter
from intergen.tool_registry import ToolRegistry
from intergen.intents import register_all_intents

# The operator's exact transcript (session_be079571) — the permanent RED fixture.
OPERATOR_TRANSCRIPT = [
    "What is the dow jones trading at right now?",
    "You can't web search?",
    "Wow, I was told you could web search- are you SURE you can't do that?",
]

_REG = ToolRegistry()
_REG.discover_tools()


def _locked_router():
    return ConversationRouter(
        tool_registry=_REG, semantic_matcher=SemanticMatcher(embedder=None),
        llm=LLMRouter(config=None), lock_dispatch=True)


def _native_router():
    return ConversationRouter(
        tool_registry=_REG, semantic_matcher=SemanticMatcher(embedder=None),
        llm=LLMRouter(config=None), lock_dispatch=False)


class OperatorTranscriptRedToGreen(unittest.TestCase):
    """The exact 3-turn session routes honestly on the locked floor (was: fabricate
    / false-deny). Driven in sequence so T3's back-reference has T2 in history."""

    def test_transcript_routes_honestly_in_sequence(self):
        r = _locked_router()
        sources = [r.route(q, decide_only=True).source for q in OPERATOR_TRANSCRIPT]
        self.assertEqual(sources[0], "current_data_offer",
                         "the Dow current-data ask must get an honest web offer")
        self.assertEqual(sources[1], "capability_question",
                         "'you can't web search?' must be answered truthfully")
        self.assertEqual(sources[2], "capability_question",
                         "the back-reference press must be answered truthfully")

    def test_transcript_answers_are_honest_not_denials(self):
        r = _locked_router()
        answers = [r.route(q).text for q in OPERATOR_TRANSCRIPT]
        # T1: an offer, never a fabricated figure or a flat denial.
        self.assertIn("search the web", answers[0].lower())
        self.assertIn("?", answers[0])  # it OFFERS (a question), not a fake number
        # T2/T3: an affirmative capability answer.
        for a in answers[1:]:
            self.assertIn("yes", a.lower())
            self.assertIn("search the web", a.lower())
            self.assertNotIn("can't", a.lower())


class WebCapabilityChallengeFrames(unittest.TestCase):
    """Negative / challenge web-capability frames answer truthfully (locked)."""

    def _src(self, q, prior=None):
        r = _locked_router()
        if prior:
            r._append_history(prior[0], prior[1])
        return r.route(q, decide_only=True).source

    def test_negative_frames_intercept(self):
        for q in ("you can't web search?",
                  "can't you search the web?",
                  "you are unable to search the internet?",
                  "you're not able to browse online?"):
            self.assertEqual(self._src(q), "capability_question", q)

    def test_positive_frames_still_intercept(self):
        for q in ("can you search the internet?",
                  "do you have internet access?"):
            self.assertEqual(self._src(q), "capability_question", q)

    def test_back_reference_needs_web_context(self):
        # With a prior web-search assistant turn, a bare challenge resolves to web.
        web_prior = ("you can't web search?",
                     "Yes — I can search the web. Ask me to and I'll run it.")
        self.assertEqual(
            self._src("are you SURE you can't do that?", prior=web_prior),
            "capability_question")

    def test_back_reference_without_context_does_not_over_capture(self):
        # No web context in history -> a bare "are you sure?" is NOT a web-cap Q.
        self.assertNotEqual(self._src("are you sure about that?"),
                            "capability_question")


class CurrentDataOffer(unittest.TestCase):
    """External live-data asks get an honest offer on the locked floor; system and
    non-live asks are left alone; NATIVE keeps its model-driven decision."""

    def _src(self, q, native=False):
        r = _native_router() if native else _locked_router()
        return r.route(q, decide_only=True).source

    def test_external_live_subjects_offer(self):
        # NON-location live data (finance / crypto / news) keeps the honest
        # web-search offer. Weather moved to the DIRECT-ANSWER class (location-gated)
        # per the ge9b finding-#3 operator ruling — see test_weather_is_location_gated.
        for q in ("what is the dow jones trading at right now",
                  "current price of bitcoin",
                  "what's the latest news",
                  "nasdaq today"):
            self.assertEqual(self._src(q), "current_data_offer", q)

    def test_weather_is_location_gated(self):
        # ge9b finding #3, D2: weather is a LOCATION-DEPENDENT external basic. With
        # web_search present but no location source in the tree, it routes to the
        # DIRECT-ANSWER class's honest location-absent answer, not the generic offer.
        self.assertEqual(self._src("what's the weather right now"),
                         "direct_answer_external")

    def test_system_current_state_not_captured(self):
        for q in ("what's my current disk usage",
                  "how much cpu am I using right now",
                  "current memory usage on this machine"):
            self.assertNotEqual(self._src(q), "current_data_offer", q)

    def test_non_live_asks_not_captured(self):
        for q in ("what is an ip address",
                  "how do I make a strong password",
                  "read my notes file"):
            self.assertNotEqual(self._src(q), "current_data_offer", q)

    def test_native_path_keeps_model_decision(self):
        # locked-only: NATIVE never routes to the offer (no 9B rewrite).
        self.assertNotEqual(
            self._src("what is the dow jones trading at right now", native=True),
            "current_data_offer")


class GroundedNotHardcoded(unittest.TestCase):
    """The yes/no + offer/decline are grounded on the live registry, never a
    hardcoded claim — web_search absent flips both to an honest negative."""

    def _router_without_web(self):
        r = ConversationRouter.__new__(ConversationRouter)
        r._conversation_history = []
        r._lock_dispatch = True
        r._append_history = lambda *a, **k: None
        r._record = lambda *a, **k: None
        r._tools = mock.Mock()
        r._tools.get_all_names.return_value = ["read_file"]  # no web_search
        return r

    def test_capability_no_when_web_absent(self):
        r = self._router_without_web()
        res = r._try_capability_question("you can't web search?", 0.0)
        self.assertIsNotNone(res)
        self.assertIn("no", res.text.lower())
        self.assertIn("isn't available", res.text.lower())

    def test_current_data_declines_when_web_absent(self):
        r = self._router_without_web()
        res = r._try_current_data_offer(
            "what is the dow jones trading at right now", 0.0)
        self.assertIsNotNone(res)
        self.assertIn("isn't available", res.text.lower())
        self.assertNotIn("want me to look it up", res.text.lower())


class ToolCapabilityCoverage(unittest.TestCase):
    """GAP-C: EVERY registered tool is answerable as a capability question — a
    positive-frame ask routes to the grounded intercept, never the locked freeform
    floor. The completeness guard is the anti-regression: a tool added under
    intergen/tools/ that is neither web nor spec-covered fails this test."""

    # A positive-frame probe per tool (the exact r56-fallthrough shapes for the five
    # that were uncovered, plus the three that already worked).
    PROBES = {
        "manage_services": "can you start and stop services?",
        "open_application": "can you open an app?",
        "read_file": "can you read a file?",
        "analyze_file": "can you analyze an image?",
        "write_file": "can you create and write files?",
        "manage_packages": "can you install software for me?",
        "run_command": "can you run terminal commands?",
        "take_screenshot": "can you take a screenshot?",
    }

    def test_every_registered_tool_is_covered(self):
        # web_search is answered by _try_capability_question (its own intercept); the
        # rest MUST have a _TOOL_CAP_Q_SPECS entry so none can false-deny.
        covered = {t for t, _rx, _h in _TOOL_CAP_Q_SPECS} | {"web_search"}
        registered = {n for n in _REG.get_all_names() if _REG.get_tool(n) is not None}
        missing = registered - covered
        self.assertEqual(missing, set(),
                         f"tools with no capability-question coverage: {missing}")

    def test_positive_frames_route_to_capability(self):
        for tool, q in self.PROBES.items():
            self.assertEqual(_locked_router().route(q, decide_only=True).source,
                             "capability_question", f"{tool}: {q!r}")

    def test_uncovered_tools_answer_affirmatively_not_denied(self):
        # The five r56 fallthroughs specifically must answer "yes … <capability>",
        # never a denial — they ship, so a denial would be a false capability denial.
        for tool in ("manage_packages", "run_command", "write_file",
                     "analyze_file", "take_screenshot"):
            a = _locked_router().route(self.PROBES[tool]).text.lower()
            self.assertIn("yes", a, tool)
            self.assertNotIn("isn't available", a, tool)


class ToolCapabilityGatedTailGrounded(unittest.TestCase):
    """The 'confirmation prompt' tail is GROUNDED on the tool's declared SafetyTier,
    never hardcoded: CONFIRM ⇒ the tail is present, AUTO ⇒ it is absent. Pins the r57
    open_application drift (it is AUTO — no prompt fires) as a permanent guard."""

    _PROBES = ToolCapabilityCoverage.PROBES

    def test_tail_matches_declared_safety_tier(self):
        for tool, q in self._PROBES.items():
            text = _locked_router().route(q).text.lower()
            has_tail = "confirmation prompt" in text
            tier = _REG.get_tool(tool).schema.safety_tier
            self.assertEqual(has_tail, tier == SafetyTier.CONFIRM,
                             f"{tool} tier={tier} tail={has_tail}")

    def test_open_application_promises_no_phantom_prompt(self):
        # r57 hardcoded open_application gated=True; its schema is AUTO — opening an
        # app never reaches the consent gate, so the answer must NOT promise a prompt.
        text = _locked_router().route("can you open an app?").text.lower()
        self.assertIn("yes", text)
        self.assertNotIn("confirmation prompt", text)


class ToolCapabilityChallengeFrames(unittest.TestCase):
    """GAP-C: the negative / challenge / back-reference frames generalize from web
    (r57) across the whole tool surface — a false-denial cannot hide behind phrasing."""

    def _src(self, q, prior=None):
        r = _locked_router()
        if prior:
            r._append_history(prior[0], prior[1])
        return r.route(q, decide_only=True).source

    def test_negative_frames_intercept(self):
        for q in ("you can't install software?",
                  "can't you restart services?",
                  "you're not able to run commands?",
                  "you cannot take a screenshot?"):
            self.assertEqual(self._src(q), "capability_question", q)

    def test_back_reference_press_resolves_to_recent_tool_topic(self):
        # A prior tool-capability answer + a bare press → re-answered for that tool.
        r = _locked_router()
        first = r.route("can you install software for me?")
        self.assertEqual(first.source, "capability_question")
        second = r.route("are you SURE you can't do that?")
        self.assertEqual(second.source, "capability_question")
        self.assertIn("software", second.text.lower())

    def test_bare_press_without_any_cap_topic_does_not_over_capture(self):
        self.assertNotEqual(self._src("are you sure about that?"),
                            "capability_question")


class ToolCapabilityNoLeak(unittest.TestCase):
    """Non-regression: a real imperative / concrete ask is NOT captured as a
    capability question — the intercept returns None and the ask routes/dispatches."""

    def test_imperatives_fall_through(self):
        r = _locked_router()
        for q in ("install firefox", "open firefox", "read my notes file",
                  'read the file "report.txt"', "restart the nginx service",
                  "take a screenshot", "run ls -la", "write hello to ~/notes.txt",
                  "analyze /home/me/pic.png"):
            self.assertIsNone(r._try_tool_capability_question(q, 0.0), q)

    def test_disambiguation_order(self):
        # First matching spec wins; verb+object sequencing keeps these distinct.
        cases = [("read files", "can you open a file?"),
                 ("analyze files", "can you read a pdf?"),
                 ("install, remove", "can you install an app?"),
                 ("open apps", "can you run a program?"),
                 ("analyze files", "can you analyze a screenshot?")]
        r = _locked_router()
        for needle, q in cases:
            res = r._try_tool_capability_question(q, 0.0)
            self.assertIsNotNone(res, q)
            self.assertIn(needle, res.text.lower(), q)


# ── 2B-LANE GAP-A + GAP-B residual corpus (r57 measurement) ──
# The banked r57 residual sweep (r57-20260709T200045Z on the peer box) measured 122
# of 146 web fixtures STILL falling through to the locked freeform floor at r57 —
# genuine live-data asks the 2B would fabricate or disown. That corpus IS the fixture
# set (per the follow-on dispatch): every row is pinned here to its honest route so a
# regression re-opens as a named test failure. Buckets, authored against the data:
#   OFFER    — genuine CURRENT external data → an honest web-search offer.
#   WEBCAP   — a web-ACCESS capability question → a truthful capability answer.
#   FREEFORM — the model answers HONESTLY from its own knowledge (static fact / math /
#              definition / pure recommendation / explicit web-search+recipe dispatch);
#              an offer here would be the wrong dishonesty (implying it cannot answer).
_RESIDUAL_OFFER = (
    "will it rain tomorrow in seattle", "how hot is it outside", "weather?",
    "do i need an umbrella today", "whats the forecast for this weekend",
    "is it gonna snow tonight", "what's happening in the news", "news",
    "whats the latest on the election", "anything going on locally i should know about",
    "any big tech news this week", "price of gold", "what's tesla stock at",
    "how much is a gallon of gas right now", "btc price", "what's the dow at",
    "WHAT IS APPLE STOCK PRICE", "spot price of silver per ounce", "did the cubs win",
    "when's the next world cup", "whats the score of the lakers game",
    "who plays tonight in the nba", "premier league standings",
    "when is the next f1 race and where", "who won the game last night",
    "who's the president of france", "who is the pope right now", "restaurants near me",
    "movie showtimes near me tonight", "coffee shops open right now", "nearest gas station",
    "is the grocery store still open", "urgent care near me open late",
    "best budget laptop 2026", "reviews of the pixel 9",
    "where can i get a ps5 cheapest right now", "convert 100 usd to euros",
    "whats the exchange rate for pounds to dollars", "how much is 5000 yen in usd",
    "how's the weather in denver", "whats nvidia trading at", "did the yankees win last night",
    "any news about the storm", "convert 50 dollars to euros", "whats a good pizza place near me",
    "is flight ua245 delayed", "how's traffic on the 405 right now",
    "whats teh wether liek tomrrow", "pollen count today", "is the air quality bad today",
    "whats the current 30 year mortgage rate", "who won best picture this year",
    "when does the new avatar movie come out", "what were last nights powerball numbers",
    "when is thanksgiving this year", "somewhere good to eat around here",
    "whats going on at the arena this weekend", "is taylor swift touring near me this year",
    "what's the best phone to buy right now", "weather in miami", "is it raining",
    "how's crypto doing", "who won the election", "IS IT GONNA BE HOT TODAY",
    "who is drake dating now", "was there a recall on the honda civic recently",
    "how much is a big mac these days", "who won album of the year at the grammys",
    "gonna clear up this afternoon or stay cloudy", "did the fed raise rates",
    "when's the next season of stranger things dropping", "whats the temp right now",
    "whats trending right now", "can i get a table at that steakhouse downtown tonight",
    "im flying to chicago friday whats the weather look like",
    "any good black friday deals on tvs", "whats the week looking like weather wise",
    "when do the packers play next", "anything new today", "is target open on christmas",
    "hey do u kno if its supposed to rain this evening cuz i wanna go for a run",
    "cheapest gas near me", "who's the ceo of twitter now", "how much does a tesla cost now",
    "whats the chance of rain today", "who delivers thai food to my area",
    "whats the weather at the beach this weekend", "whats dogecoin at today",
    "whats going on in ukraine", "tomorrows weather", "top rated sushi place near me",
    "who won the super bowl this year", "how much is 1 bitcoin in dollars",
    "whats the top story right now", "any weather warnings for my area",
    "is the switch 2 in stock anywhere", "is it supposed to be nice tomorrow",
    "how much does the new steam deck cost",
)
_RESIDUAL_WEBCAP = (
    "are you able to look up live info", "can u google stuff",
    "do you know current events or are you offline",
    "are you connected to the internet right now",
    "whats your ability to pull info from the web", "do u browse the internet",
)
_RESIDUAL_FREEFORM = (
    "how far is the moon from earth", "whats the capital of australia",
    "how fast does light travel", "is the airpods pro worth it vs the regular ones",
    "whats the best noise cancelling headphones under 200", "whats 15 percent of 80",
    "what does photosynthesis mean", "how many ounces in a pound",
    "good laptop for video editing under 1500", "whats the average temperature in phoenix in july",
    "whos the guy that made linux again", "whats a good lightweight photo editor for linux",
    "find me a recipe for banana bread",
    "find me a recipe for banana bread — i really need this to work",
    "show me find me a recipe for banana bread", "please find me a recipe for banana bread",
)
# WD-1: the two prefixed EXPLICIT web-search dispatches (a courtesy/show-me lead before
# "search the web for …") belong in the web_search dispatch lane, not freeform — moved
# out of _RESIDUAL_FREEFORM once the keyword anchor tolerates the prefix.
_RESIDUAL_WEBDISPATCH = (
    "show me search the web for how to set up a static IP on linux",
    "if you don't mind, search the web for how to set up a static IP on linux",
)


def _intercept_route(q):
    """The route the LOCKED-floor pre-model intercepts assign to `q`: the web
    capability intercept, then the current-data offer, else freeform (no intercept)."""
    r = _locked_router()
    if r._try_capability_question(q, 0.0) is not None:
        return "capability_question"
    if r._try_current_data_offer(q, 0.0) is not None:
        return "current_data_offer"
    return "freeform"


class GapBResidualCorpus(unittest.TestCase):
    """Every one of the 122 r57 residual web fixtures routes to its honest lane."""

    def test_corpus_size_is_the_measured_122(self):
        self.assertEqual(
            len(_RESIDUAL_OFFER) + len(_RESIDUAL_WEBCAP)
            + len(_RESIDUAL_FREEFORM) + len(_RESIDUAL_WEBDISPATCH), 122)

    def test_live_data_asks_get_the_offer(self):
        miss = [q for q in _RESIDUAL_OFFER
                if _intercept_route(q) != "current_data_offer"]
        self.assertEqual(miss, [], f"live-data asks not offered: {miss}")

    def test_web_access_capability_questions_answer_truthfully(self):
        miss = [q for q in _RESIDUAL_WEBCAP
                if _intercept_route(q) != "capability_question"]
        self.assertEqual(miss, [], f"web-cap questions not answered: {miss}")

    def test_knowledge_asks_stay_freeform(self):
        # Static facts / math / definitions / recommendations / explicit dispatch —
        # the model answers these; an offer would wrongly imply it cannot.
        wrong = [(q, _intercept_route(q)) for q in _RESIDUAL_FREEFORM
                 if _intercept_route(q) != "freeform"]
        self.assertEqual(wrong, [], f"knowledge asks wrongly intercepted: {wrong}")


class GapACurrentDataPrecision(unittest.TestCase):
    """The relaxed offer keeps the wave-6 machine-scope boundary and does not capture
    system ACTION commands that merely contain a live-subject word."""

    def test_system_current_state_still_excluded(self):
        for q in ("what's my current disk usage", "how much cpu am I using right now",
                  "current memory usage on this machine"):
            self.assertNotEqual(_intercept_route(q), "current_data_offer", q)

    def test_action_command_with_subject_word_not_offered(self):
        # "restart the weather service" is a dispatch, not a live-data question.
        for q in ("restart the weather service", "stop the news daemon",
                  "open the weather app"):
            self.assertNotEqual(_intercept_route(q), "current_data_offer", q)

    def test_native_floor_keeps_model_decision(self):
        # locked-only: NATIVE never routes to the offer (no 9B rewrite).
        r = _native_router()
        self.assertIsNone(r._try_current_data_offer("what's the dow at", 0.0))


class GapAWebCapabilityPositiveOrder(unittest.TestCase):
    """GAP-A residual: the positive verb-noun order + colloquial web-access phrasings
    answer truthfully (were the last r57 web-capability fallthroughs)."""

    def _src(self, q):
        return _locked_router().route(q, decide_only=True).source

    def test_positive_verb_noun_order(self):
        self.assertEqual(self._src("can you web search?"), "capability_question")

    def test_colloquial_web_access_phrasings(self):
        for q in _RESIDUAL_WEBCAP:
            self.assertEqual(self._src(q), "capability_question", q)


class ContractionTargetGuardAndVerbOrder(unittest.TestCase):
    """2B-LANE fold-in F-1 + F-2 (peer r58 verification; OUR-layer, pre-existing
    phrasings). F-1: the target guard read two contraction apostrophes as one quoted
    span, so a capability question with two contractions bypassed the whole intercept.
    F-2: the manage_packages spec held 'manage' only in its noun-first alternative, so
    the verb-first 'manage my packages' matched neither order. Both answer truthfully
    now, in BOTH postures (a capability answer is posture-independent)."""

    def _both_postures(self, q):
        return (_locked_router()._try_tool_capability_question(q, 0.0),
                _native_router()._try_tool_capability_question(q, 0.0))

    def test_f1_two_contractions_no_longer_bypass(self):
        q = "if you don't mind, could you summarize a file's contents?"
        for res in self._both_postures(q):
            self.assertIsNotNone(res, q)
            self.assertEqual(res.source, "capability_question")
            self.assertIn("analyze", res.text.lower())  # summarize → analyze_file

    def test_f2_manage_verb_first_matches(self):
        q = "are you able to manage my packages?"
        for res in self._both_postures(q):
            self.assertIsNotNone(res, q)
            self.assertEqual(res.source, "capability_question")
            self.assertIn("software packages", res.text.lower())

    def test_target_guard_ignores_contractions_but_keeps_real_targets(self):
        # A real path/quoted target still marks a concrete request (bypass); a bare
        # contraction is not a target.
        r = _locked_router()
        for q in ('read the file "report.txt"', "read the file 'notes.txt'",
                  "open /etc/hosts", "summarize ~/report.pdf"):
            self.assertIsNone(r._try_tool_capability_question(q, 0.0), q)
        # A contraction-bearing sentence with no capability frame is untouched.
        for q in ("i don't know what's going on", "isn't it a nice day"):
            self.assertIsNone(r._try_tool_capability_question(q, 0.0), q)


class ClosingRiderF3OC1WD1(unittest.TestCase):
    """2B-LANE closing rider (peer r60 verification net-new, OUR-layer):
    F-3 contracted-declarative capability frame, OC-1 definitional current-data
    over-capture, WD-1 prefixed explicit web-search dispatch. Class fixes."""

    def test_f3_contracted_declarative_frame_both_postures(self):
        # "you're able to <verb>" embedded in an indirect question is a capability ask.
        cases = [("don't you think you're able to take a screenshot?", "screenshot"),
                 ("wouldn't you say you're able to write files?", "write")]
        for q, needle in cases:
            for r in (_locked_router(), _native_router()):
                res = r._try_tool_capability_question(q, 0.0)
                self.assertIsNotNone(res, q)
                self.assertEqual(res.source, "capability_question")
                self.assertIn(needle, res.text.lower())

    def test_f3_frame_without_a_tool_object_is_not_captured(self):
        # A declarative frame with no tool spec must not intercept.
        self.assertIsNone(
            _locked_router()._try_tool_capability_question(
                "you're able to relax now", 0.0))

    def test_oc1_definitional_market_asks_fall_out(self):
        for q in ("what is the dow jones?", "what is the stock market",
                  "what are stocks", "what is bitcoin"):
            self.assertIsNone(_locked_router()._try_current_data_offer(q, 0.0), q)

    def test_oc1_keeps_value_form_and_live_subjects(self):
        # The r59 recall win must survive: the VALUE form and non-"what is" live asks.
        for q in ("what's the dow at", "what is the dow jones trading at right now",
                  "current price of bitcoin", "whats teh wether liek tomrrow"):
            self.assertIsNotNone(_locked_router()._try_current_data_offer(q, 0.0), q)

    def test_wd1_prefixed_web_dispatch_routes_to_web_search(self):
        m = SemanticMatcher(embedder=None)
        register_all_intents(m)
        for q in (("search the web for how to set up a static IP on linux",)
                  + _RESIDUAL_WEBDISPATCH):
            self.assertEqual(getattr(m.match(q), "tool_name", None), "web_search", q)

    def test_wd1_non_dispatch_sentences_not_captured(self):
        m = SemanticMatcher(embedder=None)
        register_all_intents(m)
        for q in ("why would you search the web for that",
                  "i don't want you to search the web"):
            self.assertNotEqual(getattr(m.match(q), "tool_name", None), "web_search", q)


if __name__ == "__main__":
    unittest.main()
