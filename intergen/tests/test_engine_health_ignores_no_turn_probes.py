r"""The engine-health alarm counts served ANSWERS, and mathematics is not corruption.

THE EVENT. Two minutes into a scenario run on 2026-08-26 the daemon logged:

    ENGINE-HEALTH: sustained semantic-corruption flags — 3 of the last 5 served
    generations were flagged (threshold 3 of 5). The served output is being
    flagged as incoherent.

That line tells a person their machine is serving garbage, and its remedy invites
them to pin llama_server.gpu_layers to 0 or re-run `intergen setup` — to downgrade
a working machine. No corrupt output existed. The three generations that raised it
were:

  * two replies of the single word "Hello", which are the model's answers to the
    HARNESS'S READINESS PROBE ("ping"). They carry no turn context at the model
    boundary. A one-word greeting has no verb, no digit, no identifier and no
    terminal punctuation, so short_nonsense fires on it exactly as written.
  * one genuinely coherent English answer about the Riemann Hypothesis that
    happened to contain LaTeX — \(\zeta(s)\), \sum_{n=1}^{\infty} — whose
    backslashes, braces and carets the charset check counted as mid-token welds.

So two of the three were not served answers at all, and the third was not corrupt.
The window is five generations wide, so on any run that probes twice, ONE flagged
real generation is enough to raise a false alarm on a healthy machine.

WHAT IS FIXED HERE, both halves.

1. A generation with no turn context is not a served answer and is not recorded in
   the health window. The test is the product's own: glass.current_turn_id() is
   empty outside a turn. The skip is emitted rather than silent, so a reader of
   the trace can see the window declining to count something instead of wondering
   why a count did not move.

2. Mathematical notation is exempted from the mid-token weld scan, the way
   backtick-fenced spans already are. Narrowly: only LaTeX control sequences and
   their argument groups are removed, so a real decode weld with no backslash
   command ("TOPMk${conskomland") is untouched and still flags.

WHAT IS NOT CHANGED. short_nonsense still fires on "Hello" — it is written to read
SHAPE, and a bare greeting genuinely has no sentence spine. The defect was never
that the check misjudged the text; it was that a readiness probe's reply was
counted as served output at all. Fixing the check instead would have taught it to
accept a word-bag, which is the thing it exists to catch.
"""

from __future__ import annotations

import unittest

from intergen import glass
from intergen.engine_health import EngineHealthMonitor
from intergen.semantic_health import _check_charset, assess_semantic_health

# The answer from the run, close enough to reproduce the flag: coherent English
# prose carrying the LaTeX the model actually emitted.
LATEX_ANSWER = (
    "The Riemann Hypothesis states that all non-trivial zeros of the Riemann "
    "zeta function \\(\\zeta(s)\\) have real part 1/2. The function is defined "
    "by \\(\\sum_{n=1}^{\\infty} \\frac{1}{n^s}\\) for real part greater than "
    "one, and is continued analytically elsewhere."
)

# Real decode welds, from this module's own worked example. TWO of them, because
# the charset check needs _CHARSET_FUSION_MIN welds before it calls corruption —
# one is within a healthy tokenizer's ordinary noise.
CORRUPT_WELD = ("the output was TOPMk${conskomland and then it went "
                "wr}ong^again before it stopped")


class MathematicsIsNotCorruption(unittest.TestCase):
    """Defect 2. RED at base: the LaTeX answer flags charset_sanity."""

    def test_a_coherent_answer_containing_latex_is_not_flagged(self) -> None:
        tripped, detail = _check_charset(LATEX_ANSWER)
        self.assertFalse(
            tripped,
            f"ordinary English prose with mathematics in it was called "
            f"corruption: {detail}")

    def test_the_whole_screen_agrees(self) -> None:
        self.assertEqual(
            assess_semantic_health(LATEX_ANSWER).flags, [],
            "the answer that helped raise a false engine-degradation alarm")

    def test_a_real_weld_still_flags(self) -> None:
        """The exemption must not blind the check to what it exists for. This
        token carries no LaTeX control sequence, so nothing about it is exempt."""
        tripped, detail = _check_charset(CORRUPT_WELD)
        self.assertTrue(tripped, f"a real decode weld stopped flagging: {detail}")

    def test_script_fusion_still_flags(self) -> None:
        tripped, _ = _check_charset("the host is Austin栾 and the port is Tokyo京")
        self.assertTrue(tripped)

    def test_a_replacement_character_inside_maths_still_flags(self) -> None:
        """The exemption covers the WELD scan, not broken bytes: a replacement
        character anywhere is corruption, mathematics or not."""
        tripped, _ = _check_charset("the sum \\(\\sum_{n=1}\\) came out as �")
        self.assertTrue(tripped)

    def test_a_bare_dollar_amount_is_not_treated_as_maths(self) -> None:
        """The exemption keys on LaTeX control sequences, not on the dollar sign,
        so ordinary prose about money is neither exempted nor flagged."""
        tripped, _ = _check_charset("it costs $5 and the other one costs $10")
        self.assertFalse(tripped)


class _Sink:
    """Stands in for the health monitor's record(), counting what reaches it."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, flags) -> bool:
        self.calls.append(list(flags))
        return False


class OnlyAServedAnswerEntersTheWindow(unittest.TestCase):
    """Defect 1. RED at base: a generation with no turn context is recorded."""

    def _llm(self):
        from intergen.llm import LLMRouter
        llm = LLMRouter(config=None)
        sink = _Sink()
        llm.set_semantic_flag_sink(sink)
        return llm, sink

    def test_a_generation_outside_a_turn_is_not_recorded(self) -> None:
        llm, sink = self._llm()
        self.assertEqual(glass.current_turn_id(), "",
                         "this test's premise: no turn is open here")
        llm._screen_semantic_health("Hello", [])
        self.assertEqual(
            sink.calls, [],
            "the readiness probe's reply was counted as a served answer — this "
            "is what put two 'Hello' generations into the alarm's window")

    def test_a_generation_inside_a_turn_is_recorded(self) -> None:
        llm, sink = self._llm()
        with glass.turn("a-real-turn", "test"):
            llm._screen_semantic_health("Hello", [])
        self.assertEqual(len(sink.calls), 1,
                         "a real turn's answer must still be measured")

    def test_the_flags_are_still_computed_outside_a_turn(self) -> None:
        """Not recording is not the same as not screening: the glass trace still
        carries the verdict, so nothing becomes invisible."""
        llm, _sink = self._llm()
        llm._screen_semantic_health("Hello", [])
        self.assertEqual(llm._last_semantic_flags, ["short_nonsense"],
                         "the screen must still run and still record its verdict")


class TheAlarmNeedsThreeRealAnswers(unittest.TestCase):
    """The consequence, stated as the property that matters: with the probes out
    of the window, the run that raised the alarm no longer raises it."""

    def test_the_run_that_fired_no_longer_fires(self) -> None:
        fired = []
        mon = EngineHealthMonitor(lambda: fired.append(True), window=5,
                                  threshold=3, scheduler=lambda fn: fn())
        # The three generations from the run, with the two probes now excluded
        # and the LaTeX answer no longer flagged: one real, clean answer.
        mon.record([])
        self.assertEqual(fired, [], "a healthy machine raised a degradation alarm")

    def test_three_genuinely_flagged_answers_still_fire(self) -> None:
        """The alarm must still work — this lane narrows what it counts, never
        whether it counts."""
        fired = []
        mon = EngineHealthMonitor(lambda: fired.append(True), window=5,
                                  threshold=3, scheduler=lambda fn: fn())
        for _ in range(3):
            mon.record(["charset_sanity"])
        self.assertEqual(len(fired), 1,
                         "three flagged served answers must still raise it")


if __name__ == "__main__":
    unittest.main()
