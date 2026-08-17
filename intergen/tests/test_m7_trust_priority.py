# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""M7 trust-priority leg — five legs + one rider.

Trace-grounded from two recorded sessions (internal development records): the 2026-07-08 web
session (5 findings) and PI-Z23 (2026-07-06 first-contact, findings
a/b/e). Per the diagnostic doctrine (RULE #11 — check OUR code first), each cause was
located at our layer: our claim-screen markers (leg 1), our freeform delivery screen
(leg 2), our memory-preference matcher (leg 3), our M8-2 invariant detector (leg 4),
our explain gate (leg 5), our unified capability block (rider).

LEG 1 (CRITICAL) — a fabricated COMPLETION ("Here is the script saved directly to
~/hello.sh … I've made it executable and placed it in your home directory") passed
claim_screen CLEAN on a zero-dispatch turn: the M3(ii) markers caught present/future/
in-progress action language but MISSED past-tense completion claims. The marker set now
flags a first-person completed write WITH a filesystem/permission outcome (and the
presentational / re-claim forms), location-anchored so presenting code INLINE is not a
claim.

LEG 2 (the leg-1 root enabler) — on a toolless turn the model's own TEXT staged an offer
("Say yes and I'll take care of pkm sync && pkm upgrade"; "Want me to write it for you and
save it directly?") that no code-owned machinery tracks; a later affirmative then binds to
nothing and the model fabricates. A model-authored self-offer is now screened and
re-grounded so the code-owned offer line stays the ONLY offer surface.

LEG 3 — "I want YOU to run it" tripped the memory-preference matcher ("Want me to remember
that?") twice; an imperative addressed to the assistant is not a preference. The matcher
now excludes the "you … to <verb>" imperative-to-assistant frame.

LEG 4 — a dispatched read (`run_command lpstat -p`) whose result was discarded and replaced
by a "here's how you'd check" explain escaped the M8-2 invariant (non-empty, non-deflecting
text). find_unconsumed_dispatches now recognizes the explain-instead-of-result shape as a
named, loud defect.

LEG 5 — a read-only STATE question ("What packages are installed?") intermittently got a
pkm-list EXPLAIN instead of the real listing (a near-threshold semantic match to the
"list installed packages" how-to corpus entry). The explain gate now diverts a state
question WITHOUT a how-to prior to the deterministic dispatch; a genuine how-to still
teaches (wave-6 leg-1 boundary).

RIDER — the wave-6 capability unification dropped the old compound guard, so a compound
capability-framed ask ("can you read files and also check my disk?") answered the
capability half and DROPPED the rest. The capability block is intercepted WHOLE only when
the capability question is the whole ask (or a verb-compound whose object rides the tail,
e.g. "start and stop services"); a genuine compound falls through to decomposition.

Execution byte-identical: screening / routing / matcher precision only.
"""

from __future__ import annotations

import unittest
from unittest import mock

from intergen import safety
from intergen.memory import MemoryManager
from intergen.decomposer import analyze_query
from intergen.hardware import HardwareTierLevel
from intergen.interfaces.types import MessageRole
from intergen.router import ConversationRouter
from intergen.semantic import SemanticMatcher
from intergen.llm import LLMRouter
from intergen.tool_registry import ToolRegistry

_TIER = list(HardwareTierLevel)[0]


def _native_router():
    reg = ToolRegistry()
    reg.discover_tools()
    return ConversationRouter(
        tool_registry=reg, semantic_matcher=SemanticMatcher(embedder=None),
        llm=LLMRouter(config=None), lock_dispatch=False)


class _StubResult:
    """Minimal ToolResult stand-in for find_unconsumed_dispatches."""
    def __init__(self, name="run_command", content="printer-1 idle",
                 executed=True, success=True, blocked=False, model_summary=""):
        self.name = name
        self.content = content
        self.model_summary = model_summary
        self.executed = executed
        self.success = success
        self.blocked = blocked


# ── LEG 1 — past-tense completion-claim markers (CRITICAL) ──────────────────────
class Leg1CompletionClaimTests(unittest.TestCase):
    # Trace-grounded RED (fabricated completions on zero-dispatch turns).
    RED = [
        # 2026-07-08 finding 1 — the ~/hello.sh fabrication + its re-claim
        "Here is the script saved directly to ~/hello.sh. I've made it executable "
        "and placed it in your home directory.",
        "the script I saved is ready — just run it.",
        # PI-Z23 (a) sibling
        "I've kicked off the system update in the background.",
        "I saved it to your Downloads folder.",
        "I've placed it in your home directory.",
        "I created the file at /etc/foo.conf.",
    ]
    # GREEN — honest / inline code / denials / general prose (anti-lobotomy).
    GREEN = [
        "I wrote a short script for you:",
        "Here is a script you can save yourself:",
        "I haven't run or saved anything this turn.",
        "I did not save the file.",
        "You can save it to ~/hello.sh with a text editor.",
        "I'd place it in your home directory if you'd like.",
        "I made a note of that in my reply.",
        "To make it executable, run chmod +x hello.sh.",
        "I created a plan: first back up, then install.",
    ]

    def test_red_flagged(self):
        for t in self.RED:
            v, m = safety.screen_execution_claim(t, dispatched=False)
            self.assertEqual(v, "violation", t)
            self.assertIsNotNone(m)

    def test_green_clean(self):
        for t in self.GREEN:
            v, m = safety.screen_execution_claim(t, dispatched=False)
            self.assertEqual(v, "clean", t)

    def test_dispatch_suppresses(self):
        # A real dispatch this turn makes even a completion claim honest.
        v, _ = safety.screen_execution_claim(self.RED[0], dispatched=True)
        self.assertEqual(v, "clean")


# ── LEG 2 — model-authored self-offer screen (root enabler) ─────────────────────
class Leg2ModelOfferScreenTests(unittest.TestCase):
    RED = [
        "Say yes and I'll take care of pkm sync && pkm upgrade.",
        "Want me to write it for you and save it directly?",
        "Shall I install that for you?",
        "Just let me know and I'll run the update.",
    ]
    GREEN = [
        "Want me to explain how that works?",
        "I can walk you through the steps.",
        "You can run pkm upgrade yourself when ready.",
        "Here's how to back up your files.",
    ]

    def test_red_flagged_on_toolless_no_staged_offer(self):
        for t in self.RED:
            v, _ = safety.screen_model_text_offer(
                t, dispatched=False, code_offer_staged=False)
            self.assertEqual(v, "violation", t)

    def test_green_clean(self):
        for t in self.GREEN:
            v, _ = safety.screen_model_text_offer(
                t, dispatched=False, code_offer_staged=False)
            self.assertEqual(v, "clean", t)

    def test_dispatch_or_code_offer_suppresses(self):
        # A code-owned offer IS the legitimate offer surface; a dispatched turn is fine.
        self.assertEqual(safety.screen_model_text_offer(
            self.RED[0], dispatched=True, code_offer_staged=False)[0], "clean")
        self.assertEqual(safety.screen_model_text_offer(
            self.RED[0], dispatched=False, code_offer_staged=True)[0], "clean")

    def test_router_regen_fallback_serves_honest_line(self):
        # A violation whose regeneration STILL offers → the honest no-self-offer
        # fallback replaces the draft (never a fabricated bindable offer).
        r = _native_router()
        r._llm.chat = lambda msgs, **k: mock.Mock(text="Say yes and I'll install it for you.")
        out = r._screen_and_correct_model_offer(
            "Say yes and I'll install it for you.", [], dispatched=False,
            source="llm_freeform")
        self.assertEqual(out, safety.honest_no_selfoffer_fallback())

    def test_router_clean_draft_passthrough(self):
        r = _native_router()
        clean = "Here's how you can update: run `pkm sync && pkm upgrade` yourself."
        out = r._screen_and_correct_model_offer(
            clean, [], dispatched=False, source="llm_freeform")
        self.assertEqual(out, clean)

    def test_router_regen_clean_replaces(self):
        r = _native_router()
        r._llm.chat = lambda msgs, **k: mock.Mock(
            text="I can't run that myself — here's how you'd do it.")
        out = r._screen_and_correct_model_offer(
            "Want me to run it for you?", [], dispatched=False, source="llm_freeform")
        self.assertIn("here's how", out.lower())


# ── LEG 3 — memory-preference matcher imperative-frame exclusion ────────────────
class Leg3MemoryPreferenceTests(unittest.TestCase):
    def test_imperative_to_assistant_not_a_preference(self):
        for t in ("But I want YOU to run it",
                  "no- I want YOU- InterGen- to run the script",
                  "I want you to write a script"):
            self.assertEqual(MemoryManager.classify_declarative(t),
                             (None, None, None), t)

    def test_genuine_preference_still_classified(self):
        for t, val in (("I want dark mode", "dark mode"),
                       ("I prefer vim", "vim"),
                       ("I use zsh", "zsh")):
            kind, _key, value = MemoryManager.classify_declarative(t)
            self.assertEqual(kind, "preference", t)
            self.assertEqual(value, val, t)


# ── LEG 4 — dispatched-but-discarded: explain-instead-of-result ────────────────
class Leg4DispatchedButDiscardedTests(unittest.TestCase):
    def test_explain_instead_of_result_flagged(self):
        # finding 3 / PI-Z23 (e): lpstat ran, the answer teaches the command instead.
        for text in ("Here's how you'd check: run `lpstat -p` to list your printers.",
                     "To see your printers, run the following command: `lpstat -p -d`",
                     "You can use the command lpstat to check your printers."):
            probs = safety.find_unconsumed_dispatches(text, [_StubResult()])
            self.assertEqual(len(probs), 1, text)
            self.assertEqual(probs[0][1], "explain_instead_of_result", text)

    def test_genuine_report_not_flagged(self):
        for text in ("Your printers: HP-LaserJet (idle), no default set.",
                     "There is no CUPS scheduler running, so no printers are configured."):
            self.assertEqual(
                safety.find_unconsumed_dispatches(text, [_StubResult()]), [], text)

    def test_prior_reasons_still_detected(self):
        # empty delivery + deflection still flagged (no regression).
        self.assertEqual(
            safety.find_unconsumed_dispatches("", [_StubResult()])[0][1],
            "empty_delivery")
        self.assertEqual(
            safety.find_unconsumed_dispatches(
                "I don't have current data on that.", [_StubResult()])[0][1],
            "deflection_despite_result")


# ── LEG 5 — read-only state question prefers dispatch over explain ─────────────
class Leg5DispatchOverExplainTests(unittest.TestCase):
    def _explain_with_strong_corpus(self, query):
        """Drive _try_explain with the how-to corpus forced to a STRONG match, so a
        None result proves the state-question DIVERT (not a mere retrieve miss)."""
        r = _native_router()
        entry = mock.Mock()
        entry.answer = "You can list packages with `pkm list`."
        entry.action = None
        r._howto = mock.Mock()
        r._howto.retrieve.return_value = (entry, 0.99)
        return r._try_explain(query)

    def test_state_question_diverts_to_dispatch(self):
        # A strong corpus match is available, yet the state question is NOT taught.
        result, prior = self._explain_with_strong_corpus("What packages are installed?")
        self.assertIsNone(result)
        self.assertFalse(prior)

    def test_howto_still_teaches(self):
        # A genuine how-to (with a prior) is NOT diverted — the corpus answer is served.
        result, prior = self._explain_with_strong_corpus("how do I list installed packages")
        self.assertIsNotNone(result)
        self.assertTrue(prior)
        self.assertEqual(result.source, "explain")


# ── RIDER — compound-awareness at the unified capability block ──────────────────
class RiderCompoundCapabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = _native_router()

    def _whole(self, q):
        return self.r._capability_is_whole_ask(analyze_query(q, _TIER))

    def test_single_capability_question_is_whole(self):
        for q in ("can you open an app for me?",
                  "can you search the internet?",
                  "how do I use pkm add"):
            self.assertTrue(self._whole(q), q)

    def test_verb_compound_object_on_tail_is_whole(self):
        # "start and stop services" — the object rides the tail; still one question.
        self.assertTrue(self._whole("can you start and stop services?"))

    def test_genuine_compound_not_whole(self):
        # capability half + a SEPARATE ask → not whole → falls to decomposition.
        for q in ("can you read files and also check my disk?",
                  "can you read files and check my disk space?"):
            self.assertFalse(self._whole(q), q)

    def test_manage_services_still_intercepts_whole(self):
        # Regression guard: the wave-6 sf-cap fix survives — a whole capability
        # question is still intercepted before any dispatch (returns before the
        # decomposition/dispatch path, so this is dispatch-free).
        self.assertEqual(
            self.r.route("can you start and stop services?", decide_only=True).source,
            "capability_question")


if __name__ == "__main__":
    unittest.main()
