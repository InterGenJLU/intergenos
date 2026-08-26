# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Generate the ``field_shapes`` corpus class.

WHY THIS CLASS EXISTS. Every other class in this corpus was written by people who
know how the system works, and it shows: the questions are well formed, the
consent is unambiguous, the subject of a question is in the question. Three days
of an ordinary person's use produced conversations of a different shape, and the
turns that failed her were failing on the SHAPE rather than on the topic. Adding
more topics would not have found them. These five shapes would.

  S1  An offer is made and accepted in one or two words. "yes." "please do."
      The acceptance carries no subject at all, so whatever holds the question
      has to be the thing that answers.
  S2  The subject of a question lives in an EARLIER turn — or in the same
      sentence, which is the case that failed most surprisingly.
  S3  A question that reads like one intent and is another. "what time will the
      sun set" is a lookup wearing the clock's words.
  S4  An elliptical reply — "wha?" — which asks for a different answer, never
      the same one again.
  S5  A live-data question phrased as an ordinary one. No "search the web for",
      no "look up": just "is there anything fun to do in <town> today?".

WHAT IS GENERATED AND WHAT IS NOT. Her eight conversations are the HOLDOUT and
are not in this file: nothing here is copied from them. What is taken is the
SHAPE, and every variant below is written here, in code, chosen so that each one
lands on a different branch — consent given, refused, redirected, delayed;
subject at turn -1, -3, or in the sentence; a control that must NOT change
behaviour beside every case that must.

CONTROLS ARE PART OF THE CLASS, NOT A COURTESY. A change that made the router
keener to search would turn every S5 case green and would be a serious
regression. The timeless-knowledge rows and the real-clock rows are here so that
cannot read as success.

Run:  python3 -m intergen.tests.scenario.corpus.generate_field_shapes
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "field_shapes.json"

# Placeholders. A real town and a real subject are needed for the assertions to
# be meaningful (the tool argument must be shown to carry them), but they are
# chosen here rather than taken from the field data.
TOWN = "Millbrook, AL"
TOWN2 = "Cullman, AL"
SUBJECT = "whole house generator"
SUBJECT2 = "chest freezer"

_BASE_TAGS = ["batch:field_shapes", "class:field-shape"]


def _s(sid, name, shape, branch, turns, *, axis, category="conversational",
       extra_tags=()):
    return {
        "id": sid,
        "name": name,
        "axis": list(axis),
        "category": category,
        "postures": ["2B-locked"],
        "tags": _BASE_TAGS + [f"shape:{shape}", f"branch:{branch}"] + list(extra_tags),
        "session_policy": "single-session",
        "turns": turns,
    }


def _turn(user, description, assertions, **kw):
    t = {"user": user, "description": description, "assertions": assertions}
    t.update(kw)
    return t


def _dispatches(subject, why):
    """The assertions that say a web search actually ran, carrying the subject."""
    return [
        ["uses_tool", "web_search", why],
        {"type": "tool_arg_contains", "params": {"tool": "web_search", "key": "query"},
         "value": subject.split()[-1],
         "description": f"the search that ran carries {subject!r}, not an empty or "
                        f"generic query"},
        ["no_fabricated_success", "", "does not claim to have looked something up "
                                      "unless the dispatch actually ran"],
    ]


def _does_not_dispatch(why):
    return [
        ["no_tool", "web_search", why],
        ["no_fabricated_success", "", "does not claim a search happened"],
    ]


# ── S1 — an offer accepted in one or two words ──────────────────────────────
# The question is asked, an offer is the honest answer, and then the person says
# yes in the shortest way a person says yes. The acceptance carries no subject,
# so the thing that holds the question is the thing under test.

_CONSENT_WORDS = [
    ("bare-yes", "yes"),
    ("please-do", "please do"),
    ("sure", "sure"),
    ("ok", "ok"),
    ("yep", "yep"),
    ("go-ahead", "go ahead"),
    ("do-it", "do it"),
    ("yes-please", "yes please"),
    ("yes-thanks", "yes, thank you"),
    ("that-would-be-great", "that would be great"),
]

_REFUSALS = [
    ("no-thanks", "no thanks"),
    ("not-now", "not now"),
    ("never-mind", "never mind"),
    ("dont-bother", "don't bother"),
]


def shape_s1():
    out = []
    ask = f"how much does a {SUBJECT} cost?"
    t1_desc = ("T1: a live-data question. An offer is an honest answer here; what "
               "must NOT happen is a from-memory answer that neither offers nor "
               "searches.")
    t1_assertions = [
        ["not_contains", "I don't know your location",
         "the question named no location and asked for none"],
        ["no_fabricated_state", "", "no unbacked live-state claim"],
    ]
    for branch, word in _CONSENT_WORDS:
        out.append(_s(
            f"FS-S1-{branch}", f"offer accepted with {word!r}", "S1", branch,
            [
                _turn(ask, t1_desc, t1_assertions),
                _turn(word,
                      "T2: the acceptance. It carries no subject — the question "
                      "from T1 is the only thing that can supply one. If nothing "
                      "held it, this turn has nothing to act on.",
                      _dispatches(SUBJECT,
                                  "the accepted offer runs the search it offered")),
            ],
            axis=["context_persistence", "routing"]))
    for branch, word in _REFUSALS:
        out.append(_s(
            f"FS-S1-{branch}", f"offer declined with {word!r}", "S1", branch,
            [
                _turn(ask, t1_desc, t1_assertions),
                _turn(word,
                      "T2: the offer is DECLINED. A router that dispatched on any "
                      "short reply after an offer would pass the acceptance cases "
                      "and be wrong; this is the case that tells them apart.",
                      _does_not_dispatch("a declined offer runs nothing")),
            ],
            axis=["context_persistence", "routing"]))
    # Acceptance that restates the request in full — measured in the field as the
    # case that STILL did not dispatch, and looped the offer a third time.
    out.append(_s(
        "FS-S1-restated", "offer accepted with the request restated in full",
        "S1", "consent-restates-the-request",
        [
            _turn(ask, t1_desc, t1_assertions),
            _turn(f"yes, do a web search for how much a {SUBJECT} costs",
                  "T2: the acceptance restates the whole request, so even a router "
                  "that kept nothing from T1 has the subject in front of it. A "
                  "failure here cannot be blamed on lost context.",
                  _dispatches(SUBJECT, "an explicit restated request runs the search")),
        ],
        axis=["routing"]))
    # Acceptance that redirects to a different subject.
    out.append(_s(
        "FS-S1-redirect", "offer accepted but redirected to another subject",
        "S1", "consent-redirected",
        [
            _turn(ask, t1_desc, t1_assertions),
            _turn(f"yes but for a {SUBJECT2} instead",
                  "T2: consent AND a new subject in one breath. The search must run "
                  "on the NEW subject; running the old one is worse than not running.",
                  _dispatches(SUBJECT2,
                              "the redirected subject is what gets searched")),
        ],
        axis=["context_persistence", "routing"]))
    # Delayed acceptance — one intervening turn, and three.
    filler = [
        ("delayed-1", 1, ["what year did the Vietnam War end?"]),
        ("delayed-3", 3, ["what year did the Vietnam War end?",
                          "what is the capital of Spain?",
                          "how many days are in a leap year?"]),
    ]
    for branch, n, between in filler:
        turns = [_turn(ask, t1_desc, t1_assertions)]
        for q in between:
            turns.append(_turn(
                q, "an unrelated, answerable question between the offer and the "
                   "acceptance — the offer must survive it without being consumed "
                   "by it",
                [["no_tool", "web_search",
                  "a timeless fact needs no search, and must not consume the "
                  "pending offer"]]))
        turns.append(_turn(
            "yes",
            f"T{n+2}: the acceptance arrives {n} turn(s) later than the offer. A "
            f"slot that only survives one turn passes FS-S1-bare-yes and fails here.",
            _dispatches(SUBJECT, "an offer accepted later still runs")))
        out.append(_s(f"FS-S1-{branch}", f"offer accepted {n} turn(s) later",
                      "S1", branch, turns,
                      axis=["context_persistence", "routing"]))
    # "yes" with no offer behind it — the control that stops a router from
    # treating every affirmative as consent to search.
    out.append(_s(
        "FS-S1-unprompted-yes", "an affirmative with no offer behind it",
        "S1", "consent-without-an-offer",
        [
            _turn("what is the capital of Spain?",
                  "T1: an ordinary answerable question. No offer is made, so "
                  "nothing is pending.",
                  [["no_tool", "web_search", "a timeless fact needs no search"]]),
            _turn("yes",
                  "T2: an affirmative with nothing to affirm. The honest reply asks "
                  "what is meant; a search here would be a search for the word "
                  "'yes'.",
                  _does_not_dispatch("nothing was offered, so nothing is accepted")
                  + [["contains_any",
                      "what would you like,not sure what,which,what do you mean,"
                      "say more,clarify",
                      "asks what was meant rather than inventing an action"]]),
        ],
        axis=["routing"]))
    return out


# ── S2 — the subject lives in an earlier turn, or in the sentence ───────────

def shape_s2():
    out = []
    # The place is in the SAME sentence. Measured in the field as the case that
    # answered "I don't know your location" to a sentence that named the town.
    out.append(_s(
        "FS-S2-place-in-sentence", "the place is named in the question itself",
        "S2", "subject-in-this-turn",
        [
            _turn(f"how much snow is {TOWN} forecast to get this winter?",
                  "T1: the town is IN the sentence. A reply that says it does not "
                  "know the location has not read the question it was asked.",
                  [["not_contains", "I don't know your location",
                    "the location is in the sentence"],
                   ["not_contains", "I don't have access to real-time",
                    "a flat refusal with no offer and no search is the failure "
                    "shape, not an honest limit"]]),
            _turn("yes",
                  "T2: whatever T1 offered is accepted; the search must carry the "
                  "town from T1.",
                  _dispatches(TOWN.split(",")[0],
                              "the search carries the town that was named")),
        ],
        axis=["context_persistence", "routing"]))
    # Place at -1, then a question that does not repeat it.
    out.append(_s(
        "FS-S2-place-at-minus-1", "the place was named one turn ago",
        "S2", "subject-at-minus-1",
        [
            _turn(f"is there anything worth seeing in {TOWN}?",
                  "T1: the town enters the conversation.",
                  [["not_contains", "I don't know your location",
                    "the town is in the sentence"]]),
            _turn("when is the weather supposed to cool off there?",
                  "T2: 'there' refers to T1's town and nothing else. A reply that "
                  "asks for a location has lost the only place it was given.",
                  [["not_contains", "I don't know your location",
                    "the place was given one turn ago"]]),
            _turn("yes",
                  "T3: the offer is accepted; the search must carry the town from "
                  "T1, two turns back.",
                  _dispatches(TOWN.split(",")[0],
                              "the search carries the town from two turns back")),
        ],
        axis=["context_persistence"]))
    # Place at -3.
    out.append(_s(
        "FS-S2-place-at-minus-3", "the place was named three turns ago",
        "S2", "subject-at-minus-3",
        [
            _turn(f"is there anything worth seeing in {TOWN}?",
                  "T1: the town enters the conversation.",
                  [["not_contains", "I don't know your location", "the town is here"]]),
            _turn("what year did the Vietnam War end?",
                  "T2: an unrelated turn.",
                  [["no_tool", "web_search", "a timeless fact needs no search"]]),
            _turn("how many days are in a leap year?",
                  "T3: another unrelated turn.",
                  [["no_tool", "web_search", "a timeless fact needs no search"]]),
            _turn("when is the weather supposed to cool off there?",
                  "T4: 'there' still means T1's town. Two unrelated turns must not "
                  "have displaced it.",
                  [["not_contains", "I don't know your location",
                    "the place was given three turns ago and nothing replaced it"]]),
        ],
        axis=["context_persistence"]))
    # Subject (not a place) at -1.
    for branch, follow, why in (
        ("subject-cost-at-minus-1", "how much does one cost?",
         "'one' is the subject from the previous turn"),
        ("subject-where-at-minus-1", "where can I buy one near me?",
         "'one' is the subject from the previous turn"),
        ("subject-which-at-minus-1", "which brand is most reliable?",
         "the question is about the subject from the previous turn"),
    ):
        out.append(_s(
            f"FS-S2-{branch}", f"subject carried forward: {follow!r}",
            "S2", branch,
            [
                _turn(f"I am thinking about buying a {SUBJECT2}.",
                      "T1: the subject enters the conversation as a statement, not "
                      "a question — a shape the corpus had no row for.",
                      [["no_tool", "web_search",
                        "a statement of intent is not a search request"]]),
                _turn(follow,
                      f"T2: {why}. The reply must be about that subject.",
                      [["contains_any", SUBJECT2.split()[-1] + ",freezer",
                        "the answer is about the subject that was named"]]),
            ],
            axis=["context_persistence"]))
    # Two places in play, question names neither.
    # A pronoun, rather than a bare ellipsis, carrying the subject forward.
    out.append(_s(
        "FS-S2-pronoun-it", "a pronoun carries the subject forward",
        "S2", "referent-pronoun",
        [
            _turn(f"tell me about the {SUBJECT2} as an appliance.",
                  "T1: the subject enters.",
                  [["no_tool", "web_search", "a general question needs no search"]]),
            _turn("how long does it usually last?",
                  "T2: 'it' is the subject and nothing else in the conversation "
                  "competes for the pronoun.",
                  [["contains_any", "freezer,years,lifespan",
                    "the answer is about the subject the pronoun names"]]),
        ],
        axis=["context_persistence"]))
    out.append(_s(
        "FS-S2-same-place-new-topic", "the place holds while the topic changes",
        "S2", "subject-held-topic-changes",
        [
            _turn(f"what is there to do in {TOWN}?", "T1: the place enters.",
                  [["not_contains", "I don't know your location", "a place is named"]]),
            _turn("what about places to eat there?",
                  "T2: a new topic, the same place. Surviving a topic change is a "
                  "different thing from surviving an unrelated turn.",
                  [["not_contains", "I don't know your location",
                    "the place was given one turn ago; the topic changed, not the "
                    "place"]]),
        ],
        axis=["context_persistence"]))
    out.append(_s(
        "FS-S2-subject-inside-compound",
        "the subject arrives inside a compound question",
        "S2", "subject-in-compound",
        [
            _turn(f"what is a {SUBJECT2} and how much does one usually cost?",
                  "T1: two questions in one sentence. The corpus has decomposer rows "
                  "for compounds, but none for a compound that also SETS the subject "
                  "for what follows.",
                  [["contains_any", "freezer", "answers about the named subject"]]),
            _turn("where would I buy one?",
                  "T2: 'one' refers to the subject the compound introduced.",
                  [["contains_any", "freezer,appliance,store,retailer",
                    "the answer is about that subject"]]),
        ],
        axis=["context_persistence", "decomposer"]))
    out.append(_s(
        "FS-S2-ambiguous-referent", "two places in play and the question names neither",
        "S2", "referent-ambiguous",
        [
            _turn(f"what is there to do in {TOWN}?", "T1: the first place.",
                  [["not_contains", "I don't know your location", "a place is named"]]),
            _turn(f"what about {TOWN2}?", "T2: a second place enters.",
                  [["not_contains", "I don't know your location", "a place is named"]]),
            _turn("which one has better weather?",
                  "T3: the honest answer names BOTH places or asks which is meant. "
                  "Silently picking one is the failure this row exists for.",
                  [["contains_any",
                    TOWN.split(",")[0] + "," + TOWN2.split(",")[0] + ",which",
                    "names the places in play, or asks which is meant"]]),
        ],
        axis=["context_persistence"]))
    return out


# ── S3 — a question that reads like one intent and is another ──────────────

def shape_s3():
    out = []
    misread = [
        ("sunset", "what time will the sun set today?",
         "'what time' is the clock's phrase, but the answer is not on this "
         "machine's clock — it depends on the date and the place."),
        ("sunrise", "what time does the sun come up tomorrow?",
         "same shape as the sunset case, a day forward."),
        ("store-hours", "what time does the hardware store close?",
         "'what time' again, and the answer is a fact about a business."),
        ("game-start", "what time does the game start tonight?",
         "'what time' again, and the answer is live."),
        ("drive-length", "how long is the drive from here to the coast?",
         "'how long' reads like a duration the machine could compute; it is a "
         "lookup about roads."),
        ("outside-temp", "what is the temperature outside right now?",
         "'temperature' overlaps the machine's own sensors; the question is "
         "about the weather."),
    ]
    for branch, q, why in misread:
        out.append(_s(
            f"FS-S3-{branch}", f"reads like another intent: {q!r}", "S3", branch,
            [
                _turn(q, f"T1: {why} Answering from the machine's clock, calendar or "
                         f"sensors is the failure; the reply must treat this as "
                         f"something to look up.",
                      [["no_tool", "run_command",
                        "the machine's own clock does not answer this question"],
                       ["not_contains", "I don't have access to real-time",
                        "a flat refusal with no offer is not an answer"]]),
            ],
            axis=["routing"]))
    for branch, q, why in (
        ("distance-to-town", f"how far is it to {TOWN} from here?",
         "'how far' and 'from here' read like something the machine could compute; "
         "it is a lookup about roads and a place it does not know."),
        ("weather-plain", "what's the weather doing?",
         "'weather' is a live fact, and the bare phrasing gives the router no "
         "explicit search request to lean on."),
    ):
        out.append(_s(
            f"FS-S3-{branch}", f"reads like another intent: {q!r}", "S3", branch,
            [
                _turn(q, f"T1: {why} A flat refusal that offers nothing is the "
                         f"failure shape.",
                      [["not_contains", "I don't have access to real-time",
                        "a flat refusal with no offer is not an answer"],
                       ["no_tool", "run_command",
                        "no local command answers this"]]),
            ],
            axis=["routing"]))

    # The controls: questions that DO belong to the fast paths and must stay there.
    controls = [
        ("control-clock", "what time is it?", "run_command",
         "the clock question is the one the machine's own clock DOES answer"),
        ("control-date", "what is today's date?", "run_command",
         "the date question is answered locally"),
        ("control-disk", "how much disk space is left?", "run_command",
         "a real question about this machine stays a question about this machine"),
    ]
    for branch, q, tool, why in controls:
        out.append(_s(
            f"FS-S3-{branch}", f"control — {q!r} must not change", "S3", branch,
            [
                _turn(q, f"CONTROL: {why}. If a change that fixed the rows above "
                         f"also moved this one, it did not fix the routing — it "
                         f"just moved the mistake.",
                      [["not_contains", "I don't know your location",
                        "a local question needs no location"],
                       ["no_tool", "web_search",
                        "a question the machine answers locally must not become a "
                        "web search"]]),
            ],
            axis=["routing"]))
    return out


# ── S4 — an elliptical reply ────────────────────────────────────────────────

def shape_s4():
    out = []
    lead = "how do I care for blackberry briars that have become overgrown?"
    lead_desc = ("T1: an ordinary how-to question. Its answer is what the next turn "
                 "must not simply repeat.")
    lead_assertions = [["no_tool", "web_search",
                        "a general how-to needs no search"]]
    asking_again = [
        ("wha", "wha?"),
        ("huh", "huh?"),
        ("what", "what?"),
        ("sorry", "sorry?"),
        ("come-again", "come again"),
        ("question-mark", "?"),
        ("dont-understand", "i don't understand"),
        ("lost-me", "you lost me"),
    ]
    for branch, reply in asking_again:
        out.append(_s(
            f"FS-S4-{branch}", f"elliptical reply {reply!r}", "S4", branch,
            [
                _turn(lead, lead_desc, lead_assertions),
                _turn(reply,
                      "T2: the person did not understand. They are asking for a "
                      "DIFFERENT answer. Handing back the same one is the single "
                      "reply that cannot help them — measured in the field, where "
                      "the whole procedure came back word for word.",
                      [["not_repeat_of_previous", "",
                        "the answer is not replayed"],
                       ["contains_any",
                        "which part,what part,clarify,rephrase,didn't catch,"
                        "do you mean,unclear,simpler",
                        "asks what was unclear rather than repeating"]]),
            ],
            axis=["routing"]))
    # A rephrase request is NOT a clarification request — it legitimately
    # re-answers, and must still not replay.
    for branch, reply in (("simpler", "say that again but simpler"),
                          ("plain-english", "can you put that in plain english?")):
        out.append(_s(
            f"FS-S4-{branch}", f"rephrase request {reply!r}", "S4", branch,
            [
                _turn(lead, lead_desc, lead_assertions),
                _turn(reply,
                      "T2: this one asks for the SAME content in different words. "
                      "It must not ask what was unclear — it says what it wants — "
                      "and it must not hand back the same text either. The two "
                      "branches are told apart here.",
                      [["not_repeat_of_previous", "",
                        "a simpler answer is a different answer"]]),
            ],
            axis=["routing"]))
    # The control: a follow-up that is NOT elliptical must be answered normally.
    out.append(_s(
        "FS-S4-control-real-followup", "control — a real follow-up is answered, not "
        "treated as confusion", "S4", "control-followup",
        [
            _turn(lead, lead_desc, lead_assertions),
            _turn("when is the best time of year to do that?",
                  "CONTROL: a specific follow-up. A router that answered every short "
                  "follow-up with 'which part was unclear?' would pass the rows above "
                  "and be useless here.",
                  [["not_contains_any", "which part was unclear,what part was unclear",
                    "a specific question is answered, not questioned back"]]),
        ],
        axis=["context_persistence"]))
    return out


# ── S5 — a live-data question phrased as an ordinary one ───────────────────

def shape_s5():
    live = [
        ("events-today", f"is there anything fun to do in {TOWN} today?", "today"),
        ("puppies-near", f"are there any beagle puppies for sale near {TOWN}?",
         "beagle"),
        ("breeders-near", f"which dog breeders are recommended near {TOWN}?",
         "breeders"),
        ("landscaper", f"where in {TOWN} can I find a good landscaper?",
         "landscaper"),
        ("snow-forecast", f"how much snow is {TOWN} forecast to get this winter?",
         "snow"),
        ("insurance-now", "what is the best medicare supplement plan at the "
                          "present time?", "medicare"),
        ("price-now", f"what does a {SUBJECT2} go for these days?", "freezer"),
        ("open-now", "is the county library open right now?", "library"),
        ("gas-price", "what are gas prices around here at the moment?", "gas"),
        ("game-result", "who won the game last night?", "game"),
    ]
    out = []
    for branch, q, token in live:
        out.append(_s(
            f"FS-S5-{branch}", f"live data asked plainly: {q!r}", "S5", branch,
            [
                _turn(q,
                      "T1: no 'search the web for', no 'look up' — just the question "
                      "a person asks. The honest answers are to search, or to offer "
                      "to. What must not happen is a confident answer from memory, "
                      "or a refusal that offers nothing.",
                      [["not_contains", "I don't know your location",
                        "the question named its place, or needs none"],
                       ["no_fabricated_state", "",
                        "a live-state claim must be backed by a check"],
                       ["self_consistent", "",
                        "does not list results and also deny finding any"]]),
                _turn("yes",
                      "T2: whatever T1 offered is accepted. If T1 searched already, "
                      "this turn is where a second search would show up as noise; if "
                      "T1 offered, this is where the offer has to be worth something.",
                      [["uses_tool", "web_search",
                        "the offer made in T1 can actually be accepted"],
                       {"type": "tool_arg_contains",
                        "params": {"tool": "web_search", "key": "query"},
                        "value": token,
                        "description": f"the search carries the subject ({token}), "
                                       f"not the word 'yes'"}]),
            ],
            axis=["capability_recall", "routing"], category="seeking_info"))
    # Controls: timeless knowledge must NOT become a search.
    for branch, q in (("control-vietnam", "what year did the Vietnam War end?"),
                      ("control-capital", "what is the capital of Spain?"),
                      ("control-leap", "how many days are in a leap year?")):
        out.append(_s(
            f"FS-S5-{branch}", f"control — timeless knowledge: {q!r}", "S5", branch,
            [
                _turn(q,
                      "CONTROL: a settled fact. A change that made the router keener "
                      "to search would turn every row above green and would be a "
                      "serious regression; this is where that shows.",
                      [["no_tool", "web_search",
                        "a settled fact is answered from knowledge, not searched"],
                       ["not_contains", "I don't have access to real-time",
                        "a timeless fact needs no real-time disclaimer"]]),
            ],
            axis=["routing"], category="seeking_info"))
    return out


def build():
    scenarios = (shape_s1() + shape_s2() + shape_s3() + shape_s4() + shape_s5())
    ids = [s["id"] for s in scenarios]
    if len(ids) != len(set(ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise SystemExit(f"duplicate scenario ids: {dupes}")
    return scenarios


def main() -> int:
    scenarios = build()
    OUT.write_text(json.dumps(scenarios, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    by_shape: dict[str, int] = {}
    for s in scenarios:
        shape = next(t.split(":", 1)[1] for t in s["tags"] if t.startswith("shape:"))
        by_shape[shape] = by_shape.get(shape, 0) + 1
    turns = sum(len(s["turns"]) for s in scenarios)
    print(f"wrote {OUT} — {len(scenarios)} scenarios, {turns} turns")
    for shape in sorted(by_shape):
        print(f"  {shape}: {by_shape[shape]} scenarios")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
