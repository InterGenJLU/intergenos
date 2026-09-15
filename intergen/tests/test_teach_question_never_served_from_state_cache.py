# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""An explanation question is never answered from the system-state cache.

Measured on an installed machine 2026-09-15 (the fresh-user web battery,
question 13): "Can you explain what a kernel is, in one sentence?" was
answered "You're running kernel 6.18.10-igos-21." from the state cache — a
WRONG answer, not merely a cached one. The cache keys a question on the
bare word "kernel", and the definitional-ask detector that keeps such
questions out of the keyword and semantic dispatch was consulted only
AFTER the cache; it also did not recognise the polite "can you explain
what a kernel is" shape at all.

What is pinned here:

  * the detector recognises the exact sentence and its look-alike pair —
    "what is a kernel?" is a teach ask, "what kernel am I running?" is a
    live-state ask — and leaves the battery's live-data questions alone;
  * the cache's eligibility test, on a real router with a fake cache, refuses
    the teach ask and still serves the live-state ask, so the explanation
    question reaches the model and every legitimate cache answer stays.
"""

from __future__ import annotations

import unittest

from intergen.router import ConversationRouter

EXACT = "Can you explain what a kernel is, in one sentence?"


class _StateCache:
    def lookup_for_query(self, query):
        return "6.18.10-igos-21" if "kernel" in query.lower() else None


def _router():
    r = ConversationRouter.__new__(ConversationRouter)
    r._max_history = 20
    r._record = lambda *a, **k: None
    r._won = lambda *a, **k: None
    r._current_query_type = "general"
    r._memory = None
    r._embedder = None
    r._state_cache = _StateCache()
    r.detach_conversation()
    return r


class TeachDetectorTests(unittest.TestCase):

    def test_the_exact_sentence_is_a_teach_ask(self):
        self.assertTrue(_router()._is_system_noun_teach(EXACT))

    def test_the_look_alike_pair(self):
        r = _router()
        self.assertTrue(r._is_system_noun_teach("what is a kernel?"))
        self.assertFalse(r._is_system_noun_teach("what kernel am I running?"))

    def test_polite_and_plain_teach_shapes(self):
        r = _router()
        for s in ("explain what a kernel is",
                  "could you explain what the kernel does?",
                  "please explain what a kernel is",
                  "can you tell me what a kernel is?",
                  "What is the kernel?",
                  "how does a kernel work?"):
            self.assertTrue(r._is_system_noun_teach(s), s)

    def test_live_state_questions_are_not_teach_asks(self):
        r = _router()
        for s in ("How much free disk space do I have?",   # battery q06
                  "Is my system up to date?",               # battery q10
                  "what kernel version is this machine on?",
                  "how much memory is free?",
                  "what's my hostname?"):
            self.assertFalse(r._is_system_noun_teach(s), s)


class CacheEligibilityTests(unittest.TestCase):

    def _may_serve(self, r, text, **kw):
        return r._state_cache_may_serve(
            text, text.lower(),
            has_safety_trigger=kw.get("safety", False),
            route_compound_whole=kw.get("compound", False))

    def test_the_explanation_question_does_not_reach_the_cache(self):
        r = _router()
        self.assertFalse(self._may_serve(r, EXACT))
        self.assertFalse(self._may_serve(r, "what is a kernel?"))

    def test_the_live_state_question_still_does(self):
        r = _router()
        self.assertTrue(self._may_serve(r, "what kernel am I running?"))
        self.assertTrue(self._may_serve(r, "what's my hostname?"))

    def test_the_existing_exclusions_hold(self):
        r = _router()
        self.assertFalse(self._may_serve(
            r, "what version of the kernel package is installed?"))
        self.assertFalse(self._may_serve(r, "what kernel am I running?",
                                         safety=True))
        self.assertFalse(self._may_serve(r, "what kernel am I running?",
                                         compound=True))
        r._state_cache = None
        self.assertFalse(self._may_serve(r, "what kernel am I running?"))


if __name__ == "__main__":
    unittest.main()
