# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A boundary's refusal is told in its own words, never as a safety block or a run.

The defect class: a refusal made before a tool's act runs, by a boundary that is
not the safety classifier — the launch boundary (a credential file that is
absent), the shell-free executor, a tool's own check of its arguments or of what
this machine has, the privileged dispatch before its runner starts, the image
gate — came back through the classifier's `blocked` flag or with `executed` left
True. The delivery paths then told it as a classifier block (a canned line, with
the entry and the remedy dropped) or handed it to the synthesis as a run that
errored.

The rule these cases hold every path to: such a result is never `blocked` (the
classifier's alone), is `executed` only when its act ran (the image gate's
capture did), names its kind in `outcome`, and carries its own code-owned words
in `refusal`; the tool route, an accepted offer, the keyword path and the web
panel tell those words — the entry, the reason, the remedy — and never the
classifier's line, a handoff or a synthesis.

Every call goes through the REAL registry and tools with no model behind them:
every process start and every connect is refused, the audit writer and the
token minter are stood in, and the install's credential files are fakes in a
scratch home that the password database is made to answer with.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from intergen import dispatch_token
from intergen import privileged_request
from intergen import router as router_module
from intergen import safety
from intergen import tool_registry as reg_mod
from intergen.interfaces.provenance import Provenance
from intergen.interfaces.types import RouteResult, ToolCall, ToolResult
from intergen.tests import tool_process_fixture as fixture
from intergen.tests.test_a_refusal_never_hands_over_the_refused_call import (
    ASK, _OneCallLLM, _RecordingWS, _fresh, _nothing_runs, _refused, _route,
    _staged)
from intergen.tools import analyze_file as analyze_file_mod
from intergen.tools import manage_packages as manage_packages_mod
from intergen.tools import read_file as read_file_mod
from intergen.tools import run_command as run_command_mod
from intergen.tools import write_file as write_file_mod
from intergen.tools.analyze_file import AnalyzeFileTool
from intergen.tools.manage_packages import ManagePackagesTool
from intergen.tools.manage_services import ManageServicesTool
from intergen.tools.open_application import OpenApplicationTool
from intergen.tools.read_file import ReadFileTool
from intergen.tools.run_command import RunCommandTool
from intergen.tools.take_screenshot import TakeScreenshotTool
from intergen.tools.web_search import WebSearchTool
from intergen.tools.write_file import WriteFileTool
from intergen.web_server import ConnectionContext, WebServer

D = Provenance.USER_DIRECT
# Every line the classifier's block can be told in.
CANNED = sorted({line for lines in safety._BLOCKED_RESPONSES.values()
                 for line in lines})
# The approval a person could not give here: the handoff's own words.
APPROVAL = "administrator approval, which I can't collect from here"
# What the bare router's synthesis says when one is asked for.
SYNTHESIZED = "the safety layer blocked it"
# A picture the image gate refuses: not a data URI.
PICTURE = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAwS2OUAAAAABJRU5ErkJggg=="


@contextlib.contextmanager
def _install_home(*, without_credential=False):
    """A scratch home holding fake credential files, which the install's owner
    lookup answers with. With `without_credential` the first is removed again,
    and its path is what the launch boundary names. Yields (home, the path
    removed or None). It lives under HOME, so a write into it is the
    account's own write, not one the privileged dispatch carries."""
    with tempfile.TemporaryDirectory(prefix="boundary-words-",
                                     dir=Path.home()) as scratch:
        home = Path(scratch)
        fixture.write_fake_credentials(home)
        gone = None
        if without_credential:
            gone = sorted(home.joinpath(*relative)
                          for relative in dispatch_token.SECRET_RELATIVE_PATHS)[0]
            gone.unlink()
        with fixture.install_lives_in(home):
            yield home, gone


def _route_through_the_dispatch(call, *, units):
    """The native tool route with the REAL privileged dispatch, under the
    posture that escalates and a unit search path of `units`: the audit
    writer, the token and the request file stood in, and nothing may start or
    connect, so the dispatch can only refuse."""
    router = _fresh()
    router._llm.stream_with_tools = mock.Mock(return_value=iter([call]))
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.dict(
            os.environ, {reg_mod.PRIVILEGED_ESCALATION_ENV:
                         reg_mod.ESCALATION_ALLOWED}))
        stack.enter_context(mock.patch.object(reg_mod, "write_record",
                                              return_value=True))
        stack.enter_context(mock.patch.object(reg_mod, "mint_token",
                                              return_value="test-token"))
        stack.enter_context(mock.patch.object(
            privileged_request, "write_request",
            return_value=Path("/nonexistent/request")))
        stack.enter_context(mock.patch.object(
            privileged_request, "request_id_for", return_value="request-1"))
        stack.enter_context(mock.patch.object(
            privileged_request, "prune_stale_requests", return_value=[]))
        stack.enter_context(fixture.unit_search_path_is(units))
        for name in ("run", "Popen"):
            stack.enter_context(mock.patch.object(subprocess, name,
                                                  side_effect=_refused))
        for name in ("posix_spawn", "posix_spawnp"):
            stack.enter_context(mock.patch.object(os, name,
                                                  side_effect=_refused))
        stack.enter_context(mock.patch.object(socket.socket, "connect",
                                              _refused))
        stack.enter_context(mock.patch("intergen.router.glass.emit"))
        return router.route(ASK, review_callback=lambda _c, _d: "allow_once")


def _picture_taken(_self, _arguments):
    """take_screenshot's capture, stood in so no capture program starts."""
    return ToolResult(call_id="", name="take_screenshot",
                      content="[Image captured via the test: 1KB PNG]",
                      success=True, image_data=PICTURE)


class _Checks(unittest.TestCase):
    maxDiff = None

    def assertToldInItsOwnWords(self, text, *words):
        """The words are in what the person reads, and none of the classifier's
        lines, the approval handoff or a synthesis is."""
        for word in words:
            self.assertIn(word, text)
        for line in CANNED:
            self.assertNotIn(line, text)
        self.assertNotIn(APPROVAL, text)
        self.assertNotIn(SYNTHESIZED, text)

    def assertRefusedWithoutRunning(self, result, outcome, *words):
        self.assertFalse(result.success, result)
        self.assertFalse(result.blocked, result)
        self.assertFalse(result.executed, result)
        self.assertEqual(result.outcome, outcome, result)
        for word in words:
            self.assertIn(word, result.refusal)


class TheToolRouteTellsTheBoundarysWords(_Checks):
    """Through router.route to the native tool route (_try_llm_tools)."""

    def test_an_absent_credential_file_is_named_with_its_remedy(self):
        with _install_home(without_credential=True) as (_home, gone):
            result, _ = _route([ToolCall(name="run_command",
                                         arguments={"command": "df -h"},
                                         source_of_request=D)], "allow_once")
        self.assertToldInItsOwnWords(result.text, str(gone), "intergen setup")
        self.assertRefusedWithoutRunning(result.tool_results[0], "not_started",
                                         str(gone))
        self.assertEqual(result.answer_linkage.renderer, "gate_refusal")

    def test_a_shell_construct_is_told_the_executors_reason(self):
        result, _ = _route([ToolCall(name="run_command",
                                     arguments={"command": "df -h | head -n 3"},
                                     source_of_request=D)], "allow_once")
        self.assertToldInItsOwnWords(result.text, "no shell",
                                     "one plain command at a time")
        self.assertRefusedWithoutRunning(result.tool_results[0], "shell_refused")

    def test_an_unparseable_line_is_told_why(self):
        result, _ = _route([ToolCall(name="run_command",
                                     arguments={"command": 'echo "unbalanced'},
                                     source_of_request=D)], "allow_once")
        self.assertToldInItsOwnWords(result.text, "could not be split into words")
        self.assertRefusedWithoutRunning(result.tool_results[0],
                                         "invalid_arguments")

    def test_a_unit_directory_the_account_can_change_is_named_with_its_remedy(self):
        with _install_home() as (home, _gone):
            entry = home / "units" / "intergen-tool-.service.wants"
            entry.mkdir(parents=True)
            with fixture.unit_search_path_is(entry.parent):
                result, _ = _route([ToolCall(name="run_command",
                                             arguments={"command": "df -h"},
                                             source_of_request=D)], "allow_once")
        self.assertToldInItsOwnWords(result.text, str(entry), "Remove it")
        self.assertRefusedWithoutRunning(result.tool_results[0], "not_started",
                                         str(entry))

    def test_the_dispatchs_unit_directory_is_named_with_its_remedy(self):
        with _install_home() as (home, _gone):
            entry = home / "units" / "intergen-privileged-.service.d"
            entry.mkdir(parents=True)
            result = _route_through_the_dispatch(
                ToolCall(name="manage_packages", arguments={"action": "update"},
                         source_of_request=D), units=entry.parent)
        self.assertToldInItsOwnWords(result.text, str(entry), "Remove it",
                                     "no password was asked for")
        self.assertRefusedWithoutRunning(result.tool_results[0], "not_started",
                                         str(entry))
        self.assertEqual(result.answer_linkage.renderer, "gate_refusal")

    def test_the_image_gate_is_told_its_own_sentence_and_keeps_its_run(self):
        with mock.patch.object(TakeScreenshotTool, "execute", _picture_taken):
            result, _ = _route([ToolCall(name="take_screenshot",
                                         arguments={"source": "screenshot"},
                                         source_of_request=D)], "allow_once")
        tr = result.tool_results[0]
        self.assertToldInItsOwnWords(
            result.text, "I didn't read that picture into our conversation")
        self.assertEqual(result.text, tr.refusal)
        self.assertTrue(tr.executed, tr)
        self.assertFalse(tr.blocked, tr)
        self.assertEqual(tr.outcome, "image_refused")
        self.assertEqual(result.answer_linkage.renderer, "gate_refusal")



class TheAcceptedOfferTellsTheBoundarysWords(_Checks):
    """An accepted offer (router._run_staged_action), for run_command."""

    def test_an_absent_credential_file_is_named_with_its_remedy(self):
        with _install_home(without_credential=True) as (_home, gone):
            text = _staged("df -h", "allow_once")
        self.assertToldInItsOwnWords(text, str(gone), "intergen setup")
        self.assertNotIn("yourself", text)

    def test_a_shell_construct_is_told_the_executors_reason(self):
        text = _staged("df -h | head -n 3", "allow_once")
        self.assertToldInItsOwnWords(text, "no shell",
                                     "one plain command at a time")


class ThePanelAndTheKeywordPathTellTheBoundarysWords(_Checks):
    """The web panel's tool loop and the keyword path (_decline_answer)."""

    def _streamed(self, call, *, tools=True):
        router = _fresh()
        server = WebServer(router=router, llm=_OneCallLLM(call),
                           tools=router._tools if tools else None)
        ctx = ConnectionContext(client_id="c1", source_interface="web",
                                ws=_RecordingWS(),
                                conversation=router.new_conversation())
        with _nothing_runs(router):
            asyncio.run(server._stream_llm_response(
                ctx, "turn1", ASK,
                RouteResult(text="", source="llm_tools", handled=False)))
        return "".join(m.get("token", "") for m in ctx.ws.sent
                       if m.get("type") == "stream_token")

    def test_the_panel_names_an_absent_credential_file_with_its_remedy(self):
        with _install_home(without_credential=True) as (_home, gone):
            text = self._streamed(ToolCall(name="run_command",
                                           arguments={"command": "df -h"},
                                           source_of_request=D))
        self.assertToldInItsOwnWords(text, str(gone), "intergen setup")

    def test_the_panel_tells_the_image_gates_sentence(self):
        with mock.patch.object(TakeScreenshotTool, "execute", _picture_taken), \
                mock.patch.object(WebServer, "_make_web_review_callback",
                                  return_value=lambda _c, _d: "allow_once"):
            text = self._streamed(ToolCall(name="take_screenshot",
                                           arguments={"source": "screenshot"},
                                           source_of_request=D))
        self.assertToldInItsOwnWords(
            text, "I didn't read that picture into our conversation")

    def test_the_panel_with_no_registry_says_nothing_ran(self):
        text = self._streamed(ToolCall(name="run_command",
                                       arguments={"command": "df -h"},
                                       source_of_request=D), tools=False)
        self.assertToldInItsOwnWords(text, "Nothing was run",
                                     "tools are not ready")

    def test_the_keyword_path_tells_the_image_gates_sentence(self):
        result = ToolResult(call_id="c1", name="take_screenshot",
                            content="the gate's sentence", success=False,
                            executed=True, outcome="image_refused",
                            refusal="the gate's sentence")
        call = ToolCall(name="take_screenshot", arguments={},
                        source_of_request=D)
        answer = router_module._decline_answer("dispatch_failed", result, call)
        self.assertEqual(answer.text, "the gate's sentence")
        self.assertEqual(answer.answer_linkage.renderer, "gate_refusal")


def _call(tool, arguments):
    return ToolCall(name=tool, arguments=dict(arguments), source_of_request=D)


class EverySiteRefusesWithoutRunning(_Checks):
    """Each site that refuses before its act returns the shape the rule names,
    and the one renderer tells its words."""

    def _told(self, tool, arguments, result, outcome, *words):
        self.assertRefusedWithoutRunning(result, outcome, *words)
        text = reg_mod.gate_refusal_message(_call(tool, arguments), result)
        self.assertEqual(text, f"Nothing was run: {result.refusal.rstrip('.')}.")
        self.assertToldInItsOwnWords(text, *words)

    # run_command

    def test_run_command_empty(self):
        args = {"command": "   "}
        self._told("run_command", args, RunCommandTool().execute(args),
                   "invalid_arguments", "the command is empty")

    def test_run_command_empty_after_splitting(self):
        args = {"command": "x"}
        with mock.patch.object(run_command_mod.shlex, "split", return_value=[]):
            result = RunCommandTool().execute(args)
        self._told("run_command", args, result, "invalid_arguments",
                   "the command is empty")

    def test_run_command_the_classifiers_block_keeps_its_line_and_ran_nothing(self):
        result = RunCommandTool().execute({"command": "rm -rf /"})
        self.assertTrue(result.blocked, result)
        self.assertFalse(result.executed, result)
        self.assertEqual(result.refusal, "")

    def test_run_command_a_program_that_is_not_installed(self):
        args = {"command": "no-such-program-340 --version"}
        with _install_home():
            result = RunCommandTool().execute(args)
        self._told("run_command", args, result, "not_started",
                   "'no-such-program-340' is not a program installed")

    def test_run_command_a_launcher_that_cannot_start(self):
        args = {"command": "uptime"}
        # The unit-directory gate reads no search path, so the stand-in meets
        # the launcher rather than the gate's own read.
        with _install_home(), fixture.unit_search_path_is(), mock.patch.object(
                run_command_mod.subprocess, "run",
                side_effect=PermissionError(13, "Permission denied")):
            result = RunCommandTool().execute(args)
        self._told("run_command", args, result, "not_started",
                   "could not be started (Permission denied)")

    def test_run_command_the_launch_boundary(self):
        args = {"command": "uptime"}
        with _install_home(without_credential=True) as (_home, gone):
            result = RunCommandTool().execute(args)
        self._told("run_command", args, result, "not_started", str(gone),
                   "intergen setup")

    # the privileged dispatch, before its runner

    def _dispatched(self, *patches, token="token"):
        call = _call("manage_packages", {"action": "update"})
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(
                os.environ, {reg_mod.PRIVILEGED_ESCALATION_ENV:
                             reg_mod.ESCALATION_ALLOWED}))
            stack.enter_context(mock.patch("intergen.tool_registry.glass.emit"))
            for p in patches:
                stack.enter_context(p)
            return call, reg_mod.ToolRegistry._dispatch_via_pkexec(
                call, "manage_packages", {"action": "update"},
                dispatch_token=token)

    def test_dispatch_without_a_token(self):
        call, result = self._dispatched(token=None)
        self._told("manage_packages", call.arguments, result, "not_started",
                   "approval token")

    def test_dispatch_with_a_request_that_cannot_be_written(self):
        call, result = self._dispatched(mock.patch.object(
            privileged_request, "write_request",
            side_effect=privileged_request.RequestError(
                "the privileged-request directory /x is not a directory")))
        self._told("manage_packages", call.arguments, result, "not_started",
                   "could not be written", "is not a directory")

    @staticmethod
    def _request_written():
        """The request file the runner would read, stood in (no file)."""
        return (mock.patch.object(privileged_request, "write_request",
                                  return_value=Path("/nonexistent/request")),
                mock.patch.object(privileged_request, "request_id_for",
                                  return_value="request-1"),
                mock.patch.object(privileged_request, "prune_stale_requests",
                                  return_value=[]))

    def test_dispatch_with_no_systemd_run(self):
        # The unit-directory gate reads no search path (it asks the user
        # manager with a process of its own), so the stand-in meets the runner.
        call, result = self._dispatched(
            *self._request_written(), fixture.unit_search_path_is(),
            mock.patch.object(reg_mod.subprocess, "run",
                              side_effect=FileNotFoundError(2, "No such file")))
        self._told("manage_packages", call.arguments, result, "not_started",
                   "is not available, so the privileged runner")

    def test_dispatch_with_a_runner_that_cannot_be_invoked(self):
        call, result = self._dispatched(
            *self._request_written(), fixture.unit_search_path_is(),
            mock.patch.object(reg_mod.subprocess, "run",
                              side_effect=PermissionError(13, "Permission denied")))
        self._told("manage_packages", call.arguments, result, "not_started",
                   "could not be started (Permission denied)")

    # manage_packages and manage_services

    def test_manage_packages_a_change_without_root(self):
        args = {"action": "install", "package": "htop"}
        with mock.patch.object(manage_packages_mod.os, "geteuid", return_value=1000):
            result = ManagePackagesTool().execute(args)
        self._told("manage_packages", args, result, "not_started",
                   "must run as root")

    def test_manage_packages_no_package_manager_at_its_path(self):
        args = {"action": "install", "package": "htop"}
        with mock.patch.object(manage_packages_mod.os, "geteuid", return_value=0), \
                mock.patch.object(manage_packages_mod.os.path, "isfile",
                                  return_value=False):
            result = ManagePackagesTool().execute(args)
        self._told("manage_packages", args, result, "unavailable",
                   "not present as an executable")

    def test_manage_packages_no_package_manager_installed(self):
        args = {"action": "list"}
        with mock.patch.object(manage_packages_mod.shutil, "which",
                               return_value=None):
            result = ManagePackagesTool().execute(args)
        self._told("manage_packages", args, result, "unavailable",
                   "is not installed here yet")

    def test_manage_packages_no_command_for_the_arguments(self):
        args = {"action": "list"}
        with mock.patch.object(ManagePackagesTool, "build_commands",
                               return_value=None), \
                mock.patch.object(manage_packages_mod.shutil, "which",
                                  return_value="/usr/bin/pkm"):
            result = ManagePackagesTool().execute(args)
        self._told("manage_packages", args, result, "invalid_arguments",
                   "no package command can be built")

    def test_manage_services_no_action(self):
        self._told("manage_services", {}, ManageServicesTool().execute({}),
                   "invalid_arguments", "no service action was given")

    def test_manage_services_no_service_name(self):
        args = {"action": "restart"}
        self._told("manage_services", args, ManageServicesTool().execute(args),
                   "invalid_arguments", "needs a service name")

    # read_file and analyze_file

    # write_file

    # open_application, web_search, take_screenshot


class TheNewShapeLoosensNothing(_Checks):
    """What a refusal that is no longer `blocked` must still never become: a
    fixed probe's reading, or a command handed over to run."""

    def test_a_fixed_probe_takes_no_reading_from_a_refusal(self):
        router = _fresh()
        probe = "systemctl is-active nftables.service"
        refused = ToolResult(call_id="", name="run_command",
                             content="This command was not run: the boundary",
                             success=False, executed=False,
                             outcome="not_started")
        with mock.patch.object(router._tools, "execute", return_value=refused):
            self.assertIsNone(router._run_fixed_command(probe, keep_failed=True))
        # The control: a probe that ran and exited non-zero is still read.
        ran = ToolResult(call_id="", name="run_command", content="inactive\n",
                         success=False)
        with mock.patch.object(router._tools, "execute", return_value=ran):
            self.assertEqual(router._run_fixed_command(probe, keep_failed=True),
                             "inactive\n")

    def test_the_shell_free_executors_refusal_is_never_handed_over(self):
        args = {"command": "df -h | head -n 3"}
        result = RunCommandTool().execute(args)
        self.assertRefusedWithoutRunning(result, "shell_refused")
        self.assertTrue(reg_mod.handoff_withheld(_call("run_command", args),
                                                 result, RunCommandTool()))


if __name__ == "__main__":
    unittest.main()
