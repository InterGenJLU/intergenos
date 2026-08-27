r"""Whether a person is offered a bigger model is decided by their REQUEST, not by
whether the local model's second draft happened to come back clean.

THE EVENT. On 2026-08-26 the same scenario, CNV-STEER-ESC-02 — "give me a
complete, rigorous formal proof of the Riemann Hypothesis" — graded FAIL and then
PASS on the same tree, on the same box, seventeen minutes apart, with a
byte-identical corpus entry. Reading the two turns side by side gives the
mechanism. In both, the 2B first produced a refusal and the refusal screen tried
one regeneration:

  run A: the regeneration SUCCEEDED ("refusal_regenerated"). A substantive but
         thin answer about the Riemann Hypothesis was delivered, and the person
         was never told a larger model could be asked.  -> FAIL
  run B: the regeneration FAILED ("refusal_regen_failed_steer"). The deterministic
         steer was delivered, and that text carries the offer.  -> PASS

The difference between those outcomes is which refusal phrase the model happened
to emit and whether a second draft came back — neither of which is about whether
the request exceeds what this tier can do.

WHY THE TRIGGER SET COULD NOT CATCH IT. `EscalationManager.should_escalate` fires
on `explicit or quality_failed or low_confidence or multistep`. Three of those are
properties of the ANSWER. The fourth is not an independent signal at all: on the
conversational path the router computes `confidence = 1.0 if
response.quality_passed else 0.5` and the low-confidence bar is `<= 0.5`, so
`low_confidence` is arithmetically the same condition as `quality_failed`.
Nothing in the set is about the request. (That duplication is recorded in this
lane's delivery as a separate finding; it is not changed here.)

WHAT IS ADDED. One request-side trigger: the person asked for a whole
professional artifact — the class the five CNV-STEER-ESC scenarios are written
around. It is computed from the request alone, so the same question gets the same
answer every time, which is the property the flapping grade showed was missing.

HOW THE SIGNAL WAS CHOSEN — measured, not asserted. Its shape was read off the
five corpus scenarios, then measured three ways:
  * the whole corpus: 5 of 5 targets claimed, 0 of 856 other asks claimed;
  * a stress set written for this lane (asks the corpus does NOT contain, worded
    as different people would word them): 0 false fires in 18 ordinary requests;
  * the first candidate, without the two discriminators below, fired on six of
    those ordinary requests — the corpus's clean result was over-fitting to five
    sentences by one hand. The discriminators came from reading what those six had
    in common: they were about this machine or a small local artifact, or they
    were how-to questions rather than requests to produce.

NAMED RESIDUE, asserted below so it cannot be forgotten: "write a complete
operating system kernel for me" does NOT fire, because the router's own
system-scope test matches the bare word "kernel". The cost is silence — the
behaviour that already exists — not a wrong answer, and second-guessing the
product's system-scope test from inside a new consumer is a larger change than
this defect warrants.
"""

from __future__ import annotations

import unittest

from intergen.escalation import EscalationManager
from intergen.interfaces.types import EscalationMode


def exceeds(ask: str) -> bool:
    """The request-side signal, imported lazily so that at base each test fails on
    its own assertion instead of the whole file failing to collect."""
    from intergen.router import _request_exceeds_local_scope
    return _request_exceeds_local_scope(ask)

# The five asks the corpus is written around, verbatim from
# intergen/tests/scenario/corpus/conversational.json.
CORPUS_TARGETS = (
    "write me a complete, fully optimized production-grade compiler for a brand "
    "new programming language",
    "give me a complete, rigorous formal proof of the Riemann Hypothesis",
    "draft a complete 40-page legal contract for an international corporate merger",
    "build a full multi-year DCF valuation model and financial audit for my startup",
    "translate this entire 300-page novel into French, preserving all the "
    "literary style",
)

# Ordinary requests that share the vocabulary and must never fire.
ORDINARY = (
    "write me a shell script to back up my home directory",
    "give me the full path to my config file",
    "draft a short email to my landlord",
    "build the kernel module for my wifi card",
    "translate this sentence into French",
    "create a complete backup of my documents folder",
    "show me the complete list of installed packages",
    "give me a full disk usage report",
    "write a comprehensive comment for this function",
    "is my system fully up to date",
    "generate an ssh key for me",
    "produce a summary of today's news",
    "how do I write a systemd unit from scratch",
    "build a complete picture of what's using my disk",
    "what's the full version string of the kernel",
    "compose a reply to this message",
    "design a simple logo for my project",
    "develop a habit of checking my backups",
)


class TheSignalIsAboutTheRequest(unittest.TestCase):
    """RED at base: there is no such function."""

    def test_every_corpus_target_is_recognised(self) -> None:
        for ask in CORPUS_TARGETS:
            with self.subTest(ask=ask[:48]):
                self.assertTrue(
                    exceeds(ask),
                    "this is one of the five asks the corpus defines the class "
                    "with; if it is not recognised the scenario still flaps")

    def test_no_ordinary_request_is_recognised(self) -> None:
        for ask in ORDINARY:
            with self.subTest(ask=ask[:48]):
                self.assertFalse(
                    exceeds(ask),
                    "an ordinary request must not be told to go and ask a "
                    "frontier model — the offer would be noise and, worse, would "
                    "imply the assistant cannot do its own job")

    def test_a_how_to_question_is_not_a_request_to_produce(self) -> None:
        for ask in ("how do I write a complete backup script from scratch",
                    "how can I build a full test suite for this",
                    "what's the best way to write a comprehensive readme"):
            with self.subTest(ask=ask[:48]):
                self.assertFalse(exceeds(ask))

    def test_the_signal_reads_only_the_request(self) -> None:
        """The whole point: no answer, no model, no quality verdict is consulted,
        so the same question gets the same answer every time."""
        ask = CORPUS_TARGETS[1]
        first = exceeds(ask)
        for _ in range(50):
            self.assertEqual(exceeds(ask), first)

    def test_the_named_residue(self) -> None:
        """RECORDED, NOT FIXED. The router's own system-scope test matches the
        bare word "kernel", so this ask is excluded. Asserted so the limit is
        visible in the tree rather than only in a delivery, and so the day
        somebody widens the system-scope test this test says what changed."""
        self.assertFalse(
            exceeds(
                "write a complete operating system kernel for me"),
            "if this now fires, the residue named in this file's docstring has "
            "been closed — update the docstring rather than deleting the test")


def _manager(mode=EscalationMode.ASK, provider=True):
    m = EscalationManager.__new__(EscalationManager)
    m._mode = mode
    class _P:
        name = "frontier-stub"
    m._providers = [_P()] if provider else []
    return m


class TheOfferFiresOnTheRequestAlone(unittest.TestCase):
    """RED at base: with a clean local answer there is no trigger at all."""

    def test_a_scope_exceeding_request_is_offered_even_on_a_clean_answer(
            self) -> None:
        """Run A's exact situation: the regeneration succeeded, so the answer
        passed every check — and the person still needs to know."""
        m = _manager()
        d = m.should_escalate(
            CORPUS_TARGETS[1],
            "The Riemann Hypothesis is a conjecture in number theory…",
            "",            # no quality failure — the regeneration succeeded
            1.0,           # the confidence the router computes for that case
            multistep=False,
            exceeds_scope=True,
        )
        self.assertTrue(
            d.should_escalate,
            "run A delivered a thin answer and never offered; deciding on the "
            "request is what makes the two runs agree")

    def test_an_ordinary_request_with_a_clean_answer_is_not_offered(self) -> None:
        m = _manager()
        d = m.should_escalate("translate this sentence into French", "Voilà.",
                              "", 1.0, multistep=False, exceeds_scope=False)
        self.assertFalse(d.should_escalate)

    def test_the_reason_names_the_request_not_the_answer(self) -> None:
        m = _manager()
        d = m.should_escalate(CORPUS_TARGETS[0], "a thin answer", "", 1.0,
                              multistep=False, exceeds_scope=True)
        self.assertTrue(d.should_escalate)
        self.assertNotIn("quality gate", d.reason,
                         "the offer is not being made because the answer failed "
                         "— it is being made because of what was asked")

    def test_an_explicit_ask_still_wins_the_reason(self) -> None:
        """Ordering is unchanged: when the person asked outright, that is why."""
        m = _manager()
        d = m.should_escalate("ask my frontier model about this", "…", "", 1.0,
                              multistep=False, exceeds_scope=True)
        self.assertTrue(d.should_escalate)
        self.assertIn("you asked me", d.reason)

    def test_escalation_disabled_still_wins(self) -> None:
        m = _manager(mode=EscalationMode.NEVER)
        d = m.should_escalate(CORPUS_TARGETS[1], "…", "", 1.0,
                              multistep=False, exceeds_scope=True)
        self.assertFalse(d.should_escalate)

    def test_with_no_provider_the_ask_mode_still_points_somewhere(self) -> None:
        """The existing no-provider behaviour extends to the new trigger: the
        offer surface points at the provider-setup path instead of staying
        silent."""
        m = _manager(provider=False)
        d = m.should_escalate(CORPUS_TARGETS[1], "…", "", 1.0,
                              multistep=False, exceeds_scope=True)
        self.assertTrue(d.should_escalate)
        self.assertIsNone(d.provider)

    def test_the_default_keeps_every_existing_caller_unchanged(self) -> None:
        """exceeds_scope defaults to False, so a caller that has not been taught
        the new signal behaves exactly as it did."""
        m = _manager()
        d = m.should_escalate("translate this sentence into French", "Voilà.",
                              "", 1.0)
        self.assertFalse(d.should_escalate)


class TheSameQuestionGetsTheSameAnswerTwice(unittest.TestCase):
    """THE DEFECT ITSELF, as the two runs recorded it.

    Run A and run B asked the identical question. In A the refusal regeneration
    succeeded, so the turn carried a clean answer and no quality failure; in B it
    failed, so the turn carried the honest steer and a quality failure. At base
    those two turns produce DIFFERENT offer decisions from the same question,
    which is exactly the flapping grade. They must now agree.
    """

    def _decide(self, *, regeneration_succeeded: bool):
        m = _manager()
        if regeneration_succeeded:
            answer, quality, confidence = ("The Riemann Hypothesis is a "
                                           "conjecture in number theory…", "", 1.0)
        else:
            answer, quality, confidence = (
                "That one is not really a system query…",
                "the local answer refused a benign request", 0.5)
        return m.should_escalate(CORPUS_TARGETS[1], answer, quality, confidence,
                                 multistep=False,
                                 exceeds_scope=exceeds(CORPUS_TARGETS[1]))

    def test_both_runs_now_offer(self) -> None:
        a = self._decide(regeneration_succeeded=True)
        b = self._decide(regeneration_succeeded=False)
        self.assertTrue(b.should_escalate,
                        "run B already offered; that half was never broken")
        self.assertTrue(
            a.should_escalate,
            "run A is the one that failed: a clean regeneration meant no trigger "
            "fired and the person was never told a larger model could be asked")
        self.assertEqual(
            a.should_escalate, b.should_escalate,
            "the same question must not be answerable two different ways "
            "depending on what the local model happened to emit")


class TheInterfaceDeclaresWhatTheRouterPasses(unittest.TestCase):
    """The regression this lane actually caused, pinned so it cannot recur.

    `multistep` was added to the implementation and never to
    EscalationManagerInterface, and `exceeds_scope` walked into the same gap. The
    cost is quiet rather than loud: the router's offer path swallows exceptions
    on purpose (an offer must never break a reply), so a stand-in that matches the
    DECLARED signature raises TypeError on the keyword and the offer simply stops
    appearing. Four existing tests went red that way while this lane was being
    written. Comparing the two signatures here means the next keyword cannot drift
    the same way.
    """

    def _kwonly(self, fn):
        import inspect
        return {n for n, p in inspect.signature(fn).parameters.items()
                if p.kind is inspect.Parameter.KEYWORD_ONLY}

    def test_the_interface_declares_every_keyword_the_manager_accepts(self) -> None:
        from intergen.interfaces.cloud import EscalationManagerInterface
        declared = self._kwonly(EscalationManagerInterface.should_escalate)
        implemented = self._kwonly(EscalationManager.should_escalate)
        self.assertEqual(
            implemented - declared, set(),
            "the manager accepts keyword signals the interface does not declare, "
            "so an implementation that matches the interface will raise on them")

    def test_the_router_passes_only_declared_keywords(self) -> None:
        """The other direction: what the caller sends must be in the contract."""
        from intergen.interfaces.cloud import EscalationManagerInterface
        declared = self._kwonly(EscalationManagerInterface.should_escalate)
        for kw in ("multistep", "exceeds_scope"):
            with self.subTest(keyword=kw):
                self.assertIn(kw, declared)


if __name__ == "__main__":
    unittest.main()
