# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Tests for the teaching/explain intent gate in the router (PI-218-2).

Exercises _try_explain + the pending-action-offer resolution in isolation
(ConversationRouter.__new__, the same lightweight pattern as test_router_offer),
plus the lexical-prior regex directly. The corpus uses the keyword fallback
(embedder=None) so retrieval is deterministic without the nomic-embed server.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from intergen.router import ConversationRouter, _EXPLAIN_PRIOR_RE
from intergen.howto import HowtoCorpus
from intergen.semantic import SemanticMatcher

# The in-repo ship-source corpus (intergen/data/howto). Pinned EXPLICITLY rather
# than via the default resolver so these tests read the SOURCE OF TRUTH on every
# box — an installed box resolves the default to /usr/share/intergen/howto (the
# deployed copy, which lags the tree until the next build), which would make a
# tree-only entry invisible to a default-dir fixture.
_REPO_HOWTO = Path(__file__).resolve().parent.parent / "data" / "howto"


def _explain_router(data_dir=None):
    """A bare router with just the attributes _try_explain / the offer resolver
    touch — no heavy construction, no LLM, no embedding server."""
    r = ConversationRouter.__new__(ConversationRouter)
    r._turn_index = None  # M2b 4b2a05a7: _build_messages/index_turn/reset read it; None = memory-disabled (the __init__ default)
    r._semantic = SemanticMatcher(embedder=None)          # for _normalize_input
    r._howto = HowtoCorpus(embedder=None, data_dir=data_dir)  # keyword retrieval
    r._reference = None
    r._pending_action_offer = None
    r._conversation_history = []
    r._max_history = 20
    r._last_semantic_score = None
    return r


def _repo_explain_router():
    """_explain_router pinned to the in-repo ship-source corpus (box-independent)."""
    return _explain_router(data_dir=_REPO_HOWTO)


class ExplainPriorRegexTests(unittest.TestCase):
    def test_matches_instructional_phrasings(self):
        for q in (
            "how do I update my system",
            "how to install a package",
            "how do you restart a service",
            "what's the command to check disk space",
            "show me how to add a user",
            "teach me how to use pkm",
            "what is the best way to set up networking",
            "walk me through enabling the firewall",
        ):
            self.assertIsNotNone(_EXPLAIN_PRIOR_RE.search(q), q)

    def test_does_not_match_imperatives_or_state_queries(self):
        # Plain actions and system-state questions must NOT read as instructional.
        for q in (
            "install firefox",
            "remove htop",
            "restart networkmanager",
            "how much disk space do I have",
            "how long has it been running",
            "how's my memory looking",
            "what's my hostname",
            "is ssh running",
        ):
            self.assertIsNone(_EXPLAIN_PRIOR_RE.search(q), q)


class TryExplainTests(unittest.TestCase):
    def test_update_query_routes_to_curated_answer_and_offers_action(self):
        r = _explain_router()
        result, prior = r._try_explain("how do I update my system")
        self.assertTrue(prior)
        self.assertIsNotNone(result)
        self.assertEqual(result.source, "explain")
        self.assertIn("pkm", result.text)
        # explain-FIRST then OFFER: the offer is armed for next turn.
        self.assertIsNotNone(r._pending_action_offer)
        command, tool, _orig = r._pending_action_offer
        self.assertIn("pkm", command)
        self.assertEqual(tool, "manage_packages")
        self.assertIn("Want me to run", result.text)

    def test_uninstall_howto_is_explained_not_executed(self):
        r = _explain_router()
        result, prior = r._try_explain("how do I uninstall a package")
        self.assertTrue(prior)
        self.assertIsNotNone(result)
        self.assertIn("pkm remove", result.text)

    def test_plain_imperative_is_not_explain(self):
        # No lexical prior + not a strong match → falls through to normal routing.
        r = _explain_router()
        result, prior = r._try_explain("install firefox")
        self.assertFalse(prior)
        self.assertIsNone(result)
        self.assertIsNone(r._pending_action_offer)

    def test_search_howto_has_no_action_offer(self):
        r = _explain_router()
        result, _ = r._try_explain("how do I search for a package")
        self.assertIsNotNone(result)
        self.assertIn("pkm search", result.text)
        self.assertIsNone(r._pending_action_offer)  # parameterized cmd → no auto-offer

    def test_compound_instructional_is_taught_not_decomposed_v1(self):
        # v1 BOUNDARY DECISION — decided, code review 2026-06-27
        # (157f03e9/334d45e5): a COMPOUND instructional query ("how do I update my
        # system AND reboot") is treated as ONE teach. The lexical prior fires, so
        # the explain gate serves the curated answer for the matched how-to, and
        # prior=True suppresses P0 decomposition downstream (router._route_impl:
        # `if decomposition.needs_decomposition and not explain_prior`). Accepted
        # consequence for v1: the second clause is NOT split into its own action —
        # it fails in the SAFE direction (teach, never auto-act on either clause),
        # and compound-instructional is rare. This test LOCKS that behavior so the
        # decision is deliberate, not a silent gap: changing it must update this
        # assertion on purpose. Revisit only if real usage shows the dropped clause
        # matters, re-pinning whatever behavior we then pick.
        r = _explain_router()
        result, prior = r._try_explain("how do I update my system and reboot")
        self.assertTrue(prior)                        # prior present → decomposition suppressed
        self.assertIsNotNone(result)                  # taught as one how-to, not split into actions
        self.assertEqual(result.source, "explain")
        self.assertIn("pkm", result.text)             # the first-clause (update) how-to is served
        # Only the teach-offer is armed (explain-first) — never an auto-run of
        # either clause; the reboot clause produces no action of its own.
        self.assertIsNotNone(r._pending_action_offer)
        _cmd, tool, _orig = r._pending_action_offer
        self.assertEqual(tool, "manage_packages")

    def test_disabled_corpus_is_inert(self):
        r = _explain_router()
        r._howto = None
        self.assertEqual(r._try_explain("how do I update my system"), (None, False))


class PendingActionOfferTests(unittest.TestCase):
    def _router_with_offer(self):
        r = _explain_router()
        # The staged action declares tool=manage_packages, but its command is an
        # exact shell line — the seam fix must run THAT verbatim via run_command,
        # never re-route it through the matcher (which under the dispatch lockdown
        # would drop it) or re-derive it through manage_packages' arg-extractor
        # (which would run `pkm search "pkm upgrade"`).
        r._pending_action_offer = ("pkm upgrade", "manage_packages", "how do I update")
        # Capture the code-owned dispatch off the tool registry.
        r._dispatched = None  # (name, arguments) of the executed ToolCall

        class _FakeRegistry:
            def execute(_self, call, **kwargs):
                r._dispatched = (call.name, dict(call.arguments))
                from intergen.interfaces.types import ToolResult
                return ToolResult(
                    call_id="", name=call.name,
                    content=f"ran {call.arguments.get('command')}", success=True)

        r._tools = _FakeRegistry()
        r._ingress_tracker = None
        r._trust_state = None
        r._review_callback = None
        # Template synthesis hits (instant), so the LLM-synth path isn't needed.
        r._template_synthesis = lambda *a, **k: "Done."
        r._append_history = lambda *a, **k: None
        r._record = lambda result, t0, source: None
        return r

    def test_yes_runs_staged_command_verbatim_via_run_command(self):
        r = self._router_with_offer()
        result = r._resolve_pending_action_offer("yes please", 0.0)
        self.assertIsNotNone(result)
        # The EXACT staged command, dispatched code-owned via run_command — NOT
        # re-routed through the matcher/model, NOT re-derived via manage_packages.
        self.assertEqual(r._dispatched, ("run_command", {"command": "pkm upgrade"}))
        self.assertEqual(result.source, "explain_offer_run")
        self.assertIsNone(r._pending_action_offer)        # cleared

    def test_no_declines_without_dispatching(self):
        r = self._router_with_offer()
        result = r._resolve_pending_action_offer("no thanks", 0.0)
        self.assertIsNotNone(result)
        self.assertEqual(result.source, "explain_offer_declined")
        self.assertIsNone(r._dispatched)                  # nothing ran
        self.assertIsNone(r._pending_action_offer)

    def test_unrelated_reply_lapses_the_offer(self):
        r = self._router_with_offer()
        result = r._resolve_pending_action_offer("what time is it", 0.0)
        self.assertIsNone(result)                         # caller routes normally
        self.assertIsNone(r._dispatched)
        self.assertIsNone(r._pending_action_offer)        # lapsed

    def test_no_pending_offer_returns_none(self):
        r = _explain_router()
        self.assertIsNone(r._resolve_pending_action_offer("yes", 0.0))


class FreeUpMemoryTeachFirst(unittest.TestCase):
    """FACE polish: "free up my memory" is an ask to FREE memory, not a request
    for a usage read. Without a prior it was hijacked by the state-question
    route-to-tools fallback into a `free -h` USAGE dump (ask-vs-answer mismatch).
    The `free up` lexical prior + the system-free-up-memory corpus entry route it
    to the curated TEACH-FIRST answer, never an action — while pure state reads
    ("how much memory do I have") stay reads (no overcorrection). Corpus is pinned
    to the repo ship-source so this is box-independent."""

    def test_free_up_phrasings_reach_the_curated_teach_answer(self):
        for q in ("free up my memory", "free up memory", "how do I free up memory",
                  "how do I free up my memory", "free up RAM", "how do I free up RAM",
                  "how do I clear memory", "how do I reduce memory usage"):
            r = _repo_explain_router()
            result, prior = r._try_explain(q)
            self.assertTrue(prior, q)                          # the `free up` prior fires
            self.assertIsNotNone(result, q)
            self.assertEqual(result.source, "explain", q)
            # the curated free-up answer, not the `free -h` usage read: it teaches
            # the available-vs-free nuance and is honest that no command frees RAM.
            self.assertIn("available", result.text, q)
            self.assertIn("System Monitor", result.text, q)
            self.assertIn("frees RAM", result.text, q)         # the honest "no magic command" line
            # TEACH-ONLY: never an auto-run offer (the entry carries no action).
            self.assertIsNone(r._pending_action_offer, q)

    def test_free_up_memory_does_not_dispatch_free_h(self):
        # The exact regression: the answer must not be the usage dump — no armed
        # `free -h` offer and the curated teaching text is served instead.
        r = _repo_explain_router()
        result, _ = r._try_explain("free up my memory")
        self.assertNotIn("free -h", str(r._pending_action_offer))
        self.assertIn("disk cache", result.text)

    def test_state_reads_are_not_overcorrected_into_teaching(self):
        # A pure "how much / how's my" memory STATE read must NOT reach the explain
        # gate (no prior, below the strong-match threshold) — it falls through to
        # the deterministic state dispatch unchanged.
        for q in ("how much memory do I have", "what's my memory usage",
                  "how much ram do i have", "how is my memory looking"):
            r = _repo_explain_router()
            result, prior = r._try_explain(q)
            self.assertFalse(prior, q)
            self.assertIsNone(result, q)

    def test_prior_regex_marks_free_up_but_not_state_reads(self):
        for q in ("free up my memory", "free up memory", "free up RAM",
                  "free up disk space"):
            self.assertIsNotNone(_EXPLAIN_PRIOR_RE.search(q), q)
        for q in ("how much memory do I have", "how's my memory looking",
                  "what's my memory usage"):
            self.assertIsNone(_EXPLAIN_PRIOR_RE.search(q), q)


if __name__ == "__main__":
    unittest.main()
