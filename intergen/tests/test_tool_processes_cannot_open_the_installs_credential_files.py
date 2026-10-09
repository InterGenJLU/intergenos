# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A tool's own process cannot open the install's credential files.

run_command's child and an MCP server open files themselves, so no check inside
the daemon sees those opens. A second read of the previous correction reached
the signing key through run_command by a parent-directory spelling, a symbolic
link and a search of the containing directory, each executed with no review.
These cells fire those three routes, and the two canonical spellings, at a
tool's process on FAKE credentials and require every one to be refused, with an
ordinary read beside them still running. They also hold the boundary's other
promises: a tool's process cannot ask the owner's user manager for anything, an
MCP server stands behind the same boundary and its service stops with it, a
command that overruns is stopped, an absent credential starts nothing, the
service carries the daemon unit's hardening line for line, and a credential with
a second name on disk is reported.

Every credential here is generated into a fixture the tool's service can see
(never the real ~/.config/intergen; see intergen/tests/tool_process_fixture.py),
so a refusal reads "Permission denied" from the mask and cannot be an absent
file passing for a refused one. The cells that start a process need this
session's systemd user manager and are skipped, by name, where none answers.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

from intergen import tool_registry
from intergen.interfaces.mcp import MCPServerConfig, MCPTrustTier
from intergen.interfaces.provenance import Provenance
from intergen.interfaces.types import ToolCall
from intergen.mcp_client import _MCPServerConnection
from intergen.tests.tool_process_fixture import (
    NO_USER_MANAGER,
    install_lives_in,
    user_manager_answers,
    visible_directory,
    write_fake_credentials,
)
from intergen.tool_registry import ToolRegistry
from intergen.tools.run_command import RunCommandTool

_UNIT_FILE = Path(__file__).resolve().parents[1] / "data" / "intergen.service"

#: [Service] keys that describe the daemon's own start and life, not its sandbox.
_LIFECYCLE_KEYS = {"Type", "BusName", "ExecCondition", "ExecStart", "Restart",
                   "RestartSec", "Environment"}


class _AFixtureInstall(unittest.TestCase):
    """A fixture home with fake credentials that the owning-account lookup finds."""

    def setUp(self) -> None:
        self._dir = visible_directory("intergen-boundary-case-")
        self.addCleanup(self._dir.cleanup)
        self.home = Path(self._dir.name)
        self.secrets = write_fake_credentials(self.home)
        self.keydir = self.home / ".config" / "intergen"
        self.key_path = self.keydir / "dispatch-key"
        self.token_path = self.keydir / "web-token"
        (self.keydir / "detour").mkdir()
        self.alias = self.home / "key-alias"
        self.alias.symlink_to(self.key_path)
        self.notes = self.home / "notes.txt"
        self.notes.write_text("an ordinary file the owner reads\n", encoding="utf-8")
        patcher = install_lives_in(self.home)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _secrets_in(self, *texts: str) -> list[str]:
        joined = "\n".join(t or "" for t in texts)
        return sorted(name for name, value in self.secrets.items() if value in joined)


class ThroughRunCommand(_AFixtureInstall):
    """The review's three routes and the two canonical spellings, at the process."""

    @classmethod
    def setUpClass(cls) -> None:
        if not user_manager_answers():
            raise unittest.SkipTest(NO_USER_MANAGER)

    def setUp(self) -> None:
        super().setUp()
        self.registry = ToolRegistry()
        self.registry.discover_tools()
        self.audited: list[object] = []
        self.reviews: list[str] = []

    def _review_callback(self, call: ToolCall, decision: object) -> str:
        self.reviews.append(call.name)
        return "deny"

    def _dispatch(self, command: str, provenance=Provenance.USER_DIRECT):
        call = ToolCall(name="run_command", arguments={"command": command},
                        source_of_request=provenance)
        with mock.patch.object(tool_registry, "write_record", self.audited.append):
            return self.registry.execute(call, review_callback=self._review_callback)

    def _audit_text(self) -> str:
        return "\n".join(str(getattr(r, "result_summary", "")) for r in self.audited)

    def _assert_refused_at_the_process(self, result, route: str,
                                       names=("dispatch-key",)) -> None:
        reached = self._secrets_in(result.content, result.model_summary,
                                   self._audit_text())
        self.assertEqual(
            [n for n in reached if n in names], [],
            f"{', '.join(reached)} reached the tool result or the audit record "
            f"through {route}: the tool's process opened the credential file")
        self.assertIn(
            "Permission denied", result.content or "",
            f"the refusal through {route} must be the mask's answer, not an "
            f"absent file; content was {result.content!r}")
        self.assertFalse(result.success)

    def test_a_parent_directory_spelling_is_refused_at_the_process(self) -> None:
        spelled = self.keydir / "detour" / ".." / "dispatch-key"
        self._assert_refused_at_the_process(
            self._dispatch(f"cat {spelled}"), "a parent-directory spelling")

    def test_a_symbolic_link_is_refused_at_the_process(self) -> None:
        self._assert_refused_at_the_process(
            self._dispatch(f"cat {self.alias}"), "a symbolic link")

    def test_a_search_of_the_containing_directory_is_refused(self) -> None:
        """The process derives the protected children itself; neither is read."""
        result = self._dispatch(f"grep -r . {self.keydir}")
        self._assert_refused_at_the_process(
            result, "a search of the containing directory",
            names=("dispatch-key", "web-token"))

    def test_the_canonical_spelling_of_the_key_is_refused_at_the_process(self) -> None:
        """Fired at the tool itself, past the dispatcher's screen.

        The screen refuses this spelling before any process starts, so through
        the registry this cell would measure the screen. Here the spelling
        reaches the process, and the process must still be unable to open it.
        """
        result = RunCommandTool().execute({"command": f"cat {self.key_path}"})
        self.assertEqual(self._secrets_in(result.content), [])
        self.assertIn("Permission denied", result.content or "")
        self.assertFalse(result.success)

    def test_the_canonical_spelling_of_the_token_is_refused_at_the_process(self) -> None:
        result = RunCommandTool().execute({"command": f"cat {self.token_path}"})
        self.assertEqual(self._secrets_in(result.content), [])
        self.assertIn("Permission denied", result.content or "")
        self.assertFalse(result.success)

    def test_an_ordinary_read_beside_the_credentials_still_runs(self) -> None:
        result = self._dispatch(f"cat {self.notes}")
        self.assertTrue(result.success, f"content was {result.content!r}")
        self.assertIn("an ordinary file the owner reads", result.content or "")

    def test_a_tool_process_cannot_ask_the_user_manager(self) -> None:
        """Fired at the tool's process itself, past the review.

        Through the registry this command is not read-only, and a run_command
        that is not read-only is in the privileged tier: it goes to the review
        surface and, approved, runs through the privileged runner, never as the
        daemon's child. The process boundary is what an MCP server and
        run_command's read-only children stand behind, so the cell asks the
        question at the process: the owner's user manager, which could start a
        service without the mask, must be out of its reach.
        """
        result = RunCommandTool().execute(
            {"command": "systemctl --user is-system-running"})
        first_line = (result.content or "").strip().split("\n", 1)[0].strip()
        self.assertNotIn(
            first_line, {"running", "degraded", "starting", "initializing"},
            "the tool's process reached the owner's user manager")
        self.assertIn("Failed to connect", result.content or "")
        self.assertFalse(result.success)


class AnMCPServer(_AFixtureInstall):
    """An MCP server is a tool's process too."""

    _SERVER = textwrap.dedent(
        """
        import json, os, sys

        def send(obj):
            sys.stdout.write(json.dumps(obj) + "\\n")
            sys.stdout.flush()

        def read_all(directory):
            out = []
            for root, _dirs, files in os.walk(directory):
                for name in sorted(files):
                    path = os.path.join(root, name)
                    try:
                        with open(path) as fh:
                            out.append(path + ":" + fh.read())
                    except OSError as exc:
                        out.append(path + ": " + exc.strerror)
            return "\\n".join(out)

        def run(argv):
            import subprocess
            done = subprocess.run(argv, capture_output=True, text=True)
            return done.stdout + done.stderr

        def read_one(path):
            try:
                with open(path) as fh:
                    return fh.read()
            except OSError as exc:
                return path + ": " + exc.strerror

        for line in sys.stdin:
            msg = json.loads(line)
            method, mid = msg.get("method"), msg.get("id")
            if method == "initialize":
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "protocolVersion": "2024-11-05", "capabilities": {},
                    "serverInfo": {"name": "fixture-reader", "version": "1"}}})
            elif method == "tools/list":
                send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
                    {"name": "read", "description": "Read one file.",
                     "inputSchema": {"type": "object",
                                     "properties": {"path": {"type": "string"}}}},
                    {"name": "read_all", "description": "Read every file under a directory.",
                     "inputSchema": {"type": "object",
                                     "properties": {"directory": {"type": "string"}}}},
                    {"name": "run", "description": "Run one command.",
                     "inputSchema": {"type": "object",
                                     "properties": {"argv": {"type": "array"}}}},
                ]}})
            elif method == "tools/call":
                params = msg.get("params", {})
                args = params.get("arguments", {})
                name = params.get("name")
                text = (read_all(args["directory"]) if name == "read_all"
                        else run(args["argv"]) if name == "run"
                        else read_one(args["path"]))
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": text}]}})
            elif mid is not None:
                send({"jsonrpc": "2.0", "id": mid,
                      "error": {"code": -32601, "message": "method not found"}})
        """
    )

    @classmethod
    def setUpClass(cls) -> None:
        if not user_manager_answers():
            raise unittest.SkipTest(NO_USER_MANAGER)

    def setUp(self) -> None:
        super().setUp()
        server = self.home / "fixture_reader_server.py"
        server.write_text(self._SERVER, encoding="utf-8")
        self.conn = _MCPServerConnection("fixture", MCPServerConfig(
            name="fixture", command=sys.executable, args=["-I", str(server)],
            trust_tier=MCPTrustTier.UNTRUSTED))
        self.conn.connect()
        self.addCleanup(self.conn.disconnect)

    def test_an_mcp_server_cannot_open_the_credentials_by_any_route(self) -> None:
        answers = {
            "a parent-directory spelling": self.conn.call(
                "read", {"path": str(self.keydir / "detour" / ".." / "dispatch-key")}),
            "a symbolic link": self.conn.call("read", {"path": str(self.alias)}),
            "a walk of the containing directory": self.conn.call(
                "read_all", {"directory": str(self.keydir)}),
            "the canonical spelling of the token": self.conn.call(
                "read", {"path": str(self.token_path)}),
        }
        for route, text in answers.items():
            with self.subTest(route=route):
                self.assertEqual(self._secrets_in(text), [],
                                 f"the MCP server read a credential through {route}")
                self.assertIn("Permission denied", text)
        ordinary = self.conn.call("read", {"path": str(self.notes)})
        self.assertIn("an ordinary file the owner reads", ordinary)

    def test_an_mcp_server_cannot_ask_the_user_manager(self) -> None:
        """An external tool is not in the privileged tier, so the registry runs
        it without review on a user-direct or user-implied call; the owner's
        user manager, which could start a service without the mask, must be out
        of the server's reach."""
        text = self.conn.call("run", {"argv": ["systemctl", "--user",
                                               "is-system-running"]})
        first_line = text.strip().split("\n", 1)[0].strip()
        self.assertNotIn(first_line, {"running", "degraded", "starting", "initializing"},
                         "the MCP server reached the owner's user manager")
        self.assertIn("Failed to connect", text)

    def test_disconnect_stops_the_servers_service(self) -> None:
        unit = self.conn._unit
        self.assertTrue(unit, "the server must run as a named service of its own")
        self.conn.disconnect()
        state = subprocess.run(
            ["systemctl", "--user", "is-active", unit], capture_output=True,
            text=True, timeout=10, stdin=subprocess.DEVNULL, check=False)
        self.assertNotEqual(state.stdout.strip(), "active",
                            "the server's service outlived its disconnect")


class TheLauncher(_AFixtureInstall):
    """What the service is given, and what happens when it cannot be given it."""

    def test_the_service_carries_the_daemon_units_hardening_line_for_line(self) -> None:
        from intergen import tool_process
        # The unit repeats keys (DeviceAllow=, SystemCallFilter=), so its lines
        # are read in order rather than through a parser's mapping.
        lines, section = [], None
        for raw in _UNIT_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("[") and line.endswith("]"):
                section = line
                continue
            if section != "[Service]" or not line or line.startswith("#"):
                continue
            key = line.split("=", 1)[0]
            if key not in _LIFECYCLE_KEYS:
                lines.append(line)
        self.assertEqual(
            tuple(lines), tool_process.DAEMON_HARDENING,
            "a tool's service must carry the daemon unit's hardening verbatim; "
            "a line changed in intergen/data/intergen.service is changed in "
            "intergen/tool_process.py too")
        launch = tool_process.launch_argv(["true"], unit="intergen-tool-case.service")
        properties = [a[len("--property="):] for a in launch if a.startswith("--property=")]
        for line in tool_process.DAEMON_HARDENING:
            self.assertIn(line, properties)

    def test_the_launch_masks_both_credentials_and_hands_over_the_command(self) -> None:
        from intergen import tool_process
        launch = tool_process.launch_argv(
            ["cat", "/etc/hostname"], unit="intergen-tool-case.service",
            runtime_max_sec=7)
        self.assertEqual(launch[0], tool_process.SYSTEMD_RUN)
        for expected in (
                f'--property=InaccessiblePaths="{self.key_path}"',
                f'--property=InaccessiblePaths="{self.token_path}"',
                "--property=RuntimeMaxSec=7",
                "--expand-environment=no",
                "--user", "--wait", "--pipe", "--collect"):
            self.assertIn(expected, launch)
        self.assertEqual(launch[launch.index("--"):], ["--", "cat", "/etc/hostname"])

    def test_the_launch_masks_the_manager_sockets(self) -> None:
        from intergen import tool_process
        runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": runtime}):
            launch = tool_process.launch_argv(
                ["true"], unit="intergen-tool-case.service")
        for expected in (
                f'--property=InaccessiblePaths="-{runtime}/systemd/private"',
                f'--property=InaccessiblePaths="-{runtime}/bus"'):
            self.assertIn(expected, launch)

    def test_an_absent_credential_starts_nothing(self) -> None:
        self.token_path.unlink()
        result = RunCommandTool().execute({"command": f"cat {self.notes}"})
        self.assertFalse(result.success)
        self.assertTrue(result.blocked)
        self.assertTrue((result.content or "").startswith("This command was not run:"),
                        f"content was {result.content!r}")
        self.assertIn(str(self.token_path), result.content)
        self.assertNotIn("an ordinary file the owner reads", result.content)

    def test_an_unreadable_credential_path_is_refused_with_its_own_reason(
            self) -> None:
        """A path that cannot be read is not reported as a missing file."""
        from intergen import tool_process
        self.keydir.chmod(0)
        try:
            try:
                os.stat(self.key_path)
            except PermissionError:
                pass
            else:
                self.skipTest(
                    "this process can stat through an unsearchable directory "
                    "(it holds CAP_DAC_OVERRIDE), so the case cannot be made here")
            with self.assertRaises(tool_process.ToolProcessNotStarted) as caught:
                tool_process.launch_argv(["true"], unit="intergen-tool-case.service")
        finally:
            self.keydir.chmod(0o700)
        message = str(caught.exception)
        self.assertIn("could not be read", message)
        self.assertNotIn("does not exist", message)

    def test_a_credential_path_that_runs_through_a_file_is_not_called_missing(
            self) -> None:
        """Only "no such file" is absence; "not a directory" is a failed read."""
        from intergen import tool_process
        os.rename(self.keydir, self.home / "moved-aside")
        self.keydir.write_text("an ordinary file\n", encoding="utf-8")
        with self.assertRaises(tool_process.ToolProcessNotStarted) as caught:
            tool_process.launch_argv(["true"], unit="intergen-tool-case.service")
        message = str(caught.exception)
        self.assertIn("could not be read", message)
        self.assertNotIn("does not exist", message)

    def test_a_second_name_for_a_credential_is_reported(self) -> None:
        from intergen import tool_process
        second = self.home / "second-name"
        os.link(self.key_path, second)
        with mock.patch.object(tool_process, "_LINK_COUNTS_READ_BACK", False):
            with self.assertLogs("intergen.tool_process", "WARNING") as logs:
                warned = tool_process.read_back_link_counts()
        self.assertEqual(len(warned), 1)
        self.assertIn(f"{self.key_path} has 2 names on disk", logs.output[0])
        second.unlink()
        with mock.patch.object(tool_process, "_LINK_COUNTS_READ_BACK", False):
            with self.assertNoLogs("intergen.tool_process", "WARNING"):
                self.assertEqual(tool_process.read_back_link_counts(), [])

    def test_a_command_that_overruns_is_stopped_with_its_service(self) -> None:
        if not user_manager_answers():
            self.skipTest(NO_USER_MANAGER)
        from intergen import tool_process
        unit = "intergen-tool-overrun-case-" + os.urandom(4).hex() + ".service"
        started = time.monotonic()
        with mock.patch.object(tool_process, "new_unit_name", return_value=unit):
            result = RunCommandTool().execute({"command": "sleep 30", "timeout": 2})
        elapsed = time.monotonic() - started
        self.assertEqual(result.content, "Command timed out after 2 seconds: sleep 30")
        self.assertFalse(result.success)
        self.assertLess(elapsed, 2 + tool_process.CLIENT_GRACE_SECONDS)
        state = subprocess.run(
            ["systemctl", "--user", "is-active", unit], capture_output=True,
            text=True, timeout=10, stdin=subprocess.DEVNULL, check=False)
        self.assertNotEqual(state.stdout.strip(), "active",
                            "the command's service outlived its timeout")


if __name__ == "__main__":
    unittest.main()
