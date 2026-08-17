# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The keyword/semantic fast path must not serve corrupt LLM synthesis.

MEASURED CLASS — the round-1 2B battery of 2026-08-11 (204 conversations): the
worst served output of the whole round rode source="keyword" with a tool. The
correct tool RAN every time (read_file on /etc/fstab, the disk check, a service
status, web_search); the LLM summarization step then burned the correct result
into structural garbage, and _synthesize_tool_result handed that text straight
back as the answer. Nothing consulted the corruption screen's verdict on this
lane, and nothing consulted the quality gate's either.

What makes the honest answer here better than a sentence: the turn already holds
the tool's own output (every one of these routes carries it as full_output). The
user asked a question the tool ALREADY ANSWERED, so a rejected synthesis serves
that answer — never the garbage, and never a generic apology.

Covered here:
  1. the four measured round-1 cases that an instrument names are not served,
     and the tool's own output is what reaches the user instead;
  2. the fifth measured case, fluent short nonsense, which no instrument named
     when this lane's screen landed and which the short-nonsense check has
     since closed — the test records where the boundary now sits;
  3. a good synthesis is served unchanged and pays nothing;
  4. an exhausted quality ladder (the generic apology) is replaced by the tool's
     own output, not passed through;
  5. THE F1 BINDING: the screen applies to LLM synthesis only — the tool's own
     output is served verbatim even when its shape would trip a corruption
     check (a package listing legitimately repeats);
  6. reporting parity with the agentic lane: a glass event on EVERY decision;
  7. the answer linkage tells the truth about which composer produced the text.

The garbage strings are verbatim from the sealed round-1 run
(results.json sha256 bc0535591c77d5a9c1a73a2cf04def43f505650631e0b01fee69297d44bd7fff),
built programmatically from the sealed JSON rather than typed by hand.
"""
from __future__ import annotations

import unittest
import unittest.mock as mock

from intergen.interfaces.types import LLMResponse
from intergen.llm import LLMRouter
from intergen.router import ConversationRouter as R
from intergen.semantic_health import assess_semantic_health

# ── the five measured round-1 cases, verbatim ────────────────────────────────
READ_FSTAB = (
    '00.\n.\n00.\n.\n0.\n.\n.\n.\n00.\n.\n.\n0.\n00.\n.\n.\n0.\n.\n0.00'
    '00.\n.\n00.0.\n0000.\n..\n.\n.\n00.\n0.\n00.\n0.\n00.\n.0.\n000.\n'
    '0.\n.\n0.\n.\n00000000.\n.\n00.\n0.\n0.\n.\n.\n.\n0.\n0.\n.\n0.\n.'
    '\n.\n0.\n.\n.\n.\n.0.000.\n.\n.\n0.\n00.\n000.\n.\n0.\n0.\n.\n.\n.'
    '\n000.\n..\n.\n.\n.\n0000.\n.\n.\n.\n.\n00.\n0.\n00.\n.\n.\n00.\n.'
    '000000.\n0清在清清运行清明清网络清清清运行明明明清明运行清清温暖运行明在运行运行清清运行明清运行网络明清明在关于明清在明清'
    '明清清清清清运行清运行明运行明网络清清清清运行在明明清运行明清清网络清清清在网络明清清网络明清清清运行网络运行明运行明清清明在清网络'
    '温暖清明明清明网络清在明网络清运行清在清网络清运行清清明清明清明明网络清清清清运行运行清清清在明运行明清清清清清在清在清清网络明运行'
    '清清明运行明清运行清清关于清清清清明清网络运行运行清在清清明明明运行运行明清清清清关于在清明运行清清清清网络明清明运行温暖网络运行清'
    '清清明明运行清清清明清运行在在清清运行清明清运行明清运行'
)
BOOT_COMPLAINT = (
    ',,, ,,  ,"  ,, , .  .\n, \n ,,,\n.  ,,,,,.,   , ""  ,,, (, , ,..,,'
    ',,,,,, ,, ,; , ,,,, (,,,,,,.,,,,,,,,,.  ;,",,,,,,,.,,,, ,, , ,,., '
    ', ,,.,,,,   \n,,""  , .,, (",,  ,\n,,,, ,  ,.,,", \n,,,, (," ,.,,.'
    '  ,,,  ,,,, .,,,,, , "",,,\n." (,,, ,,,,, ,  .,,,,,, ,.,  一个:10 |,'
    ' in or in in in in in in,, in in, in in in, in in is, in, is is, s'
    'tatements in, is is in in in in in, is in in in in in in is in, in'
    ' in in, in in in in in in in in in, in, in to, in in in in in,,, i'
    'n in in,, in in in in in, in in in or,, in, in in in in in in in i'
    'n in in in in in in, in in, in in in in is in in in in in in,,,, i'
    'n in, to in, in in, in in, in, in in in in is in, is in is is in i'
    'n'
)
DISK_NATURAL = (
    '( ( ( ( (\xa0 (\xa0 —\xa0 (（ ( ( (\xa0 （ ( ( ( ( (\xa0 （ ( ( (  ( '
    '( (（ ( (  \n\n  ( (\xa0 ( ( ( (  ( (   ( ( ( （\n\n\xa0 ('
)
WEB_SEARCH_WS = (
    '(词'
)
# The fifth: fluent, short, and meaningless. No r156 instrument names it.
CHECK_SERVICE = (
    'As much least pragmatic unpaid tool'
)

NAMED_BY_AN_INSTRUMENT = (READ_FSTAB, BOOT_COMPLAINT, DISK_NATURAL, WEB_SEARCH_WS)

FSTAB_CONTENT = (
    "UUID=1111-2222  /      ext4  defaults  0 1\n"
    "UUID=3333-4444  /home  ext4  defaults  0 2\n"
)
GOOD_SYNTHESIS = "Your fstab has two entries: the root filesystem and /home."

# A legitimate tool output whose SHAPE trips a corruption check — the F1 case.
PKG_LISTING = "\n".join(f"package-{i}  1.0.{i}  installed" for i in range(60))


def _router():
    """A router with only what this unit needs — no daemon, no tools, no LLM
    server. Nothing here may prompt or reach the network."""
    r = object.__new__(R)
    r._llm = object.__new__(LLMRouter)
    r._llm._last_finish_reason = "stop"
    r._llm._last_semantic_flags = []
    r._last_synthesis_rejection = None
    return r


def _real_flags(text):
    """The flags the SHIPPED screen produces for this text.

    chat() runs the screen on every generation and hands its verdict back on the
    response, so a stub that invented flags would be testing a fiction. This
    calls the real predicate instead — the fixture then carries exactly what
    production would carry for those bytes.
    """
    return assess_semantic_health(text, system_prompt="",
                                  conversation_texts=[]).flags


def _answering(text, *, flags=None, quality_passed=True):
    """Stub the model's reply to the synthesis prompt."""
    def _chat(messages, **kw):
        return LLMResponse(text=text, model="local", local=True,
                           quality_passed=quality_passed,
                           semantic_flags=(list(flags) if flags is not None
                                           else _real_flags(text)))
    return _chat


class RejectedSynthesisServesTheToolOutputTests(unittest.TestCase):
    def setUp(self):
        self.r = _router()

    def _synthesize(self, model_text, *, flags=None, quality_passed=True,
                    raw=FSTAB_CONTENT):
        with mock.patch.object(self.r._llm, "chat",
                               _answering(model_text, flags=flags,
                                          quality_passed=quality_passed)):
            return self.r._synthesize_tool_result(
                "Cat /etc/fstab", "read_file", raw, raw_output=raw)

    def test_the_four_measured_garbage_cases_never_reach_the_user(self):
        for i, garbage in enumerate(NAMED_BY_AN_INSTRUMENT):
            with self.subTest(case=i):
                out = self._synthesize(garbage)
                self.assertNotIn(garbage.strip()[:12], out,
                                 "measured garbage must not be served")
                self.assertIn("UUID=1111-2222", out,
                              "the tool's own answer is what the user gets")
                self.assertTrue(self.r._last_synthesis_rejection,
                                "the rejection must be recorded for the caller")

    def test_a_good_synthesis_is_served_unchanged(self):
        out = self._synthesize(GOOD_SYNTHESIS)
        self.assertEqual(out, GOOD_SYNTHESIS)
        self.assertIsNone(self.r._last_synthesis_rejection)

    def test_an_exhausted_quality_ladder_is_not_passed_through(self):
        # chat() spends its own ladder and returns the honest fallback sentence
        # with quality_passed False. On this lane that sentence is the WRONG
        # answer: the tool already produced the right one.
        out = self._synthesize(LLMRouter._EMPTY_RESPONSE_FALLBACK,
                               quality_passed=False)
        self.assertNotIn("Could you rephrase", out)
        self.assertIn("UUID=1111-2222", out)

    def test_a_semantically_flagged_synthesis_is_replaced(self):
        # Fluent-looking text the corruption screen flagged. The gate says
        # nothing about it; the screen's verdict is what must be consulted.
        out = self._synthesize("The tool reports the following state.",
                               flags=("charset_sanity",))
        self.assertIn("UUID=1111-2222", out)
        self.assertIn("charset_sanity", self.r._last_synthesis_rejection)

    def test_the_fifth_measured_case_is_now_caught_too(self):
        # THE BOUNDARY MOVED. When this lane's screen landed, no instrument
        # named the fifth round-1 case — the gate saw ordinary words and the
        # corruption screen saw no structural damage — and this test recorded
        # that gap so silence could not read as coverage. The short-nonsense
        # check closed it: a reply of at most eight words with no verb, no
        # number, no identifier and no terminal punctuation is not a sentence.
        # The lane's behaviour is unchanged; what changed is that the reply is
        # now named, so the tool's own answer is served instead.
        out = self._synthesize(CHECK_SERVICE)
        self.assertNotIn(CHECK_SERVICE, out)
        self.assertIn("UUID=1111-2222", out)
        self.assertIn("short_nonsense", self.r._last_synthesis_rejection)

    def test_the_tool_output_is_served_verbatim_even_when_its_shape_repeats(self):
        # F1 BINDING: the screen governs LLM synthesis only. A package listing
        # repeats by nature; serving it must never be second-guessed by a
        # corruption check written for prose.
        out = self._synthesize(READ_FSTAB, raw=PKG_LISTING)
        self.assertIn("package-0  1.0.0  installed", out)
        self.assertIn("package-59  1.0.59  installed", out)

    def test_no_usable_tool_output_falls_back_to_the_honest_sentence(self):
        # Nothing to serve: no synthesis, no tool text. The generic sentence is
        # correct HERE, because there is no answer in hand to deliver instead.
        out = self._synthesize(READ_FSTAB, raw="   ")
        self.assertNotIn("00.", out)
        self.assertTrue(out.strip(), "the user must not get an empty message")


class RejectionPredicateTests(unittest.TestCase):
    """The predicate reads the instruments; it never re-judges text itself."""

    def setUp(self):
        self.r = _router()

    def _reason(self, text, *, flags=(), quality_passed=True):
        resp = LLMResponse(text=text, model="local", local=True,
                           quality_passed=quality_passed,
                           semantic_flags=list(flags))
        return self.r._synthesis_rejection_reason(resp, text, "Cat /etc/fstab")

    def test_gate_named_text_is_rejected(self):
        self.assertTrue(self._reason(READ_FSTAB))

    def test_screen_flags_are_rejected_and_named(self):
        reason = self._reason(GOOD_SYNTHESIS, flags=("repetition_blowup",))
        self.assertIn("repetition_blowup", reason)

    def test_exhausted_ladder_is_rejected(self):
        self.assertTrue(self._reason(GOOD_SYNTHESIS, quality_passed=False))

    def test_a_good_reply_is_accepted(self):
        self.assertEqual(self._reason(GOOD_SYNTHESIS), "")


class ReportingParityTests(unittest.TestCase):
    """The agentic lane records every gate decision; so must this one."""

    def setUp(self):
        self.r = _router()

    def _emissions(self, model_text, **kw):
        seen = []

        def _emit(phase, event, **rest):
            seen.append((phase, event, rest.get("detail") or {}))

        with mock.patch("intergen.router.glass.emit", _emit):
            with mock.patch.object(self.r._llm, "chat",
                                   _answering(model_text, **kw)):
                self.r._synthesize_tool_result(
                    "Cat /etc/fstab", "read_file", FSTAB_CONTENT,
                    raw_output=FSTAB_CONTENT)
        return seen

    def test_a_passing_synthesis_is_recorded(self):
        events = [e for e in self._emissions(GOOD_SYNTHESIS)
                  if e[1] == "tool_synthesis_gate"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][2].get("verdict"), "pass")

    def test_a_rejected_synthesis_is_recorded_with_its_reason_and_raw_text(self):
        events = self._emissions(READ_FSTAB)
        gate = [e for e in events if e[1] == "tool_synthesis_gate"]
        rejected = [e for e in events if e[1] == "tool_synthesis_rejected"]
        self.assertEqual(len(gate), 1)
        self.assertNotEqual(gate[0][2].get("verdict"), "pass")
        self.assertEqual(len(rejected), 1,
                         "a rejection must keep the raw text, like the agentic "
                         "lane does — a silently dropped reply teaches nobody")
        self.assertEqual(rejected[0][2].get("raw"), READ_FSTAB)


class AnswerLinkageHonestyTests(unittest.TestCase):
    """The turn must say which composer actually produced the delivered text."""

    def setUp(self):
        self.r = _router()

    def test_renderer_names_the_template_when_no_model_ran(self):
        self.r._last_synthesis_rejection = None
        self.assertEqual(self.r._synth_renderer(False), "template")

    def test_renderer_names_the_model_when_its_text_is_served(self):
        self.r._last_synthesis_rejection = None
        self.assertEqual(self.r._synth_renderer(True), "llm_synth")

    def test_renderer_names_the_tool_output_when_the_synthesis_was_rejected(self):
        self.r._last_synthesis_rejection = "repetitive"
        self.assertEqual(self.r._synth_renderer(True), "tool_output_verbatim")


if __name__ == "__main__":
    unittest.main()
