# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Every consent surface describes the command the tool builds, through route().

THE DEFECT, measured over every call shape the review card can be raised for:
each surface that describes a held act composed its own text from the call's
raw arguments, apart from the code that builds the command. An update card
promised installed updates over `pkm update`, a refresh only (with a package
named, a line pkm's parser rejects); --yes and --user ran unshown; "ssh" was on
a card for sshd; the handoff's sudo reached its first command only; the zenity
body and the console line showed the argument dict; a user-mode service change
was sent through the privileged runner, where --user reaches root's manager;
and calls that would be refused were refused after the person approved them.

THE RULE these cases hold: the surfaces render one description derived from
the argv the tool's own builder returns for the call (tool_registry
.describe_call); an approved act runs where its card says (the privileged
runner for a root act, the person's own process for a user-manager act); a call
that will be refused is refused before any card, with its reason.

Each case is one turn through route() on the locked floor and the unlocked one.
The review card is recorded and approved; the privileged runner is a stand-in
that records what it was handed; every process start is recorded and answered
without running, so nothing on this machine is changed.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from intergen import review_modal, tool_registry
from intergen.consent_dialog_sanitize import describe_gate_action
from intergen.howto import HowtoCorpus
from intergen.intents import register_all_intents
from intergen.interfaces.hardware import HardwareTierLevel
from intergen.interfaces.provenance import Provenance
from intergen.interfaces.scanner import ScanDisposition, ScanVerdict
from intergen.interfaces.types import ToolCall, ToolResult
from intergen.llm import LLMRouter
from intergen.router import ConversationRouter
from intergen.semantic import SemanticMatcher
from intergen.tool_registry import ToolRegistry
from intergen.tools.manage_packages import ManagePackagesTool

CORPUS = Path(__file__).resolve().parents[1] / "data" / "howto"


class _FlagIngress:
    """Flags every result coming back, so a tool whose card follows its run
    (an ingress tool) shows that card."""

    def scan(self, text, context):
        if getattr(getattr(context, "direction", None), "value", "") == "ingress":
            return ScanVerdict(disposition=ScanDisposition.FLAG, reason="case")
        return ScanVerdict(disposition=ScanDisposition.ALLOW)


class _Case(unittest.TestCase):
    """A router as the daemon builds one, on a floor; one turn through it."""

    def _router(self, *, locked: bool, model_calls=()) -> ConversationRouter:
        registry = ToolRegistry()
        registry.discover_tools()
        services = registry.get_tool("manage_services")
        services._read_service_unit = (
            lambda name, user_mode=False: ("known", name, ""))
        matcher = SemanticMatcher(embedder=None)
        register_all_intents(matcher)
        llm = LLMRouter(config=None)
        queued = list(model_calls)

        def _stream_with_tools(messages, *, tools=(), **kw):
            if queued:
                yield queued.pop(0)
            else:
                yield "(the model's answer)"

        llm.chat = lambda messages, **kw: SimpleNamespace(
            text="(the model's answer)", semantic_flags=[], quality_passed=True,
            local=True, tokens_prompt=0, tokens_completion=0, escalated=False,
            model="stub")
        llm.stream_with_tools = _stream_with_tools
        llm.stream = lambda *a, **kw: iter(["(the model's answer)"])
        llm.continue_after_tool_call = lambda *a, **kw: SimpleNamespace(
            text="(the model's answer)", semantic_flags=[], quality_passed=True,
            local=True, tokens_prompt=0, tokens_completion=0, escalated=False,
            model="stub")
        with mock.patch.dict(os.environ, {"INTERGEN_HOWTO_DIR": str(CORPUS)}):
            router = ConversationRouter(
                tool_registry=registry, semantic_matcher=matcher, llm=llm,
                hardware_tier=HardwareTierLevel.TIER_2, lock_dispatch=locked)
        router._howto = HowtoCorpus(embedder=None, data_dir=CORPUS)
        router._current_query_type = "general"
        router._filler = None
        self.cards: list[dict] = []
        self.runner: list[dict] = []
        self.started: list[list[str]] = []

        def _review(call, decision):
            what, command = tool_registry.card_action_description(call)
            self.cards.append({
                "call": call, "what": what, "command": command,
                "zenity": review_modal._format_modal_body(
                    call, decision, "", "", ""),
                "headline": describe_gate_action(call.name, call.arguments),
                "needs_pkexec": decision.needs_pkexec})
            return "allow_once"

        self.review = _review
        self.router = router
        return router

    def _turn(self, router, text, *, scanner=None):
        def _runner(call, tool_name, arguments, dispatch_token=None):
            self.runner.append(dict(arguments))
            return ToolResult(call_id=call.call_id, name=tool_name,
                              content="(stood in)", success=True)

        def _start(args, *a, **kw):
            argv = [str(x) for x in args]
            if argv[:1] != ["gpgv"]:
                self.started.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")

        router._tools._scanner = scanner
        with mock.patch("subprocess.run", _start), \
                mock.patch("intergen.tool_registry.write_record",
                           lambda *a, **kw: None), \
                mock.patch("intergen.tool_registry.mint_token",
                           lambda *a, **kw: "token"), \
                mock.patch.object(router._tools, "_dispatch_via_pkexec", _runner):
            return router.route(text, review_callback=self.review)

    @staticmethod
    def _ran(started, program):
        """The recorded starts of one program (the turn's own reads, run
        through the tool unit wrapper, are other programs)."""
        return [argv for argv in started
                if any(os.path.basename(word) == program for word in argv[:1])
                or (argv[:1] == ["/usr/bin/systemd-run"]
                    and any(os.path.basename(w) == program for w in argv))]

    def assertCardIsTheBuilders(self, card, commands):
        line = " && ".join(shlex.join(argv) for argv in commands)
        self.assertEqual(card["command"], line)
        self.assertIn(f"Command:     {line}", card["zenity"])
        self.assertNotIn("Arguments:", card["zenity"])
        self.assertEqual(card["headline"], card["what"])


class TheUpdateFamilyTests(_Case):
    """An update is the refresh then the upgrade, under one approval, on both
    floors: the card shows both commands the builder returns, and the runner is
    handed exactly the call the card described."""

    def _update(self, text, arguments):
        for locked in (True, False):
            with self.subTest(locked=locked, text=text):
                router = self._router(locked=locked)
                self._turn(router, text)
                self.assertEqual(len(self.cards), 1, self.cards)
                card = self.cards[0]
                self.assertEqual(card["call"].arguments, arguments)
                commands = ManagePackagesTool().build_commands(arguments)
                self.assertEqual([argv[1] for argv in commands],
                                 ["sync", "upgrade"])
                self.assertCardIsTheBuilders(card, commands)
                self.assertTrue(card["needs_pkexec"])
                self.assertEqual(self.runner, [arguments])
                self.assertEqual(self.started, [])

    def test_the_system_update_runs_the_refresh_then_every_upgrade(self):
        self._update("update my system", {"action": "update"})

    def test_a_package_update_runs_the_refresh_then_its_upgrade(self):
        self._update("update htop", {"action": "update", "package": "htop"})


# A turn the router hands to the model's tool turn on the unlocked floor (no
# code-owned route answers it); the model's call is the case's own.
MODEL_TURN = "my sound is broken, please fix it"


class TheModelsCallsTests(_Case):
    """The model's own calls, on the unlocked floor; the locked floor never
    lets the model choose a call, so the same turn dispatches none there."""

    def _model_turn(self, call, text, *, scanner=None):
        results = {}
        for locked in (True, False):
            router = self._router(locked=locked, model_calls=[call])
            self._turn(router, text, scanner=scanner)
            results[locked] = (list(self.cards), list(self.runner),
                               list(self.started))
        return results

    def test_a_user_manager_act_runs_as_the_person_and_its_card_says_user(self):
        call = ToolCall(name="manage_services",
                        arguments={"action": "restart", "service": "pipewire",
                                   "user_mode": True},
                        source_of_request=Provenance.USER_DIRECT)
        results = self._model_turn(call, MODEL_TURN)
        cards, runner, started = results[False]
        self.assertEqual(len(cards), 1, cards)
        self.assertCardIsTheBuilders(
            cards[0], [["systemctl", "--user", "restart", "pipewire"]])
        self.assertFalse(cards[0]["needs_pkexec"])
        self.assertIn("your own", cards[0]["what"])
        # Approved, it ran in this process on the person's own manager — the
        # privileged runner was handed nothing.
        self.assertEqual(runner, [])
        self.assertEqual(self._ran(started, "systemctl"),
                         [["systemctl", "--user", "restart", "pipewire"]])
        for card in results[True][0]:
            self.assertNotEqual(card["call"].arguments.get("user_mode"), True)

    def test_a_dead_verb_is_refused_before_any_card_with_its_reason(self):
        call = ToolCall(name="manage_packages", arguments={"action": "upgrade"},
                        source_of_request=Provenance.USER_DIRECT)
        router = self._router(locked=False, model_calls=[call])
        with mock.patch.object(router._tools, "_audit_log_refusal",
                               wraps=router._tools._audit_log_refusal) as refusal:
            self._turn(router, MODEL_TURN)
        self.assertEqual(self.cards, [])
        self.assertEqual((self.runner, self._ran(self.started, "pkm")), ([], []))
        reasons = [c.args[2] for c in refusal.call_args_list]
        self.assertEqual(len(reasons), 1, reasons)
        self.assertIn("upgrade", reasons[0])
        # On the code-owned floor the word is taught and offered as the one
        # act it means: the package tool's update, its builder's line shown.
        # The teach parser offers only a name the package index resolves; the
        # index is stood in for, resolving htop.
        locked = self._router(locked=True)
        with mock.patch.object(type(locked), "_package_in_index",
                               lambda self, name: name == "htop"):
            result = self._turn(locked, "how do i upgrade htop")
        arguments = {"action": "update", "package": "htop"}
        line = " && ".join(shlex.join(argv) for argv in
                           ManagePackagesTool().build_commands(arguments))
        self.assertEqual(locked._conv.pending_action_offer,
                         (line, "manage_packages", "how do i upgrade htop",
                          arguments))
        self.assertIn(f"`{line}`", result.text)

    def test_a_call_that_would_be_refused_raises_no_card(self):
        call = ToolCall(name="run_command",
                        arguments={"command": "pkm sync && pkm upgrade"},
                        source_of_request=Provenance.USER_DIRECT)
        results = self._model_turn(call, MODEL_TURN)
        for locked, (cards, runner, started) in results.items():
            with self.subTest(locked=locked):
                self.assertEqual(
                    [c for c in cards if c["call"].name == "run_command"], [])
                self.assertEqual(runner, [])
                self.assertEqual(self._ran(started, "pkm"), [])

    def test_a_tool_with_no_command_line_shows_a_sentence_not_its_arguments(self):
        call = ToolCall(name="read_file", arguments={"path": "/etc/hostname"},
                        source_of_request=Provenance.USER_DIRECT)
        results = self._model_turn(call, MODEL_TURN, scanner=_FlagIngress())
        cards = [c for c in results[False][0] if c["call"].name == "read_file"]
        self.assertEqual(len(cards), 1, results[False][0])
        self.assertEqual(cards[0]["command"], "Read the file /etc/hostname.")
        self.assertEqual(cards[0]["what"], cards[0]["command"])
        self.assertNotIn("{", cards[0]["zenity"])
        self.assertIn("Action:      Read the file /etc/hostname.",
                      cards[0]["zenity"])


class TheTeachLineTests(_Case):
    """A package action taught in words hands the person the registry's own
    terminal line for that act (tool_registry.handoff_command): sudo on every
    command that runs as root, without the --yes a person at a terminal
    answers. Before, the teach map printed a bare pkm line with no sudo."""

    def test_the_taught_line_is_the_registrys_on_both_floors(self):
        for text, arguments, verb, app in (
                ("how do i upgrade htop",
                 {"action": "update", "package": "htop"}, "update", "htop"),
                ("how do i install htop",
                 {"action": "install", "package": "htop"}, "install", "htop"),
                ("how do i remove htop",
                 {"action": "remove", "package": "htop"}, "remove", "htop")):
            line = tool_registry.handoff_command(ToolCall(
                name="manage_packages", arguments=arguments,
                source_of_request=Provenance.USER_DIRECT))
            self.assertTrue(line.startswith("sudo "), line)
            self.assertNotIn("--yes", line)
            for locked in (True, False):
                with self.subTest(text=text, locked=locked):
                    # The teach parser offers only a name the package index
                    # resolves; the index is stood in for, resolving htop.
                    router = self._router(locked=locked)
                    with mock.patch.object(type(router), "_package_in_index",
                                           lambda self, name: name == "htop"):
                        result = self._turn(router, text)
                    self.assertIn(f"To {verb} {app}, run `{line}`.",
                                  result.text)
                    self.assertEqual((self.cards, self.runner), ([], []))

class TheOffersTests(_Case):
    """An offered pkm line is staged as the package tool's call and shows that
    tool's builder's line; an offer whose call would be refused is not made."""

    def test_an_offer_whose_call_is_refused_is_not_made(self):
        for locked in (True, False):
            with self.subTest(locked=locked):
                router = self._router(locked=locked)
                text = router._stage_action_offer_or_handoff(
                    "sudo pkm verify --all", "manage_packages", "verify")
                self.assertEqual(text, "")
                self.assertIsNone(router._conv.pending_action_offer)

    def test_an_accepted_update_offer_runs_the_builders_commands(self):
        for locked in (True, False):
            with self.subTest(locked=locked):
                router = self._router(locked=locked)
                offer = router._stage_action_offer_or_handoff(
                    "sudo pkm sync && sudo pkm upgrade --all",
                    "manage_packages", "how do I update my system")
                commands = ManagePackagesTool().build_commands(
                    {"action": "update"})
                line = " && ".join(shlex.join(argv) for argv in commands)
                self.assertIn(f"`{line}`", offer)
                self._turn(router, "yes")
                self.assertEqual(len(self.cards), 1, self.cards)
                self.assertCardIsTheBuilders(self.cards[0], commands)
                self.assertEqual(self.runner, [{"action": "update"}])
                self.assertEqual(self.started, [])


if __name__ == "__main__":
    unittest.main()
