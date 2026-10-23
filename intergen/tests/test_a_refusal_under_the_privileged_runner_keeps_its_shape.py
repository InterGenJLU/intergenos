# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A refusal made under the privileged runner reaches the registry as a refusal.

THE DEFECT. The privileged dispatcher runs as root in a process of its own, and
only its stdout and its exit status come back across the boundary. Every refusal
it makes (an approval that does not verify, arguments the schema refuses, a
BLOCKED classification, a spent approval) and every refusal a tool makes under
it (write_file's path refusals, a package action pkm cannot take) returned as
text and an exit status of 1, and the registry rebuilt the result from those two
alone: executed left True, the blocked flag, the outcome and the refusal's words
gone. A call that never ran was told to the person as a run that had errored.

THE CORRECTION. On every non-zero exit the dispatcher ends its output with one
result line of its own, written in the same print as its reason: the request it
answers and the result's executed, blocked, outcome and refusal. The registry
reads that shape from the FINAL line only, and only when the line names this
dispatch's request identifier, so text inside a tool's content (written before
the line) cannot pose as it, and neither can a final line the dispatcher did not
write for this dispatch. A success carries no line and is read as before.

HOW THESE CELLS DRIVE IT. The whole chain runs with three links stood in. The
registry's _dispatch_via_pkexec writes its request and builds its systemd-run
command exactly as in production; the stand-in for subprocess.run takes the
request identifier from that command and calls the real
privileged_dispatch.main() with it, as the runner does after pkexec; the
dispatcher reads and verifies the request (a test key signs the approval) and
answers through a stand-in tool; its stdout and exit status go back to the
registry, which builds the result as it does after a real dispatch. systemd-run,
pkexec and the runner shim are what is stood in, nothing runs as root, nothing
contacts PolicyKit, and both stores and the runtime tree are temporary
directories.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import pwd
import tempfile
import unittest
from unittest import mock

from intergen import dispatch_token as dt
from intergen import privileged_dispatch as pd
from intergen import privileged_request as pr
from intergen import tool_registry as tr
from intergen.interfaces.provenance import Provenance
from intergen.interfaces.types import SafetyTier, ToolCall, ToolResult
from intergen.tests.escalation_posture import (
    escalation_allowed_for_a_stubbed_dispatch,
)
from intergen.tests.tool_process_fixture import unit_search_path_is


_KEY = "cd" * dt.KEY_BYTES
_TOOL = "manage_packages"
_ARGS = {"action": "install", "package": "a-neutral-package"}

#: The wire format these cells plant by hand, so a planted line does not depend
#: on the code it is planted against. test_the_line_is_the_one_these_cells_plant
#: holds it equal to what the dispatcher writes.
_PREFIX = "privileged_dispatch result: "


def _planted_line(request_id, **fields):
    body = {"request_id": request_id, "executed": False, "blocked": False,
            "outcome": "not_started",
            "refusal": "a refusal that did not happen"}
    body.update(fields)
    return _PREFIX + json.dumps(body, sort_keys=True)


class _Tool:
    """A discovered privileged tool whose every answer a cell chooses."""

    #: Declares no command line, as the tools whose route is re-derived read.
    command_line_parameters = ()

    def __init__(self, *, result=None, raises=None, validation_error=None,
                 safety=SafetyTier.CONFIRM):
        self._result = result if result is not None else ToolResult(
            call_id="", name=_TOOL, content="done", success=True)
        self._raises = raises
        self._validation_error = validation_error
        self._safety = safety

    def validate_arguments(self, arguments):
        return self._validation_error

    def classify_safety(self, arguments):
        return self._safety

    def execute(self, arguments):
        if self._raises is not None:
            raise self._raises
        return self._result


class _Completed:
    """Stand-in for subprocess.CompletedProcess."""

    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _ChainTestCase(unittest.TestCase):
    """The registry and the dispatcher, joined where the runner would join them."""

    def setUp(self):
        self.uid = os.getuid()
        self.user = pwd.getpwuid(self.uid).pw_name
        store = tempfile.TemporaryDirectory(prefix="runnershape-")
        self.addCleanup(store.cleanup)
        runtime = tempfile.TemporaryDirectory(prefix="runnershape-rt-")
        self.addCleanup(runtime.cleanup)
        uid_dir = os.path.join(runtime.name, str(self.uid))
        os.makedirs(uid_dir, mode=0o700)
        # The dispatch meets the unit-directory gate before its systemd-run,
        # and the gate asks the user manager for its search path through
        # subprocess.run, which the runner below stands in for: answered with
        # an empty directory, so the stand-in sees the dispatch alone.
        units = os.path.join(runtime.name, "units")
        os.makedirs(units)
        self.registry = mock.MagicMock()
        self.registry.discover_tools.return_value = 1
        self.registry.get_tool.return_value = _Tool()
        for patcher in (
            mock.patch.object(pd, "_CONSUMED_NONCE_DIR", store.name),
            mock.patch.object(pd, "_CONSUMED_NONCE_PATH",
                              os.path.join(store.name, "consumed-nonces")),
            mock.patch.object(pd, "_DISPATCH_STATE_PATH",
                              os.path.join(store.name, "dispatch-states")),
            mock.patch.object(pr, "RUNTIME_ROOT", runtime.name),
            mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": uid_dir},
                            clear=False),
            mock.patch.object(dt, "load_dispatch_key", return_value=_KEY),
            mock.patch.object(pd, "ToolRegistry", return_value=self.registry),
            unit_search_path_is(units),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.dispatcher_output = None
        self.request_id = None

    def use_tool(self, tool):
        self.registry.get_tool.return_value = tool

    def dispatch(self, *, token_args=None, canned_stdout=None):
        """Dispatch _TOOL with _ARGS through the chain; return the result.

        `canned_stdout`, when given, is called with the request identifier and
        answers in the dispatcher's place, for the cells about lines it did
        not write.
        """
        token = dt.mint_token(_TOOL, token_args or _ARGS, self.uid, key=_KEY,
                              ttl_seconds=120)

        def _runner(argv, **kwargs):
            if len(argv) >= 3 and tuple(argv[1:3]) == ("--user",
                                                       "is-system-running"):
                return _Completed(0, "running\n")
            if argv[0] != tr._SYSTEMD_RUN:
                raise AssertionError(f"a process the chain does not start: "
                                     f"{argv!r}")
            request_id = argv[-1]
            self.request_id = request_id
            if canned_stdout is not None:
                return _Completed(1, canned_stdout(request_id))
            out = io.StringIO()
            env = {"PKEXEC_UID": str(self.uid), "PKEXEC_USER": self.user}
            # The runner hands the dispatcher the tool pkexec authenticated
            # for and the request identifier, the last two words it was given.
            with mock.patch.dict(os.environ, env, clear=False), \
                    contextlib.redirect_stdout(out):
                try:
                    code = pd.main(["--tool", argv[-2],
                                    "--request-id", request_id])
                except SystemExit as exc:
                    code = exc.code
            self.dispatcher_output = out.getvalue()
            return _Completed(code, out.getvalue())

        call = ToolCall(name=_TOOL, arguments=dict(_ARGS),
                        call_id="runner-shape",
                        source_of_request=list(Provenance)[0])
        with mock.patch.object(tr.subprocess, "run", side_effect=_runner), \
                escalation_allowed_for_a_stubbed_dispatch():
            return tr.ToolRegistry._dispatch_via_pkexec(
                call, _TOOL, dict(_ARGS), token)

    def assertNotRun(self, result, outcome, words):
        self.assertFalse(result.success)
        self.assertFalse(
            result.executed,
            f"a refusal made under the runner was told as a run: "
            f"{result.content!r}")
        self.assertEqual(getattr(result, "outcome", None), outcome)
        self.assertIn(words, getattr(result, "refusal", "") or "")
        self.assertIn(words, result.content)
        self.assertNotIn(_PREFIX, result.content)


class TheDispatchersOwnRefusalsArriveAsRefusalsTests(_ChainTestCase):

    def test_an_approval_that_does_not_verify_arrives_not_run(self):
        result = self.dispatch(
            token_args={"action": "install", "package": "another-package"})
        self.assertNotRun(result, "not_started",
                          "dispatch token verification failed")
        self.assertFalse(result.blocked)

    def test_arguments_the_schema_refuses_arrive_as_invalid_arguments(self):
        self.use_tool(_Tool(validation_error="package must be a string"))
        result = self.dispatch()
        self.assertNotRun(result, "invalid_arguments",
                          "argument validation failed")

    def test_a_tool_the_registry_did_not_discover_arrives_as_unavailable(self):
        self.registry.get_tool.return_value = None
        result = self.dispatch()
        self.assertNotRun(result, "unavailable",
                          "the registry did not discover it")

    def test_a_blocked_classification_arrives_blocked_with_no_words(self):
        self.use_tool(_Tool(safety=SafetyTier.BLOCKED))
        result = self.dispatch()
        self.assertFalse(result.success)
        self.assertFalse(result.executed)
        self.assertTrue(result.blocked)
        self.assertEqual(getattr(result, "outcome", None), "blocked")
        self.assertEqual(getattr(result, "refusal", None), "")
        self.assertIn("classified BLOCKED", result.content)

    def test_a_spent_approval_arrives_not_run(self):
        token = dt.mint_token(_TOOL, _ARGS, self.uid, key=_KEY,
                              ttl_seconds=120)
        payload = dt.verify_token(token, _TOOL, _ARGS, self.uid,
                                  username=self.user)
        pd._consume_nonce(payload.nonce, payload.exp)
        with mock.patch.object(dt, "mint_token", return_value=token):
            result = self.dispatch()
        self.assertNotRun(result, "not_started", "already consumed (replay)")

    def test_an_approval_store_that_cannot_be_opened_arrives_unavailable(self):
        blocker = os.path.join(tempfile.mkdtemp(prefix="runnershape-b-"), "f")
        self.addCleanup(os.unlink, blocker)
        open(blocker, "w").close()
        with mock.patch.object(pd, "_CONSUMED_NONCE_PATH",
                               os.path.join(blocker, "consumed-nonces")):
            result = self.dispatch()
        self.assertNotRun(result, "unavailable",
                          "consumed-nonce store unavailable")

    def test_a_read_only_route_arrives_not_run(self):
        self.use_tool(_Tool(safety=SafetyTier.AUTO))
        result = self.dispatch()
        self.assertNotRun(result, "not_started",
                          "never routed to this boundary")

    def test_a_tool_that_raises_arrives_as_a_run_that_raised(self):
        self.use_tool(_Tool(raises=RuntimeError("a neutral failure")))
        result = self.dispatch()
        self.assertFalse(result.success)
        self.assertTrue(result.executed)
        self.assertEqual(getattr(result, "outcome", None), "")
        self.assertEqual(getattr(result, "refusal", ""), "")
        self.assertIn("raised RuntimeError: a neutral failure", result.content)
        self.assertNotIn(_PREFIX, result.content)


class AToolsOwnRefusalUnderTheRunnerKeepsItsShapeTests(_ChainTestCase):

    def _tool_result(self, **fields):
        return ToolResult(call_id="", name=_TOOL, **fields)

    def test_a_tool_refusal_arrives_with_its_flags_outcome_and_words(self):
        fields = dict(content="the tool's own refusal", success=False,
                      executed=False, blocked=False, outcome="path_refused")
        # A tree without the words field (before this correction) builds the
        # refusal without its words; the cell still fails there on executed.
        if "refusal" in ToolResult.__dataclass_fields__:
            fields["refusal"] = "the tool's own refusal"
        self.use_tool(_Tool(result=self._tool_result(**fields)))
        result = self.dispatch()
        self.assertNotRun(result, "path_refused", "the tool's own refusal")

    def test_a_tool_block_keeps_its_blocked_flag(self):
        self.use_tool(_Tool(result=self._tool_result(
            content="Command blocked by the safety classifier", success=False,
            executed=False, blocked=True)))
        result = self.dispatch()
        self.assertTrue(result.blocked)
        self.assertFalse(result.executed)

    def test_a_run_that_failed_stays_a_run(self):
        self.use_tool(_Tool(result=self._tool_result(
            content="the program exited 3", success=False)))
        result = self.dispatch()
        self.assertFalse(result.success)
        self.assertTrue(result.executed)
        self.assertEqual(getattr(result, "outcome", None), "")
        self.assertIn("the program exited 3", result.content)
        self.assertNotIn(_PREFIX, result.content)


class TextCannotPoseAsTheResultLineTests(_ChainTestCase):

    def _ran_and_failed_with(self, content_for):
        """Dispatch a tool that ran and failed, its content written when it
        runs, so the planted line may name this very request."""
        tool = _Tool()
        tool.execute = lambda arguments: ToolResult(
            call_id="", name=_TOOL, content=content_for(self.request_id),
            success=False)
        self.use_tool(tool)
        return self.dispatch()

    def test_content_carrying_this_requests_line_mid_output_does_not_pose(self):
        result = self._ran_and_failed_with(
            lambda rid: "line one\n" + _planted_line(rid) + "\nline three")
        self.assertTrue(result.executed)
        self.assertEqual(getattr(result, "outcome", None), "")
        self.assertIn("line three", result.content)

    def test_content_ending_in_this_requests_line_does_not_pose(self):
        result = self._ran_and_failed_with(
            lambda rid: "line one\n" + _planted_line(rid))
        self.assertTrue(result.executed)
        self.assertEqual(getattr(result, "refusal", ""), "")
        self.assertIn(_planted_line(self.request_id), result.content)

    def test_a_final_line_naming_another_request_is_read_as_output(self):
        result = self.dispatch(canned_stdout=lambda rid: (
            "output\n" + _planted_line("0" * 32) + "\n"))
        self.assertTrue(result.executed)
        self.assertIn(_PREFIX, result.content)

    def test_a_final_line_not_in_the_dispatchers_shape_is_read_as_output(self):
        result = self.dispatch(canned_stdout=lambda rid: (
            "output\n" + _PREFIX + json.dumps({"request_id": rid,
                                               "executed": "no"}) + "\n"))
        self.assertTrue(result.executed)

    def test_the_dispatchers_own_shape_for_this_request_is_read(self):
        result = self.dispatch(canned_stdout=lambda rid: (
            "output\n" + _planted_line(rid, refusal="the planted words")
            + "\n"))
        self.assertFalse(result.executed)
        self.assertEqual(getattr(result, "refusal", None), "the planted words")
        self.assertNotIn(_PREFIX, result.content)

    def test_the_line_is_the_one_these_cells_plant(self):
        self.use_tool(_Tool(validation_error="a neutral refusal"))
        self.dispatch()
        last = self.dispatcher_output.rstrip("\n").rpartition("\n")[2]
        self.assertTrue(last.startswith(_PREFIX), last)
        body = json.loads(last[len(_PREFIX):])
        self.assertEqual(sorted(body), ["blocked", "executed", "outcome",
                                        "refusal", "request_id"])


class ASuccessIsReadAsBeforeTests(_ChainTestCase):

    def test_a_success_carries_no_result_line_and_its_content_whole(self):
        self.use_tool(_Tool(result=ToolResult(
            call_id="", name=_TOOL, content="installed\n", success=True)))
        result = self.dispatch()
        self.assertTrue(result.success)
        self.assertTrue(result.executed)
        self.assertEqual(result.content, "installed")
        self.assertNotIn(_PREFIX, self.dispatcher_output)


if __name__ == "__main__":
    unittest.main()
