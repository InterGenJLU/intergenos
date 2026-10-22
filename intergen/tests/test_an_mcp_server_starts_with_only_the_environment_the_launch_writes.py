# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""An MCP server starts with the environment written for it and its configured names, nothing else.

A server's service is spawned by the user manager with the manager's own
environment block, which any process of the account can change, and the block
is compiled into the service's environment at the moment of the spawn. A value
there can name a program the server then runs (LD_PRELOAD, NODE_OPTIONS,
PYTHONPATH), and a directory first on the daemon's PATH would decide which
program a configured name starts. These cells hold the launch's answer: the
program is resolved on the fixed system PATH or given as an absolute path; the
service unsets glibc's unsafe variables; a fixed launcher on the system's
interpreter, started with -I, keeps only the written names and the configured
ones and execs the server with those alone, so a name set in the block at any
moment never reaches it; and a configured value is handed over by name, never
on a command line.

The cells that start a server need this session's systemd user manager and are
skipped, by name, where none answers. No cell writes into the user manager's
own block or search path.
"""

from __future__ import annotations

import contextlib
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from intergen import mcp_client, tool_process
from intergen.interfaces.mcp import MCPServerConfig, MCPTrustTier
from intergen.mcp_client import _MCPServerConnection
from intergen.tests.tool_process_fixture import (
    NO_USER_MANAGER, install_lives_in, unit_search_path_is, user_manager_answers,
    visible_directory, write_fake_credentials,
)

#: Every name a server's written environment holds besides LANG and LC_*.
_ALLOWLIST = {"PATH", "HOME", "USER", "LOGNAME", "XDG_RUNTIME_DIR",
              "DBUS_SESSION_BUS_ADDRESS", "TERM", "npm_config_update_notifier",
              "CUPS_SERVER", "PYTHONNOUSERSITE"}

#: Values another hand could have put in the user manager's block, and so in
#: the daemon's own environment, each naming a program a server would run, a
#: place it would read one from, or a destination it would contact.
_PLANTED = {
    "LD_PRELOAD": "/nonexistent/preload.so",
    "NODE_OPTIONS": "--require /nonexistent/hook.js",
    "PYTHONPATH": "/nonexistent/site",
    "BASH_ENV": "/nonexistent/bash-env",
    "CUPS_SERVER": "printer.invalid:631",
    "HOME": "/nonexistent/home",
    "XDG_RUNTIME_DIR": "/nonexistent/run",
    "DBUS_SESSION_BUS_ADDRESS": "unix:path=/nonexistent/bus",
    "PATH": "/nonexistent/bin:/usr/bin",
    "LANG": "C.UTF-8",
}

#: A configured value with the characters a command line would mangle or show.
_CONFIGURED = {"INTERGEN_CASE_SETTING": "a value with spaces & $dollar 'quotes'",
               "INTERGEN_CASE_TOKEN": "case-token-0123456789"}


class _Stop(Exception):
    """Raised by the stand-in for Popen once the launch has been recorded."""


@contextlib.contextmanager
def _fake_credentials():
    """Two fake credential files for the launcher's mask, in a throwaway directory."""
    with tempfile.TemporaryDirectory() as home:
        paths = tuple(Path(home, name) for name in ("dispatch-key", "web-token"))
        for path in paths:
            path.write_text("fake\n", encoding="utf-8")
        with mock.patch.object(tool_process, "credential_paths", return_value=paths):
            yield


def _config(command: str = "/usr/bin/python3", env: dict[str, str] | None = None,
            args: list[str] | None = None) -> MCPServerConfig:
    return MCPServerConfig(name="case", command=command, args=list(args or ["server.py"]),
                           env=dict(_CONFIGURED if env is None else env),
                           trust_tier=MCPTrustTier.UNTRUSTED)


def _parts(launch: list[str]) -> tuple[list[str], list[str]]:
    """A launch split at its "--": systemd-run's options, then what the service runs."""
    split = launch.index("--")
    return launch[:split], launch[split + 1:]


class _ALaunchRecorded(unittest.TestCase):
    """connect() up to the start: the line and the environment Popen is given."""

    def _launch(self, config: MCPServerConfig, environ: dict[str, str] | None = None):
        seen: dict[str, object] = {}

        def popen(args, *a, **kw):
            seen["argv"] = [str(x) for x in args]
            seen["env"] = kw.get("env")
            raise _Stop

        with tempfile.TemporaryDirectory() as empty, _fake_credentials(), \
                unit_search_path_is(empty), \
                mock.patch.dict(os.environ, _PLANTED if environ is None else environ, clear=True), \
                mock.patch.object(subprocess, "Popen", popen):
            written = tool_process.tool_environment()
            with self.assertRaises(_Stop):
                _MCPServerConnection("case", config).connect()
        return seen["argv"], seen["env"], written


class TheServersLaunchLine(_ALaunchRecorded):
    """What systemd-run is given for a server."""

    def test_the_service_unsets_the_loader_list_before_the_launcher_starts(self) -> None:
        options, _ = _parts(self._launch(_config())[0])
        self.assertIn("--property=UnsetEnvironment="
                      + " ".join(tool_process.LOADER_UNSAFE_VARIABLES), options)

    def test_the_launcher_starts_first_on_the_systems_interpreter_isolated(self) -> None:
        _, own = _parts(self._launch(_config())[0])
        self.assertEqual(own[:4], ["/usr/bin/python3", "-I", "-c", mcp_client._LAUNCHER])

    def test_the_launcher_keeps_the_written_names_and_the_configured_ones_alone(self) -> None:
        launch, _, written = self._launch(_config())
        _, own = _parts(launch)
        self.assertEqual(sorted(own[4].split()), sorted(set(written) | set(_CONFIGURED)))
        for name in _PLANTED:
            if name not in written:
                with self.subTest(name=name):
                    self.assertNotIn(name, own[4].split())

    def test_the_server_follows_the_launcher_with_its_arguments(self) -> None:
        _, own = _parts(self._launch(_config(args=["-u", "server.py", "a b", ""]))[0])
        self.assertEqual(own[5:], ["/usr/bin/python3", "-u", "server.py", "a b", ""])

    def test_the_written_values_are_set_on_the_service_and_nothing_of_the_block(self) -> None:
        launch, _, written = self._launch(_config(env={}))
        options, _ = _parts(launch)
        assigned = dict(a[len("--setenv="):].split("=", 1)
                        for a in options if a.startswith("--setenv=") and "=" in a[9:])
        self.assertEqual(assigned, written)
        self.assertEqual(assigned["HOME"], pwd.getpwuid(os.getuid()).pw_dir)
        self.assertEqual(assigned["PATH"], tool_process.TOOL_PATH)
        for name, value in _PLANTED.items():
            if name != "LANG":
                with self.subTest(name=name):
                    self.assertNotEqual(assigned.get(name), value)

    def test_a_configured_value_is_handed_over_by_name_and_on_no_command_line(self) -> None:
        launch, client_env, _ = self._launch(_config())
        options, _ = _parts(launch)
        for name, value in _CONFIGURED.items():
            with self.subTest(name=name):
                self.assertIn(f"--setenv={name}", options)
                self.assertFalse([a for a in launch if value in a],
                                 "a configured value is on the command line")
                self.assertEqual(client_env[name], value)

    def test_a_configured_name_overrides_the_written_one(self) -> None:
        launch, client_env, _ = self._launch(_config(env={"PATH": "/opt/server/bin:/usr/bin"}))
        options, own = _parts(launch)
        self.assertIn("--setenv=PATH", options)
        self.assertFalse([a for a in options if a.startswith("--setenv=PATH=")], options)
        self.assertIn("PATH", own[4].split())
        self.assertEqual(client_env["PATH"], "/opt/server/bin:/usr/bin")

    def test_a_configured_loader_variable_starts_nothing(self) -> None:
        for name in ("LD_PRELOAD", "TMPDIR"):
            with self.subTest(name=name):
                with _fake_credentials(), mock.patch.object(subprocess, "Popen") as popen:
                    with self.assertRaises(tool_process.ToolProcessNotStarted) as caught:
                        _MCPServerConnection("case", _config(env={name: "/x"})).connect()
                self.assertIn(name, str(caught.exception))
                popen.assert_not_called()

    def test_a_configured_name_that_is_no_variable_name_starts_nothing(self) -> None:
        for name in ("A B", "A=B", "", "1A", "A\nB"):
            with self.subTest(name=name):
                with _fake_credentials(), mock.patch.object(subprocess, "Popen") as popen:
                    with self.assertRaises(tool_process.ToolProcessNotStarted) as caught:
                        _MCPServerConnection("case", _config(env={name: "x"})).connect()
                self.assertIn(repr(name), str(caught.exception))
                popen.assert_not_called()


class TheServersProgramIsResolvedOnTheFixedPath(_ALaunchRecorded):
    """A configured name is looked up where commands run, never on the daemon's PATH."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = scratch.name
        for name in ("python3", "intergen-planted-server"):
            planted = Path(self.scratch, name)
            planted.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            planted.chmod(0o755)
        self.inherited = dict(_PLANTED, PATH=self.scratch + os.pathsep + "/usr/bin")

    def test_a_directory_first_in_the_inherited_path_does_not_choose_the_program(self) -> None:
        _, own = _parts(self._launch(_config(command="python3"), self.inherited)[0])
        self.assertEqual(own[5], shutil.which("python3", path=tool_process.TOOL_PATH))
        self.assertFalse(own[5].startswith(self.scratch + os.sep), own[5])

    def test_a_configured_path_does_not_choose_the_program_either(self) -> None:
        env = dict(_CONFIGURED, PATH=self.scratch + os.pathsep + "/usr/bin")
        _, own = _parts(self._launch(_config(command="python3", env=env), self.inherited)[0])
        self.assertEqual(own[5], shutil.which("python3", path=tool_process.TOOL_PATH))

    def test_a_name_only_the_inherited_path_holds_is_not_found(self) -> None:
        with mock.patch.dict(os.environ, self.inherited, clear=True), \
                mock.patch.object(subprocess, "Popen") as popen:
            with self.assertRaises(FileNotFoundError):
                _MCPServerConnection("case", _config(command="intergen-planted-server")).connect()
        popen.assert_not_called()

    def test_a_configured_absolute_path_is_started_as_configured(self) -> None:
        program = Path(self.scratch, "intergen-planted-server")
        _, own = _parts(self._launch(_config(command=str(program)), self.inherited)[0])
        self.assertEqual(own[5], str(program))


class TheLauncherItself(unittest.TestCase):
    """The launcher's own code, run as the service runs it (no service here)."""

    def _run(self, keep: str, program: list[str], environ: dict[str, str]):
        return subprocess.run(["/usr/bin/python3", "-I", "-c", mcp_client._LAUNCHER, keep] + program,
                              env=environ, capture_output=True, text=True, timeout=30,
                              stdin=subprocess.DEVNULL, check=False)

    def test_the_program_gets_the_kept_names_alone(self) -> None:
        environ = dict(_PLANTED, KEPT_A="a value with spaces", KEPT_B="$HOME 'q' \\ \"",
                       NOT_KEPT="dropped")
        environ.pop("LD_PRELOAD")
        done = self._run("KEPT_A KEPT_B", ["/usr/bin/env", "-0"], environ)
        self.assertEqual(done.returncode, 0, done.stderr)
        seen = dict(item.split("=", 1) for item in done.stdout.split("\0") if item)
        self.assertEqual(seen, {"KEPT_A": "a value with spaces", "KEPT_B": "$HOME 'q' \\ \""})

    def test_nothing_in_the_environment_runs_in_the_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as site:
            marker = Path(site, "ran")
            Path(site, "sitecustomize.py").write_text(
                f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8")
            Path(site, "startup.py").write_text(
                f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8")
            environ = {"PATH": "/usr/bin", "PYTHONPATH": site, "PYTHONSTARTUP": str(Path(site, "startup.py")),
                       "PYTHONHOME": "/nonexistent/home", "PYTHONINSPECT": "1"}
            done = self._run("PATH", ["/usr/bin/true"], environ)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertFalse(marker.exists(), "the environment ran code in the launcher")

    def test_the_programs_arguments_arrive_unchanged(self) -> None:
        done = self._run("PATH", ["/usr/bin/printf", "%s|", "a b", "", "--x", "$y", "'q'"],
                         {"PATH": "/usr/bin"})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "a b||--x|$y|'q'|")


class TheServerAtARealStart(unittest.TestCase):
    """A server started through the user manager sees the written names and its own, nothing else."""

    _SERVER = textwrap.dedent(
        """
        import json, os, sys

        # The environment the server was started with, as the kernel holds it
        # (the interpreter may add to os.environ at its own start).
        with open("/proc/self/environ", "rb") as fh:
            started = dict(item.decode().split("=", 1)
                           for item in fh.read().split(b"\\0") if item)

        for line in sys.stdin:
            msg = json.loads(line)
            method, mid = msg.get("method"), msg.get("id")
            if method == "initialize":
                result = {"protocolVersion": "2024-11-05", "capabilities": {},
                          "serverInfo": {"name": "environment-reporter", "version": "1"}}
            elif method == "tools/list":
                result = {"tools": [{"name": "environment", "inputSchema": {"type": "object"},
                                     "description": json.dumps({"env": started,
                                                                "argv": sys.orig_argv})}]}
            elif mid is None:
                continue
            else:
                result = {}
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}) + "\\n")
            sys.stdout.flush()
        """
    )

    @classmethod
    def setUpClass(cls) -> None:
        if not user_manager_answers():
            raise unittest.SkipTest(NO_USER_MANAGER)

    def setUp(self) -> None:
        directory = visible_directory("intergen-mcp-environment-case-")
        self.addCleanup(directory.cleanup)
        home = Path(directory.name)
        write_fake_credentials(home)
        patcher = install_lives_in(home)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.server = home / "environment_reporter.py"
        self.server.write_text(self._SERVER, encoding="utf-8")

    def test_the_server_sees_the_written_environment_and_its_configured_names_alone(self) -> None:
        conn = _MCPServerConnection("case", _config(command="python3", args=[str(self.server)]))
        planted = {k: v for k, v in _PLANTED.items()
                   if k not in {"PATH", "HOME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "LANG"}}
        with mock.patch.dict(os.environ, planted):
            conn.connect()
        self.addCleanup(conn.disconnect)
        report = json.loads(conn.list_tools()[0].schema.description)
        seen = report["env"]
        locale = {k for k, v in os.environ.items()
                  if re.fullmatch(r"LANG|LC_[A-Z]+", k) and re.fullmatch(r"[A-Za-z0-9_.@-]+", v)}
        self.assertEqual(set(seen), _ALLOWLIST | locale | set(_CONFIGURED),
                         "the user manager's block, the daemon's environment or the "
                         "service's own variables reached the server")
        for name, value in _CONFIGURED.items():
            self.assertEqual(seen[name], value)
        self.assertEqual(seen["HOME"], pwd.getpwuid(os.getuid()).pw_dir)
        self.assertEqual(seen["PATH"], tool_process.TOOL_PATH)
        self.assertEqual(report["argv"], [shutil.which("python3", path=tool_process.TOOL_PATH),
                                          str(self.server)])


if __name__ == "__main__":
    unittest.main()
