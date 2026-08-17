# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""An honest enumeration of real tool data must not read as corruption.

MEASURED FALSE POSITIVE (the one the fast-path screen produced across four
sealed runs, 656 turns): "What packages are installed?" answered with a real
summary of a 979-package listing — "You have 979 packages installed via `pkm`.
Here are a few examples: `Mako`, `Python`, `a52dec`, ..." — tripped the
repetition check, because a long list of short names looks, to a detector built
for prose, exactly like a token loop.

The distinguishing fact is available for free on this path and nowhere else:
the tool's own output is in hand. An enumeration whose items TRACE BACK to that
output is honest however long it runs; an enumeration of items that appear
nowhere in the tool's output is either invented or corrupt, and stays rejected.

Deterministic, no new model calls, and narrow by construction:
  * only a repetition-shaped screen flag can be excused this way — a charset or
    foreign-script flag is corruption regardless of what the tool printed;
  * a gate reason (degenerate / repetitive / echo / artifacts) is NEVER excused:
    the text-shape gate speaks about the reply itself;
  * with no tool output to trace against, nothing is excused.
"""
from __future__ import annotations

import unittest
import unittest.mock as mock

from intergen.interfaces.types import LLMResponse
from intergen.llm import LLMRouter
from intergen.router import ConversationRouter as R

PKG_OUTPUT = "\n".join(
    ["979 packages installed via pkm", "Mako", "Python", "a52dec", "Library",
     "abseil-cpp", "Abseil", "accelerate", "Training", "diffusers", "aom",
     "apr", "argon2", "aspell", "at-spi2-core", "atk", "attr", "audit",
     "autoconf", "automake", "avahi", "babl", "bash", "bc", "binutils"])

HONEST_ENUMERATION = (
    "You have 979 packages installed via `pkm`. Here are a few examples: "
    "`Mako`, `Python`, `a52dec`, `Library`, `abseil-cpp`, `Abseil`, "
    "`accelerate`, `Training`, `diffusers`, `aom`, `apr`, `argon2`, `aspell`, "
    "`at-spi2-core`, `atk`, `attr`, `audit`, `autoconf`, `automake`, `avahi`."
)

INVENTED_ENUMERATION = (
    "You have 979 packages installed via `pkm`. Here are a few examples: "
    "`zzsoft`, `qqlib`, `wibble`, `frobnicator`, `blorp`, `zizzle`, `quux`, "
    "`fnord`, `bletch`, `snork`, `plugh`, `xyzzy`, `thud`, `garply`, `waldo`."
)


def _router():
    r = object.__new__(R)
    r._llm = object.__new__(LLMRouter)
    r._llm._last_finish_reason = "stop"
    r._llm._last_semantic_flags = []
    r._last_synthesis_rejection = None
    return r


def _response(text, flags=(), quality_passed=True):
    return LLMResponse(text=text, model="local", local=True,
                       quality_passed=quality_passed, semantic_flags=list(flags))


class HonestEnumerationSurvivesTests(unittest.TestCase):
    def setUp(self):
        self.r = _router()

    def test_the_measured_false_positive_is_no_longer_rejected(self):
        reason = self.r._synthesis_rejection_reason(
            _response(HONEST_ENUMERATION, ("repetition_blowup",)),
            HONEST_ENUMERATION, "What packages are installed?",
            raw_output=PKG_OUTPUT)
        self.assertEqual(reason, "", "an enumeration of the tool's own items "
                                     "is honest however long it runs")

    def test_an_invented_enumeration_is_still_rejected(self):
        reason = self.r._synthesis_rejection_reason(
            _response(INVENTED_ENUMERATION, ("repetition_blowup",)),
            INVENTED_ENUMERATION, "What packages are installed?",
            raw_output=PKG_OUTPUT)
        self.assertTrue(reason, "items that appear nowhere in the tool output "
                                "are not traceable and stay rejected")

    def test_the_whole_reply_is_served_when_the_enumeration_is_honest(self):
        with mock.patch.object(
                self.r._llm, "chat",
                lambda *a, **k: _response(HONEST_ENUMERATION,
                                          ("repetition_blowup",))):
            out = self.r._synthesize_tool_result(
                "What packages are installed?", "manage_packages", PKG_OUTPUT,
                raw_output=PKG_OUTPUT)
        self.assertEqual(out, HONEST_ENUMERATION)
        self.assertIsNone(self.r._last_synthesis_rejection)


class TheExcuseIsNarrowTests(unittest.TestCase):
    def setUp(self):
        self.r = _router()

    def test_a_charset_flag_is_never_excused(self):
        reason = self.r._synthesis_rejection_reason(
            _response(HONEST_ENUMERATION, ("charset_sanity",)),
            HONEST_ENUMERATION, "What packages are installed?",
            raw_output=PKG_OUTPUT)
        self.assertTrue(reason, "broken bytes are corruption whatever the tool "
                                "printed")

    def test_a_foreign_script_flood_is_never_excused(self):
        reason = self.r._synthesis_rejection_reason(
            _response(HONEST_ENUMERATION, ("foreign_script_flood",)),
            HONEST_ENUMERATION, "What packages are installed?",
            raw_output=PKG_OUTPUT)
        self.assertTrue(reason)

    def test_a_mixed_flag_set_is_never_excused(self):
        reason = self.r._synthesis_rejection_reason(
            _response(HONEST_ENUMERATION,
                      ("repetition_blowup", "charset_sanity")),
            HONEST_ENUMERATION, "What packages are installed?",
            raw_output=PKG_OUTPUT)
        self.assertTrue(reason)

    def test_a_gate_reason_is_never_excused(self):
        # The text-shape gate speaks about the reply itself. A degenerate reply
        # is degenerate even if its few real words came from the tool.
        garbage = "( ( ( ( ( ( ( ( ( ( ( ( ( ( ( ( ( ( ( ( ( ( ( ( ( ("
        reason = self.r._synthesis_rejection_reason(
            _response(garbage), garbage, "What packages are installed?",
            raw_output=PKG_OUTPUT)
        self.assertTrue(reason)

    def test_no_tool_output_excuses_nothing(self):
        reason = self.r._synthesis_rejection_reason(
            _response(HONEST_ENUMERATION, ("repetition_blowup",)),
            HONEST_ENUMERATION, "What packages are installed?",
            raw_output="")
        self.assertTrue(reason, "with nothing to trace against, the flag stands")

    def test_a_repetition_loop_is_not_an_enumeration(self):
        # A token loop has no items to trace; it must not slip through by
        # accident because its one repeated word occurs in the tool output.
        loop = "packages packages packages packages packages packages " * 6
        reason = self.r._synthesis_rejection_reason(
            _response(loop, ("repetition_blowup",)), loop,
            "What packages are installed?", raw_output=PKG_OUTPUT)
        self.assertTrue(reason)

    def test_a_short_reply_is_not_an_enumeration(self):
        short = "You have 979 packages installed."
        reason = self.r._synthesis_rejection_reason(
            _response(short, ("repetition_blowup",)), short,
            "What packages are installed?", raw_output=PKG_OUTPUT)
        self.assertTrue(reason, "three words are not an enumeration; the flag "
                                "means something else there")

    def test_single_letter_fragments_do_not_trace(self):
        # Measured by cross-review: every one of these is a substring of the
        # tool's output by coincidence, so the loop traced at 100% and bought
        # itself the excuse — the corrupt reply would then be served in place
        # of the tool's own answer, the exact substitution the screen prevents.
        fragments = ("Here are the packages: a, b, c, i, o, t, l, s, m, n, p, "
                     "r, u, d, e, g, k, x, y, z, v, w, f")
        reason = self.r._synthesis_rejection_reason(
            _response(fragments, ("repetition_blowup",)), fragments,
            "What packages are installed?", raw_output=PKG_OUTPUT)
        self.assertTrue(reason, "one-character items carry no information and "
                                "cannot prove they came from the tool")

    def test_two_letter_fragments_do_not_trace(self):
        # Every fragment here IS a substring of the tool output (ma<-Mako,
        # py<-Python, li<-Library, …), so the old substring test traced all of
        # them and excused the flag.
        fragments = ("Here are the packages: ma, py, li, ab, ac, di, ao, ap, "
                     "ar, as, at, au, av, ba, bc, bi, bn")
        reason = self.r._synthesis_rejection_reason(
            _response(fragments, ("repetition_blowup",)), fragments,
            "What packages are installed?", raw_output=PKG_OUTPUT)
        self.assertTrue(reason)

    def test_an_item_buried_inside_a_longer_word_does_not_trace(self):
        # "aut" is inside "autoconf" and "automake" but was never printed as an
        # item; a substring match would have counted it as traced.
        buried = ("Here are the packages: aut, ava, bab, bas, bin, aud, aut, "
                  "ava, bab, bas, bin, aud, aut, ava, bab, bas")
        reason = self.r._synthesis_rejection_reason(
            _response(buried, ("repetition_blowup",)), buried,
            "What packages are installed?", raw_output=PKG_OUTPUT)
        self.assertTrue(reason, "a prefix of a real name is not that name")


if __name__ == "__main__":
    unittest.main()
