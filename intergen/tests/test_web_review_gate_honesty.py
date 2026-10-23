# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Web review-gate honesty wave — the deterministic units.

Covers the pieces of the single-gate / honest-handoff / loop-killer / one-source
capability work that are provable without a live model or a real box:

  * the 3-part honest handoff builder (name the action / why it can't proceed
    here / the exact command) — no "blocked", no gate/tool jargon;
  * the user-language classification translator (never the raw enum label);
  * the web review-callback BRIDGE — fail-closed on every branch, and the
    forbidden / hard-deny parity pre-check the fast/offer path relies on;
  * the streaming gate-refusal message (honest handoff for a real state-change,
    a plain refusal for a hard-BLOCKED destructive command — never "run it
    yourself" for the thing the block exists to stop);
  * the router loop-killer — a declined offer is remembered and not re-armed as
    fresh, and its deny renders the honest handoff, not the raw "denied";
  * the ONE interface-aware capability registry — the router phrase table and
    the model's system-prompt guard both read from it (no drift).

The handoff, refusal and guard cells used to pin the command line they showed
("pkm sync && pkm upgrade") against itself, so they passed while that line was
not what the package tool ran (it built `pkm update`, a refresh only) and the
handoff's sudo reached the first command only. Each now checks a surface
against the argv the tool's own builder returns (BuilderAgainstSurfaceTests:
every shown row).
"""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import tempfile
import unittest
from unittest import mock

from intergen.interfaces.types import ToolCall, ToolResult
from intergen.interfaces.provenance import (IngressTracker, Provenance,
                                            ToolRiskTier)
from intergen import tool_registry
from intergen import capability_registry
from intergen.tools.manage_packages import ManagePackagesTool
from intergen.tools.manage_services import ManageServicesTool


def _update_commands(package: str = "", pkm: str | None = None):
    """The package tool's own builder's commands for an update."""
    arguments = {"action": "update", "package": package} if package else {
        "action": "update"}
    return ManagePackagesTool().build_commands(arguments, pkm=pkm)


def _terminal(commands, root: bool) -> str:
    """The builder's commands as a person types them: --yes dropped, sudo on
    every command that runs as root."""
    lines = []
    for argv in commands:
        line = shlex.join([w for w in argv if w != "--yes"])
        lines.append(f"sudo {line}" if root else line)
    return " && ".join(lines)


# ── The honest handoff + classification translator (user-language classification / the honest-handoff design) ──────────
class HonestHandoffTests(unittest.TestCase):
    def test_admin_handoff_has_sudo_command_and_no_jargon(self):
        # The builder's update, two commands: sudo reaches both, so the
        # upgrade does not run without root.
        line = " && ".join(" ".join(argv) for argv in _update_commands(pkm="pkm"))
        msg = tool_registry.honest_handoff_message(
            "Refresh the package index, then upgrade every package.", line,
            needs_admin=True)
        self.assertIn("administrator approval", msg)
        self.assertIn(
            "`" + " && ".join("sudo " + " ".join(argv)
                              for argv in _update_commands(pkm="pkm")) + "`",
            msg)
        # No internal jargon anywhere in a user-facing handoff.
        low = msg.lower()
        for banned in ("blocked", "safety layer", "gate", "provenance",
                       "privileged_state_changing"):
            self.assertNotIn(banned, low, f"handoff leaked jargon: {banned!r}")

    def test_non_admin_handoff_has_no_sudo(self):
        msg = tool_registry.honest_handoff_message(
            "", "systemctl reload foo", needs_admin=False)
        self.assertNotIn("sudo", msg)
        self.assertIn("`systemctl reload foo`", msg)

    def test_empty_command_omits_command_block(self):
        msg = tool_registry.honest_handoff_message("A change.", "", needs_admin=True)
        self.assertNotIn("`", msg)          # no command block
        self.assertIn("administrator approval", msg)

    def test_classification_sentence_never_raw_label(self):
        for tier in ToolRiskTier:
            s = tool_registry.classification_sentence(tier)
            self.assertNotIn("_", s)         # no snake_case enum label leaked
            self.assertTrue(s.endswith("."))
        self.assertTrue(tool_registry.tier_needs_admin(
            ToolRiskTier.PRIVILEGED_STATE_CHANGING))
        self.assertFalse(tool_registry.tier_needs_admin(
            ToolRiskTier.USER_SCOPE_STATE_CHANGING))


# ── The web review-callback bridge (single-gate review) ────────────────────────────────────
class _RaisingTools:
    def get_tool(self, name):
        raise RuntimeError("boom")   # provoke the bridge's fail-closed path


class _NoTools:
    def get_tool(self, name):
        return None


class WebReviewBridgeTests(unittest.TestCase):
    def _server(self, tools):
        from intergen.web_server import WebServer
        s = WebServer()
        s._tools = tools
        s._governance = None
        return s

    def _ctx(self, server):
        from intergen.web_server import ConnectionContext
        return ConnectionContext(client_id="t", source_interface="web", ws=object())

    def test_bridge_fails_closed_on_exception(self):
        # ANY exception in the bridge → a failed review, never a silent allow
        # (fail-closed), and never the person's "deny": nobody refused anything.
        from intergen.tool_registry import REVIEW_FAILED
        server = self._server(_RaisingTools())
        cb = server._make_web_review_callback(self._ctx(server), "turn1", object())
        call = ToolCall(name="manage_packages",
                        arguments={"action": "update"},
                        source_of_request=Provenance.USER_DIRECT)
        self.assertEqual(cb(call, None), REVIEW_FAILED)

    def test_bridge_denies_forbidden_action_before_any_card(self):
        # Parity: a forbidden Z3 action fails closed at the bridge (the fast/offer
        # path's substitute for the streaming :1752 forbidden pre-check) — and it
        # returns BEFORE ever scheduling a card. Stub the scheduler so a card
        # render would be observable if it (wrongly) reached one.
        from intergen import zones
        server = self._server(_NoTools())
        cb = server._make_web_review_callback(self._ctx(server), "turn1", object())
        # Find a call zones.forbidden_reason actually refuses (self-substrate /
        # owner_only write). Skip cleanly if the zone set does not cover one here.
        candidates = [
            ToolCall(name="write_file",
                     arguments={"path": "/etc/intergen/governance.json",
                                "content": "x"},
                     source_of_request=Provenance.USER_DIRECT),
            ToolCall(name="modify_governance", arguments={"autonomy_tier": 5},
                     source_of_request=Provenance.USER_DIRECT),
        ]
        forbidden = next(
            (c for c in candidates
             if zones.forbidden_reason(c.name, c.arguments)), None)
        if forbidden is None:
            self.skipTest("no forbidden-zone call available in this build")
        scheduled = {"n": 0}
        orig = asyncio.run_coroutine_threadsafe
        try:
            def _spy(coro, loop):
                scheduled["n"] += 1
                coro.close()
                raise AssertionError("forbidden action reached the card")
            asyncio.run_coroutine_threadsafe = _spy
            # Refused, and recorded as refused without review: no card was
            # raised, so no person denied it.
            from intergen.tool_registry import REFUSED_WITHOUT_REVIEW
            self.assertEqual(cb(forbidden, None), REFUSED_WITHOUT_REVIEW)
            self.assertEqual(scheduled["n"], 0, "forbidden action was not pre-denied")
        finally:
            asyncio.run_coroutine_threadsafe = orig

    def test_bridge_verdict_mapping(self):
        # approved → allow_once, denied → deny — with the coroutine scheduling
        # stubbed so no live loop is needed.
        server = self._server(_NoTools())
        cb = server._make_web_review_callback(self._ctx(server), "turn1", object())
        call = ToolCall(name="manage_packages",
                        arguments={"action": "update"},
                        source_of_request=Provenance.USER_DIRECT)

        class _Fut:
            def __init__(self, v=None, e=None):
                self._v, self._e = v, e
            def result(self, timeout=None):
                if self._e:
                    raise self._e
                return self._v

        orig = asyncio.run_coroutine_threadsafe
        try:
            for verdict, expected in (("approved", "allow_once"),
                                      ("denied", "deny")):
                def _fake(coro, loop, _v=verdict):
                    coro.close()             # avoid un-awaited-coroutine warning
                    return _Fut(v=_v)
                asyncio.run_coroutine_threadsafe = _fake
                self.assertEqual(cb(call, None), expected)
            # A scheduling exception fails closed, as a failed review.
            from intergen.tool_registry import REVIEW_FAILED
            def _boom(coro, loop):
                coro.close()
                return _Fut(e=TimeoutError())
            asyncio.run_coroutine_threadsafe = _boom
            self.assertEqual(cb(call, None), REVIEW_FAILED)
        finally:
            asyncio.run_coroutine_threadsafe = orig


# ── The streaming gate-refusal message (single-gate review) ────────────────────────────────
class GateRefusalMessageTests(unittest.TestCase):
    def _server(self):
        from intergen.web_server import WebServer
        s = WebServer()
        s._tools = None          # _classify_risk_tier(None, ...) → state-changing
        s._governance = None
        return s

    def test_state_change_deny_gets_honest_handoff(self):
        server = self._server()
        call = ToolCall(name="manage_packages",
                        arguments={"action": "update"},
                        source_of_request=Provenance.USER_DIRECT)
        tr = ToolResult(call_id="", name="manage_packages", content="denied",
                        success=False, executed=False)
        msg = server._gate_refusal_message(call, tr)
        # The handoff is the builder's own commands as a person types them.
        self.assertIn("`" + _terminal(_update_commands(), root=True) + "`", msg)
        self.assertNotIn("blocked", msg.lower())

    def test_blocked_destructive_never_hands_over_the_command(self):
        server = self._server()
        call = ToolCall(name="run_command",
                        arguments={"command": "rm -rf /"},
                        source_of_request=Provenance.USER_DIRECT)
        tr = ToolResult(call_id="", name="run_command", content="blocked",
                        success=False, executed=False, blocked=True)
        msg = server._gate_refusal_message(call, tr)
        self.assertNotIn("rm -rf /", msg)    # never advise the blocked command
        self.assertNotIn("`", msg)           # no command block at all

    def test_handoff_command_only_for_command_backed_tools(self):
        from intergen.web_server import WebServer
        pkg = ToolCall(name="manage_packages", arguments={"action": "update"},
                       source_of_request=Provenance.USER_DIRECT)
        shot = ToolCall(name="take_screenshot", arguments={"source": "screenshot"},
                        source_of_request=Provenance.USER_DIRECT)
        self.assertTrue(WebServer._handoff_command(pkg))
        self.assertEqual(WebServer._handoff_command(shot), "")


# ── The router loop-killer (the honest-handoff work) ─────────────────────────────────────────
class _DenyTools:
    """A registry whose execute() returns a gate-refusal (not executed)."""
    def execute(self, call, *, ingress_tracker=None, trust_state=None,
                review_callback=None, origin=None, search_ask=None):
        return ToolResult(call_id="", name=getattr(call, "name", "run_command"),
                          content="Tool call denied by user via review modal.",
                          success=False, executed=False)

    def get_tool(self, name):
        # No tool object: the handoff's rule reads the call alone.
        return None


class LoopKillerTests(unittest.TestCase):
    def _router(self):
        from intergen.router import ConversationRouter
        r = ConversationRouter.__new__(ConversationRouter)
        r._handed_off_commands = set()
        r._tools = _DenyTools()
        r._ingress_tracker = IngressTracker()
        r._trust_state = object()
        r._review_callback = None
        r._filler = None
        r._pending_action_offer = None
        r._append_history = lambda a, b: None
        return r

    def test_deny_records_handoff_and_renders_honest_message(self):
        r = self._router()
        res = r._run_staged_action("pkm install zoom", "run_command", None)
        # The raw "denied" tool text is NEVER the delivered answer.
        self.assertNotIn("review modal", res.text)
        self.assertIn("pkm install zoom", res.text)      # the command handoff
        self.assertTrue(r._command_handed_off("pkm install zoom"))

    def test_declined_action_is_not_re_offered(self):
        r = self._router()
        r._note_handed_off("pkm install zoom")
        line = r._stage_action_offer_or_handoff(
            "pkm install zoom", "run_command", "install zoom")
        # No fresh offer armed, and the line is the honest handoff, not "say yes".
        self.assertIsNone(r._pending_action_offer)
        self.assertIn("pkm install zoom", line)
        self.assertNotIn("say yes", line.lower())

    def test_fresh_action_still_arms_an_offer(self):
        r = self._router()
        # A NOT-declined action arms normally (filler=None → template offer
        # line). A pkm line is staged as the package tool's call, and the offer
        # shows that tool's builder's command line, --yes included.
        line = r._stage_action_offer_or_handoff(
            "pkm install vim", "run_command", "install vim")
        # A pkm command line staged under run_command is re-tooled to the
        # package tool it MEANS, with its structured arguments as the fourth
        # element, so the review gate sees "manage_packages install vim" and
        # never a shell line; the person sees the command its builder returns.
        built = shlex.join(ManagePackagesTool().build_commands(
            {"action": "install", "package": "vim"})[0])
        self.assertEqual(r._pending_action_offer,
                         (built, "manage_packages", "install vim",
                          {"action": "install", "package": "vim"}))
        self.assertIn(f"`{built}`", line)


# ── The one interface-aware capability registry (the capability single-source) ───────────────────────
class CapabilityRegistrySingleSourceTests(unittest.TestCase):
    def test_router_phrase_table_reads_from_the_registry(self):
        from intergen.router import _TOOL_CAP_Q_SPECS
        for tool, _rx, phrase in _TOOL_CAP_Q_SPECS:
            self.assertTrue(phrase, f"{tool}: empty capability phrase")
            self.assertEqual(phrase, capability_registry.phrase(tool),
                             f"{tool}: router phrase drifted from the registry")

    def test_system_guard_built_from_registry_commands(self):
        from intergen import persona
        guard = persona.SYSTEM_CAPABILITY_GUARD
        self.assertIn(capability_registry.PKM_INDEX_REFRESH_CMD, guard)
        self.assertIn(capability_registry.PKM_UPGRADE_CMD, guard)
        # The guard is the assembled one, not a stale hardcoded copy.
        self.assertEqual(guard,
                         capability_registry.build_system_capability_guard())

    def test_pkm_update_command_is_the_shared_constant(self):
        # The constant the guard names is the package tool's own update, both
        # commands, as a person types pkm.
        self.assertEqual(
            capability_registry.PKM_UPDATE_COMMAND,
            " && ".join(" ".join(argv) for argv in _update_commands(pkm="pkm")))
        guard = capability_registry.build_system_capability_guard()
        for argv in (_update_commands(pkm="pkm")
                     + _update_commands("<package>", pkm="pkm")):
            self.assertIn("`" + " ".join(argv) + "`", guard)

    def test_confirmation_tail_is_interface_aware(self):
        web = capability_registry.confirmation_tail("web")
        console = capability_registry.confirmation_tail("console")
        self.assertIn("card", web)
        self.assertIn("prompt", console)


# ── Every shown row: each surface against the tool's own builder ─────────────
def _class_rows():
    """The call shapes the consent-surface class was enumerated over: every
    package and service action with and without its argument (user mode too),
    the alias and the wrong key, command lines, writes, the tools with no
    command line, and an external handler."""
    from intergen.tools.manage_services import (ACTION_ALIASES, AUTO_ACTIONS,
                                                CONFIRM_ACTIONS)
    rows = []
    for action in ("list", "search", "info", "verify", "install", "remove",
                   "uninstall", "update", "upgrade"):
        rows.append(("manage_packages", {"action": action}))
        rows.append(("manage_packages", {"action": action, "package": "htop"}))
    rows.append(("manage_packages", {"action": "search", "query": "htop"}))
    rows.append(("manage_packages", {"action": "install", "name": "htop"}))
    for action in sorted(AUTO_ACTIONS | CONFIRM_ACTIONS | set(ACTION_ALIASES)):
        for unit in (None, "sshd"):
            for user_mode in (False, True):
                args = {"action": action}
                if unit:
                    args["service"] = unit
                if user_mode:
                    args["user_mode"] = True
                rows.append(("manage_services", args))
    rows.append(("manage_services", {"action": "restart", "service": "ssh"}))
    rows.append(("manage_services", {"action": "restart", "unit": "sshd"}))
    for command in ("ls -la /tmp", "systemctl restart sshd",
                    "pkm sync && pkm upgrade", 'echo "a  b"',
                    "sudo pkm upgrade --all", "rm -rf /", "  touch /tmp/x  "):
        rows.append(("run_command", {"command": command}))
    rows.append(("run_command", {"command": "sleep 5", "timeout": 200}))
    for write in ({"path": "~/notes.txt", "content": "hello\n"},
                  {"path": "notes.txt", "content": "x"},
                  {"path": "/etc/hosts", "content": "x"},
                  {"path": "~/newdir/a.txt", "content": "x", "create_dirs": True},
                  {"path": "", "content": "x"}):
        rows.append(("write_file", write))
    rows += [("analyze_file", {"path": "sample"}), ("open_application", {}),
             ("read_file", {"path": "sample"}), ("take_screenshot", {}),
             ("web_search", {"query": "sample"}),
             ("an_external_handler", {"arg": "value"})]
    return rows


class _FlagScanner:
    """A scanner that flags every result coming back, so the tools whose card
    comes after their run (an ingress tool, an external handler) show it."""

    def scan(self, text, context):
        from intergen.interfaces.scanner import ScanDisposition, ScanVerdict
        if getattr(context, "direction", None) is not None and \
                context.direction.value == "ingress":
            return ScanVerdict(disposition=ScanDisposition.FLAG, reason="test")
        return ScanVerdict(disposition=ScanDisposition.ALLOW)


class BuilderAgainstSurfaceTests(unittest.TestCase):
    """Every row the registry shows a card for: the card, the handoff, the
    desktop headline, the zenity body, the console line and the model's guard
    each say what the tool's own builder returns for that call, and the call
    runs where its card says. Every row it refuses is refused before any card,
    with its reason. Nothing runs: a process start fails the case."""

    def setUp(self):
        from intergen.interfaces.types import ToolSchema
        self.registry = tool_registry.ToolRegistry()
        self.registry.discover_tools()
        self.registry.register_external(
            "an_external_handler",
            ToolSchema(name="an_external_handler", description="external",
                       parameters={"type": "object"}),
            lambda arguments: "(sent nowhere)", "")
        self.registry._scanner = _FlagScanner()
        services = self.registry.get_tool("manage_services")
        services._read_service_unit = (
            lambda name, user_mode=False: ("known", name, ""))
        self.started = []
        self.paths = []

        def _refuse(args, *a, **kw):
            # Refused, so nothing runs. The never-list's signature check that
            # write_file's classification makes (gpgv) is a read, not the act;
            # refused, it leaves that classification on its interim floor.
            if list(args)[:1] != ["gpgv"]:
                self.started.append(args)
            raise OSError(f"a process start, refused by this case: {args!r}")

        def _runner(call, tool_name, arguments, dispatch_token=None):
            self.paths.append("root")
            return ToolResult(call_id=call.call_id, name=tool_name,
                              content="(stood in)", success=False,
                              executed=False)

        # write_file's signed never-list is loaded once per process and kept
        # (write_file._policy_cache). It is pinned here to the state this case
        # reads, the manifest not loaded and the interim floor in force, so a
        # test before this one that loaded the real manifest cannot change which
        # write rows show a card: with it loaded, the write to /etc/hosts is
        # refused before any card instead.
        from intergen.tools import write_file
        for patcher in (
                mock.patch("subprocess.run", _refuse),
                mock.patch("intergen.tool_registry.write_record",
                           lambda *a, **kw: None),
                mock.patch("intergen.tool_registry.mint_token",
                           lambda *a, **kw: "token"),
                mock.patch.object(self.registry, "_dispatch_via_pkexec",
                                  _runner),
                mock.patch.object(write_file, "_policy_cache", None),
                mock.patch.object(write_file, "_policy_loaded", True),
                mock.patch.object(write_file, "_policy_untrusted", False)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _built_line(self, name, arguments):
        """The command line from the tool's own builder, or None for a tool
        with no command line; run_command's from its own execute(), the
        process start captured and refused."""
        if name == "manage_packages":
            commands = ManagePackagesTool().build_commands(arguments)
            return commands, " && ".join(shlex.join(c) for c in commands)
        if name == "manage_services":
            argv = ManageServicesTool().build_command(arguments)
            return [argv], shlex.join(argv)
        if name == "run_command":
            from intergen import tool_process
            from intergen.tools.run_command import RunCommandTool
            captured = []

            def _capture(args, *a, **kw):
                captured.append(list(args))
                return subprocess.CompletedProcess(args, 0, "", "")
            # The unit search path the launcher reads before every start is
            # stood in by a directory that does not exist (no drop-in applies),
            # so the one process start captured is the launch itself.
            with tempfile.TemporaryDirectory() as scratch, \
                    mock.patch("subprocess.run", _capture), \
                    mock.patch.object(tool_process, "unit_search_path",
                                      return_value=[os.path.join(scratch, "systemd", "user")]):
                RunCommandTool().execute(dict(arguments))
            launch = captured[0]
            inner = launch[launch.index("--") + 1:]
            # The command starts under the environment the executor writes:
            # env -i and exactly that environment's assignments, then the program.
            written = [tool_process.ENV, "-i"] + [
                f"{key}={value}" for key, value in tool_process.tool_environment().items()]
            self.assertEqual(inner[:len(written)], written)
            inner = inner[len(written):]
            return [inner], shlex.join(inner)
        return None, None

    def _console(self, card, name):
        from intergen.console.shell import ConsoleShell
        shell = object.__new__(ConsoleShell)
        got = []
        shell._add_message = lambda role, content, *a, **kw: got.append(content)
        shell._gate_pending = None
        shell._is_streaming = False
        frame = {"type": "gate_prompt", "tool_name": name, "card": card,
                 "action": "{}", "blocked_by": "provenance_gate"}
        asyncio.run(ConsoleShell._process_server_message(shell, frame))
        return "\n".join(got)

    def test_every_shown_row_says_what_the_builder_runs(self):
        from intergen import review_modal
        from intergen.consent_dialog_sanitize import describe_gate_action
        shown = refused = 0
        refused_rows = []
        for name, arguments in _class_rows():
            with self.subTest(name=name, arguments=arguments):
                self.paths.clear()
                cards = []
                tool = self.registry.get_tool(name)
                in_person = []
                if tool is not None:
                    def _in_person(args, _name=name):
                        in_person.append(_name)
                        # A result that came back, so an ingress tool's
                        # result is scanned and its after-the-run card shown.
                        return ToolResult(call_id="", name=_name,
                                          content="(stood in)", success=True)
                    tool.execute = _in_person
                call = ToolCall(name=name, arguments=dict(arguments),
                                source_of_request=Provenance.USER_IMPLIED)
                try:
                    result = self.registry.execute(
                        call, review_callback=lambda c, d: cards.append(d)
                        or "allow_once")
                finally:
                    if tool is not None:
                        del tool.execute
                if not cards:
                    if not result.executed and not result.success \
                            and not in_person and not self.paths:
                        # Refused before any card, with its reason.
                        refused += 1
                        refused_rows.append((name, arguments))
                        self.assertTrue(result.content.strip())
                    continue
                shown += 1
                decision = cards[0]
                what, command = tool_registry.card_action_description(call)
                commands, line = self._built_line(name, arguments)
                act = tool_registry.describe_call(call, tool)
                if line is not None:
                    self.assertEqual(command, line)
                    body = review_modal._format_modal_body(
                        call, decision, "", "", "")
                    self.assertIn(f"Command:     {line}", body)
                    self.assertNotIn("Arguments:", body)
                    console = self._console({"what": what, "command": command},
                                            name)
                    self.assertIn(f"Command: {line}", console)
                    # The handoff of the person's own call; one that is not
                    # theirs keeps its line withheld
                    # (test_a_refusal_never_hands_over_the_refused_call).
                    own = ToolCall(name=name, arguments=dict(arguments),
                                   source_of_request=Provenance.USER_DIRECT)
                    handoff = tool_registry.gate_refusal_message(
                        own, ToolResult(call_id="", name=name, content="",
                                        success=False, executed=False), tool)
                    self.assertIn("`" + _terminal(commands, act.root) + "`",
                                  handoff)
                else:
                    # No command line: a sentence naming the act's argument.
                    self.assertEqual(command, what)
                    self.assertNotIn(str(arguments), command)
                self.assertEqual(describe_gate_action(name, arguments), what)
                # The act runs where its card says: the privileged runner for
                # a root act, the person's own process otherwise.
                if act.root:
                    self.assertEqual((self.paths, in_person), (["root"], []))
                    self.assertTrue(decision.needs_pkexec)
                elif name in ("manage_services",):
                    self.assertEqual((self.paths, in_person), ([], [name]))
                    self.assertIn("--user", command)
                    self.assertFalse(decision.needs_pkexec)
                if name == "manage_packages" and arguments["action"] == "update":
                    guard = capability_registry.build_system_capability_guard()
                    for argv in _update_commands(pkm="pkm"):
                        self.assertIn("`" + " ".join(argv) + "`", guard)
        # The calls whose card used to precede an act that differed or did not
        # happen are refused before any card now: no package, the wrong key,
        # the dead verbs, a daemon-reload naming a unit, a shell construct, a
        # relative path.
        for row in (("manage_packages", {"action": "install"}),
                    ("manage_packages", {"action": "remove"}),
                    ("manage_packages", {"action": "install", "name": "htop"}),
                    ("manage_packages", {"action": "upgrade"}),
                    ("manage_packages", {"action": "uninstall", "package": "htop"}),
                    ("manage_services", {"action": "daemon-reload",
                                         "service": "sshd"}),
                    ("manage_services", {"action": "daemon-reload",
                                         "service": "sshd", "user_mode": True}),
                    ("run_command", {"command": "pkm sync && pkm upgrade"}),
                    ("write_file", {"path": "notes.txt", "content": "x"})):
            self.assertIn(row, refused_rows)
        # A web search the model decides on is refused before any card: it
        # runs only on the person's word.
        self.assertIn(("web_search", {"query": "sample"}), refused_rows)
        # The counts this class rests on: a change to either is read here.
        self.assertEqual((shown, refused), (33, 50))
        self.assertEqual(self.started, [])


if __name__ == "__main__":
    unittest.main()
