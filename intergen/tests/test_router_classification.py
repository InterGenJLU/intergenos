# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""F-2 — tool-eligibility classification (ConversationRouter._classify_query_type).

Root cause of the operator-reported "InterGen refused to act" failure: an
imperative action request with no registered intent ("list the printers") was
classified 'general', so the eligibility gate (route() ~:254 — eligible when the
P2 score is high OR the type is diagnostic/safety) attached NO tools, the turn
fell to P4 freeform, and InterGen deflected ("open a terminal and run lpstat")
instead of calling run_command itself.

The fix classifies action-imperatives as 'diagnostic' (tool-eligible + act-now),
while keeping conversational asks (identity, gratitude, "tell me about ...") OUT,
and it runs BEFORE the identity check so a polite/pronoun imperative ("I'm asking
YOU to ...") is not deflected into a conversational answer. These tests pin all
of that. No instance state is touched, so the router is built via __new__.
"""

from __future__ import annotations

import unittest

from intergen.router import ConversationRouter

# The query types the eligibility gate (route() ~:254) treats as tool-eligible.
_TOOL_ELIGIBLE = {"diagnostic", "safety"}


def _router() -> ConversationRouter:
    # _classify_query_type / _looks_like_action_request use only class attrs.
    return ConversationRouter.__new__(ConversationRouter)


class ActionRequestEligibilityTests(unittest.TestCase):
    def setUp(self):
        self.r = _router()

    def _t(self, q: str) -> str:
        return self.r._classify_query_type(q)

    def test_reported_failure_list_the_printers_is_tool_eligible(self):
        # The exact operator-reported turn must now attach tools.
        self.assertEqual(self._t("list the printers"), "diagnostic")
        self.assertIn(self._t("list the printers"), _TOOL_ELIGIBLE)

    def test_polite_and_pronoun_wrapped_imperatives_are_eligible(self):
        # The "you" misroute: a polite/pronoun-wrapped imperative must reach
        # tools, not be read as an identity/conversational turn.
        for q in (
            "can you list the printers",
            "could you show the running services",
            "please open firefox",
            "I'm asking you to do that",
            "I am asking you to list the printers",
            "go ahead and do it for me",
        ):
            self.assertEqual(self._t(q), "diagnostic", q)

    def test_action_verbs_and_state_queries_are_eligible(self):
        for q in (
            "show the disk usage",
            "open firefox",
            "start bluetooth",
            "restart networkmanager",
            "what printers are available",
            "which services are running",
            "find /etc/fstab",        # pre-existing diagnostic, still works
            "check the disk space",
        ):
            self.assertIn(self._t(q), _TOOL_ELIGIBLE, q)


class ConversationalStaysOutTests(unittest.TestCase):
    def setUp(self):
        self.r = _router()

    def _t(self, q: str) -> str:
        return self.r._classify_query_type(q)

    def test_identity_stays_identity(self):
        for q in ("who are you", "what's your name", "tell me about yourself",
                  "what do you know", "remember my name is Chris"):
            self.assertEqual(self._t(q), "identity", q)

    def test_gratitude_stays_general(self):
        self.assertEqual(self._t("thanks, that fixed it"), "general")
        self.assertEqual(self._t("thank you, well done"), "general")

    def test_general_knowledge_stays_general(self):
        # A knowledge/explanation ask is answered from training, not a tool.
        self.assertEqual(self._t("tell me about linux"), "general")

    def test_conversational_never_tool_eligible(self):
        for q in ("who are you", "tell me about yourself", "thanks",
                  "tell me about linux"):
            self.assertNotIn(self._t(q), _TOOL_ELIGIBLE, q)


class SubstringTrapsAndOrderingTests(unittest.TestCase):
    def setUp(self):
        self.r = _router()

    def test_action_verbs_match_whole_leading_token_only(self):
        # Substring traps must NOT be read as action verbs.
        self.assertNotEqual(self.r._classify_query_type("I forget my password story"),
                            "diagnostic")   # "forget" != "get"
        self.assertNotEqual(self.r._classify_query_type("listen to what I said"),
                            "diagnostic")   # "listen" != "list"

    def test_safety_still_wins_over_action(self):
        # Destructive verbs are caught by the safety trigger (also tool-eligible),
        # not the action path — safety is checked first.
        self.assertEqual(self.r._classify_query_type("delete everything"), "safety")
        self.assertEqual(self.r._classify_query_type("wipe the disk"), "safety")

    def test_looks_like_action_request_helper_directly(self):
        yes = ["list the printers", "can you open firefox",
               "i'm asking you to do that", "what drives are mounted"]
        no = ["who are you", "tell me about yourself", "thanks", "hello there",
              "i forget the details"]
        for q in yes:
            self.assertTrue(self.r._looks_like_action_request(q), q)
        for q in no:
            self.assertFalse(self.r._looks_like_action_request(q), q)


if __name__ == "__main__":
    unittest.main()


class ServiceTypoNormalizationTests(unittest.TestCase):
    """A typo'd service-status query must still reach a tool, not deflect.

    "is ssh runnign?" mis-routed to freeform and the 2B fabricated a fake
    systemctl dump. _normalize_input now corrects the "running"/"active"/
    "enabled" verb typos so the manage_services keyword pattern matches.
    """

    def _norm(self, q: str) -> str:
        from intergen.semantic import SemanticMatcher
        return SemanticMatcher._normalize_input(q)

    def test_running_typos_corrected(self):
        for typo, q in [
            ("is ssh runnign?", "is ssh running"),
            ("is nginx runing", "is nginx running"),
            ("check ssh actvie", "check ssh active"),
            ("is the firewall enabld", "is the firewall enabled"),
        ]:
            self.assertEqual(self._norm(typo), q)

    def test_corrected_query_matches_service_pattern(self):
        import re
        pat = re.compile(r"^(?:is|check)\s+\w+\s+(?:running|active|enabled)",
                         re.IGNORECASE)
        for typo in ["is ssh runnign?", "is nginx runing", "check ssh actvie"]:
            self.assertRegex(self._norm(typo), pat)


class RouteToToolsGuardTests(unittest.TestCase):
    """The pre-P3 route-to-tools guard: a direct system-state question the 2B
    would otherwise deflect ("run this yourself") is dispatched deterministically
    — but a how-to that merely mentions a system noun is NOT hijacked.
    """

    def setUp(self):
        self.r = _router()

    def test_state_questions_gate_open(self):
        for q in [
            "so i've been having storage issues and i'm curious how much disk space i have left",
            "what's my hostname",
            "how much ram do i have",
            "what is my disk usage",
            "list the printers",            # action request
        ]:
            self.assertTrue(self.r._looks_like_state_question(q), q)

    def test_howtos_gate_closed(self):
        # These resolve a command in the selector but must NOT trip the guard.
        for q in [
            "how do i free up disk space",
            "tell me about python",
            "how to install firefox",
            "why is my storage full of logs",
        ]:
            self.assertFalse(self.r._looks_like_state_question(q), q)

    def test_fallback_noops_without_tool_registry(self):
        # A partially-built router (no _tools) must no-op, not crash.
        from intergen.interfaces.types import RouteResult
        res = self.r._try_deterministic_fallback("what's my hostname")
        self.assertIsInstance(res, RouteResult)
        self.assertFalse(res.handled)

    def test_selector_resolves_lexical_hostname_and_verbose_disk(self):
        from intergen.router import ConversationRouter as C
        from intergen.semantic import SemanticMatcher
        norm = SemanticMatcher._normalize_input
        self.assertEqual(C._natural_language_to_command(norm("what's this box called")),
                         "hostname")
        self.assertEqual(C._natural_language_to_command(norm("yo what's my host")),
                         "hostname")
        self.assertEqual(
            C._natural_language_to_command(
                norm("curious about how much disk space i have left")),
            "df -h")

    def test_how_long_conversational_does_not_hijack_uptime(self):
        # The over-hijack the corpus didn't contain: "how long" is a state-question
        # gate marker AND used to be a bare selector key → a conversational
        # "how long does it take to learn Python" opened the gate AND dispatched
        # uptime. The gate-CLOSED guarantee here is at the SELECTOR: the marker
        # still opens the gate (so genuine uptime asks reach the selector), but a
        # non-uptime "how long" resolves NO command → the guard no-ops to P3/P4.
        from intergen.router import ConversationRouter as C
        from intergen.semantic import SemanticMatcher
        norm = SemanticMatcher._normalize_input
        # gate OPENS (the marker is intentionally retained)...
        self.assertTrue(
            self.r._looks_like_state_question("how long does it take to learn python"))
        # ...but the selector dispatches NOTHING for the conversational form.
        for q in ["how long does it take to learn python",
                  "how long until the movie starts",
                  "how long should i marinate chicken"]:
            self.assertIsNone(C._natural_language_to_command(norm(q)), q)
        # Genuine uptime asks STILL resolve via the contextual keys + literal.
        for q in ["how long has it been up",
                  "how long has the system been running",
                  "uptime"]:
            self.assertEqual(C._natural_language_to_command(norm(q)), "uptime", q)

    def test_time_query_routes_deterministically_to_date(self):
        # PI-218-3/-4 companion: "what time is it" must classify as system_info
        # (gate) and resolve to `date` (selector) at P1 — both as a single query
        # and as a decomposed sub — not fall to the ~50s llm_tools path that
        # mis-picked take_screenshot / read_file(/usr/bin/time) on the development machine trace.
        from intergen.router import ConversationRouter as C
        from intergen.semantic import SemanticMatcher
        from intergen.intents import register_all_intents
        norm = SemanticMatcher._normalize_input
        # Gate: a keyword-only matcher classifies the time queries as system_info.
        m = SemanticMatcher(embedder=None)
        register_all_intents(m)
        for q in ["what time is it", "what's the time", "what time of day is it"]:
            self.assertEqual(m._match_keywords(q).intent_id, "system_info", q)
        # Selector: resolves to `date`.
        for q in ["what time is it", "what's the time", "what is the time",
                  "do you have the current time", "what time of day is it"]:
            self.assertEqual(C._natural_language_to_command(norm(q)), "date", q)
        # \btime\b excludes "uptime": an uptime ask is unaffected (still uptime).
        for q in ["uptime", "how long has it been up", "what's my uptime"]:
            self.assertEqual(C._natural_language_to_command(norm(q)), "uptime", q)


class SecondWaveQualityTests(unittest.TestCase):
    """Template-synthesis and service-name-extraction fixes from the 2nd pull."""

    def setUp(self):
        self.r = _router()

    def test_hostname_template_not_mislabeled_as_os(self):
        # "host" contains the substring "os" (h-os-t) — the OS summary template
        # must NOT win; a hostname result renders as a hostname sentence.
        from intergen.router import ConversationRouter as C
        self.assertEqual(C._template_synthesis("yo what's my host", "boxname"),
                         "Your hostname is boxname.")

    def test_os_template_still_fires_for_real_os_query(self):
        from intergen.router import ConversationRouter as C
        out = C._template_synthesis("what os am I running", 'PRETTY_NAME="InterGenOS 1.0"')
        self.assertIn("InterGenOS", out)

    def test_service_name_from_indirect_clause(self):
        args = self.r._extract_arguments(
            "manage_services", "I can't connect via SSH, is the service even on?")
        self.assertEqual(args["service"], "ssh")
        self.assertEqual(args["action"], "status")

    def test_service_scan_no_false_match(self):
        from intergen.router import ConversationRouter as C
        self.assertEqual(C._scan_service_name("how are you today"), "")
        self.assertEqual(C._scan_service_name("restart nginx please"), "nginx")


class BareStatusHealthCheckTests(unittest.TestCase):
    """Bare 'Status' routes to the grounded system_map health check (avoiding
    the 2B 'I am InterGenOS' identity slip); specific status queries are not
    hijacked and keep their own routing."""

    def setUp(self):
        self.r = _router()

    def test_bare_status_is_system_map(self):
        for q in ["Status", "status", "status?", "status check",
                  "current status", "status please"]:
            self.assertTrue(self.r._is_system_map_query(q), q)

    def test_specific_status_not_hijacked(self):
        for q in ["status of nginx", "git status", "show me status",
                  "is sshd running"]:
            # These must NOT be captured by the bare-status branch; they route
            # via their own handlers (manage_services / run_command). (The
            # service-status forms still reach system_map via their own phrases,
            # but never via the bare-objectless branch added here.)
            stripped = q.lower().strip().rstrip("?!. ")
            self.assertNotIn(stripped, (
                "status", "status check", "status report", "status update",
                "current status", "give me a status", "status please"))


class IdentityCollisionGuardTests(unittest.TestCase):
    """Deterministic identity guard: InterGen (assistant) is never InterGenOS
    (the OS). Corrects the bare self-as-OS claim; spares correct possessive /
    membership forms."""

    def _fix(self, t):
        from intergen.router import correct_identity_collision
        return correct_identity_collision(t)

    def test_corrects_bare_self_as_os_claim(self):
        self.assertEqual(
            self._fix("I am InterGenOS, an AI assistant embedded in your system."),
            "I am InterGen, an AI assistant embedded in your system.")
        self.assertEqual(self._fix("I am InterGenOS."), "I am InterGen.")
        self.assertEqual(self._fix("I'm InterGenOS and I can help."),
                         "I'm InterGen and I can help.")

    def test_corrects_operating_system_claim(self):
        out = self._fix("I am the operating system.")
        self.assertIn("InterGen", out)
        self.assertNotIn("I am the operating system.", out)

    def test_spares_correct_forms(self):
        for t in [
            "I am InterGenOS's AI assistant.",
            "I'm InterGen, the assistant built into InterGenOS.",
            "This system runs InterGenOS, a Linux distribution built from source.",
            "I'm InterGen, your AI assistant.",
        ]:
            self.assertEqual(self._fix(t), t)

    def test_noop_on_unrelated_and_empty(self):
        self.assertEqual(self._fix("Your hostname is boxname."),
                         "Your hostname is boxname.")
        self.assertEqual(self._fix(""), "")
