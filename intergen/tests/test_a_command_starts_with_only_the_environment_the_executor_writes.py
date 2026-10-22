# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A command the executor runs starts with the environment written for it, and nothing else.

A tool's service inherits the user manager's environment block, which any
process of the account can change, and a drop-in directory in the manager's
unit search path applies to every service whose name it matches. A value in
either can name a program a read-only command then runs (BASH_ENV,
NODE_OPTIONS, LD_PRELOAD, a PATH with the account's own directory first) or a
destination it contacts (CUPS_SERVER), and the command line shows none of it.
These cells hold the executor's answer: the service unsets glibc's unsafe
variables, env -i starts the command with an allowlist whose values come from
the password database and the account's id, the program is resolved on a fixed
system PATH, and a drop-in where the account can write refuses the start by
name. (git, which also reads configuration from the work tree it stands in,
asks first in every form: test_safety_interactive_escape.)

No cell writes into the user manager's own search path: the drop-ins are made
in scratch directories, and the manager's reply naming its search path is
answered with those directories at the process boundary (busctl), so the
launcher's own read and parse of the reply run. The cells that start a process
need this session's systemd user manager and are skipped, by name, where none
answers.
"""

from __future__ import annotations

import contextlib
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from intergen import tool_process
from intergen.tests.tool_process_fixture import (
    NO_USER_MANAGER, install_lives_in, user_manager_answers, visible_directory,
    write_fake_credentials, written_environment_and_program,
)
from intergen.tools.run_command import RunCommandTool

_REAL_RUN = subprocess.run

#: Every name a command's environment holds besides LANG and LC_*.
_ALLOWLIST = {"PATH", "HOME", "USER", "LOGNAME", "XDG_RUNTIME_DIR",
              "DBUS_SESSION_BUS_ADDRESS", "TERM", "npm_config_update_notifier",
              "CUPS_SERVER", "PYTHONNOUSERSITE"}

#: Values another hand could have put in the user manager's block, each naming a
#: program a command would run, a place it would read one from, or a
#: destination it would contact.
_PLANTED = {
    "BASH_ENV": "/nonexistent/bash-env",
    "NODE_OPTIONS": "--require /nonexistent/hook.js",
    "LD_PRELOAD": "/nonexistent/preload.so",
    "LD_AUDIT": "/nonexistent/audit.so",
    "RIPGREP_CONFIG_PATH": "/nonexistent/ripgreprc",
    "PYTHONSTARTUP": "/nonexistent/startup.py",
    "PYTHONPATH": "/nonexistent/site",
    "OPENSSL_CONF": "/nonexistent/openssl.cnf",
    "GIT_CONFIG_GLOBAL": "/nonexistent/gitconfig",
    "CUPS_SERVER": "printer.invalid:631",
    "npm_config_registry": "http://registry.invalid/",
    "HOME": "/nonexistent/home",
    "XDG_RUNTIME_DIR": "/nonexistent/run",
    "DBUS_SESSION_BUS_ADDRESS": "unix:path=/nonexistent/bus",
    "TERM": "xterm-256color",
    "PATH": "/nonexistent/bin:/usr/bin",
}

_UNIT = "intergen-tool-0123456789abcdef.service"


class _Runs:
    """subprocess.run as the launcher meets it: busctl's read of the manager's
    UnitPath is answered with `directories`, as busctl prints it; every other
    process is recorded and, with `start`, really started."""

    def __init__(self, directories, *, start: bool = False) -> None:
        self.directories = [str(d) for d in directories]
        self.start = start
        self.asked: list[tuple[list[str], dict | None]] = []
        self.launched: list[list[str]] = []

    def __call__(self, args, *a, **kw):
        argv = [str(x) for x in args]
        if os.path.basename(argv[0]) == "busctl":
            self.asked.append((argv, kw.get("env")))
            reply = json.dumps({"type": "as", "data": self.directories})
            return subprocess.CompletedProcess(argv, 0, reply + "\n", "")
        self.launched.append(argv)
        if self.start:
            return _REAL_RUN(args, *a, **kw)
        return subprocess.CompletedProcess(argv, 0, "ok\n", "")


@contextlib.contextmanager
def _fake_credentials():
    """Two fake credential files for the launcher's mask, in a throwaway directory."""
    with tempfile.TemporaryDirectory() as home:
        paths = tuple(Path(home, name) for name in ("dispatch-key", "web-token"))
        for path in paths:
            path.write_text("fake\n", encoding="utf-8")
        with mock.patch.object(tool_process, "credential_paths", return_value=paths):
            yield


def _interpreter_of(program: str) -> str:
    """The dynamic loader an ELF program names (its PT_INTERP segment)."""
    data = Path(program).read_bytes()
    phoff = int.from_bytes(data[0x20:0x28], "little")
    phentsize = int.from_bytes(data[0x36:0x38], "little")
    for index in range(int.from_bytes(data[0x38:0x3a], "little")):
        header = data[phoff + index * phentsize:phoff + (index + 1) * phentsize]
        if int.from_bytes(header[0:4], "little") == 3:  # PT_INTERP
            offset = int.from_bytes(header[8:16], "little")
            size = int.from_bytes(header[32:40], "little")
            return data[offset:offset + size].rstrip(b"\0").decode()
    raise AssertionError(f"{program} names no loader")


class TheEnvironmentIsWrittenNotInherited(unittest.TestCase):
    """tool_environment: what every command starts with."""

    def test_the_allowlist_is_the_whole_environment(self) -> None:
        with mock.patch.dict(os.environ, _PLANTED, clear=True):
            environment = tool_process.tool_environment()
        self.assertEqual(set(environment), _ALLOWLIST)
        entry = pwd.getpwuid(os.getuid())
        uid = os.getuid()
        self.assertEqual(environment["PATH"], tool_process.TOOL_PATH)
        self.assertEqual(environment["HOME"], entry.pw_dir)
        self.assertEqual(environment["USER"], entry.pw_name)
        self.assertEqual(environment["LOGNAME"], entry.pw_name)
        self.assertEqual(environment["XDG_RUNTIME_DIR"], f"/run/user/{uid}")
        self.assertEqual(environment["DBUS_SESSION_BUS_ADDRESS"],
                         f"unix:path=/run/user/{uid}/bus")
        self.assertEqual(environment["TERM"], "dumb")

    def test_the_pins_close_the_measured_reads(self) -> None:
        with mock.patch.dict(os.environ, _PLANTED, clear=True):
            environment = tool_process.tool_environment()
        self.assertEqual(environment["npm_config_update_notifier"], "false")
        self.assertEqual(environment["CUPS_SERVER"], "/run/cups/cups.sock")
        self.assertEqual(environment["PYTHONNOUSERSITE"], "1")
        # Measured not to stop npm's update check, so it is not a pin.
        self.assertNotIn("npm_config_offline", environment)

    def test_the_account_is_read_from_the_password_database(self) -> None:
        fake = mock.MagicMock()
        fake.getpwuid.return_value.pw_dir = "/home/the-account"
        fake.getpwuid.return_value.pw_name = "the-account"
        with mock.patch.dict(os.environ, _PLANTED, clear=True), \
                mock.patch.object(tool_process, "pwd", fake):
            environment = tool_process.tool_environment()
        fake.getpwuid.assert_called_once_with(os.getuid())
        self.assertEqual((environment["HOME"], environment["USER"], environment["LOGNAME"]),
                         ("/home/the-account", "the-account", "the-account"))

    def test_an_account_with_no_entry_starts_nothing(self) -> None:
        fake = mock.MagicMock()
        fake.getpwuid.side_effect = KeyError(os.getuid())
        with mock.patch.object(tool_process, "pwd", fake):
            with self.assertRaises(tool_process.ToolProcessNotStarted) as caught:
                tool_process.tool_environment()
        self.assertIn("password database", str(caught.exception))

    def test_a_locale_is_carried_only_as_a_name(self) -> None:
        planted = {"LANG": "C.UTF-8", "LC_TIME": "en_GB.UTF-8",
                   "LC_ALL": "/nonexistent/locale", "LC_MESSAGES": "../../x",
                   "LANGUAGE": "en", "LC_CTYPE": "C.UTF-8 extra"}
        with mock.patch.dict(os.environ, planted, clear=True):
            environment = tool_process.tool_environment()
        self.assertEqual({k: v for k, v in environment.items() if k not in _ALLOWLIST},
                         {"LANG": "C.UTF-8", "LC_TIME": "en_GB.UTF-8"})

    def test_every_directory_of_the_fixed_path_is_roots(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("run as root, every directory is writable by the account")
        for directory in tool_process.TOOL_PATH.split(":"):
            with self.subTest(directory=directory):
                if not os.path.isdir(directory):
                    continue
                st = os.stat(directory)
                self.assertEqual(st.st_uid, 0, f"{directory} is not root's")
                self.assertFalse(st.st_mode & (stat.S_IWGRP | stat.S_IWOTH),
                                 f"{directory} is writable by others")
                self.assertFalse(os.access(directory, os.W_OK))

    def test_the_loader_list_is_the_one_this_machines_loader_carries(self) -> None:
        loader = _interpreter_of(tool_process.ENV)
        data = Path(loader).read_bytes()
        runs = [m.group(0).rstrip(b"\0").split(b"\0")
                for m in re.finditer(rb"(?:[A-Z][A-Z0-9_]*\0){20,}", data)]
        wanted = [name.encode() for name in tool_process.LOADER_UNSAFE_VARIABLES]
        self.assertIn(wanted, runs,
                      f"{loader} carries no list equal to LOADER_UNSAFE_VARIABLES; "
                      f"the lists it carries: {[b' '.join(r).decode() for r in runs]}")


class TheLaunchCarriesOnlyTheWrittenEnvironment(unittest.TestCase):
    """launch_argv with write_environment: the line systemd-run is given."""

    def _launch(self, argv, **kw):
        with tempfile.TemporaryDirectory() as empty, _fake_credentials(), \
                mock.patch("subprocess.run", _Runs([empty])), \
                mock.patch.dict(os.environ, _PLANTED, clear=True):
            return (tool_process.launch_argv(argv, unit=_UNIT, write_environment=True, **kw),
                    tool_process.tool_environment())

    def test_the_service_unsets_the_loader_list_before_env_starts(self) -> None:
        launch, _ = self._launch(["/usr/bin/true"])
        before = launch[:launch.index("--")]
        self.assertIn("--property=UnsetEnvironment="
                      + " ".join(tool_process.LOADER_UNSAFE_VARIABLES), before)
        self.assertFalse([a for a in before if a.startswith("--setenv")], before)

    def test_env_starts_the_command_with_the_allowlist_alone(self) -> None:
        launch, expected = self._launch(["/usr/bin/true", "--version"])
        environment, own = written_environment_and_program(launch)
        self.assertEqual(environment, expected)
        self.assertEqual(own, ["/usr/bin/true", "--version"])
        for name, value in _PLANTED.items():
            with self.subTest(name=name):
                self.assertNotIn(value, environment.values())

    def test_the_accounts_own_manager_sockets_are_masked_whatever_the_environment_names(
            self) -> None:
        # The written environment points a command at the account's own runtime
        # directory, so its sockets are masked for every launch, MCP's included.
        with tempfile.TemporaryDirectory() as empty, _fake_credentials(), \
                mock.patch("subprocess.run", _Runs([empty])), \
                mock.patch.dict(os.environ, _PLANTED, clear=True):
            launch = tool_process.launch_argv(["/usr/bin/true"], unit=_UNIT)
        runtime = f"/run/user/{os.getuid()}"
        for socket in (f"{runtime}/systemd/private", f"{runtime}/bus"):
            with self.subTest(socket=socket):
                self.assertIn(f'--property=InaccessiblePaths="-{socket}"', launch)

    def test_a_program_env_would_not_start_is_refused(self) -> None:
        for program in ("/nonexistent/a=b", "true", "./true"):
            with self.subTest(program=program):
                with self.assertRaises(tool_process.ToolProcessNotStarted) as caught:
                    self._launch([program])
                self.assertIn(repr(program), str(caught.exception))

    def test_the_search_path_is_asked_of_the_manager_at_the_accounts_own_bus(self) -> None:
        runs = _Runs(["/nonexistent/unit-path"])
        with _fake_credentials(), mock.patch("subprocess.run", runs), \
                mock.patch.dict(os.environ, _PLANTED, clear=True):
            tool_process.launch_argv(["/usr/bin/true"], unit=_UNIT, write_environment=True)
        self.assertEqual(len(runs.asked), 1)
        argv, env = runs.asked[0]
        self.assertEqual(argv[:2], [tool_process.BUSCTL,
                                    f"--address=unix:path=/run/user/{os.getuid()}/bus"])
        self.assertEqual(argv[-4:], ["org.freedesktop.systemd1", "/org/freedesktop/systemd1",
                                     "org.freedesktop.systemd1.Manager", "UnitPath"])
        self.assertEqual(env, {"PATH": tool_process.TOOL_PATH, "LANG": "C"})


class TheProgramIsResolvedOnTheFixedPath(unittest.TestCase):
    """run_command: a name is looked up where the command will run, never on the daemon's PATH."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = scratch.name
        for name in ("df", "intergen-planted-tool"):
            planted = Path(self.scratch, name)
            planted.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            planted.chmod(0o755)

    def _execute(self, command: str) -> tuple[object, _Runs]:
        runs = _Runs([self.scratch])
        inherited = self.scratch + os.pathsep + os.environ.get("PATH", "/usr/bin")
        with _fake_credentials(), mock.patch("subprocess.run", runs), \
                mock.patch.dict(os.environ, {"PATH": inherited}):
            return RunCommandTool().execute({"command": command}), runs

    def test_a_directory_first_in_the_inherited_path_does_not_choose_the_program(self) -> None:
        result, runs = self._execute("df -h")
        self.assertEqual(len(runs.launched), 1, runs.launched)
        program = runs.launched[0][-2]
        self.assertFalse(program.startswith(self.scratch + os.sep),
                         f"the inherited PATH chose {program}")
        self.assertEqual(program, shutil.which("df", path=tool_process.TOOL_PATH))
        self.assertTrue(result.success)

    def test_a_name_only_the_inherited_path_holds_is_not_found(self) -> None:
        result, runs = self._execute("intergen-planted-tool --list")
        self.assertEqual(runs.launched, [], "a program off the fixed PATH was started")
        self.assertFalse(result.success)
        self.assertIn("No such file or directory", result.content)


class _ADropInGate(unittest.TestCase):
    """A scratch unit directory, and the fixture install a start needs."""

    def setUp(self) -> None:
        self._dir = visible_directory("intergen-dropin-case-")
        self.addCleanup(self._dir.cleanup)
        self.home = Path(self._dir.name)
        write_fake_credentials(self.home)
        self.units = self.home / "config" / "systemd" / "user"
        self.units.mkdir(parents=True)
        patcher = install_lives_in(self.home)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(tool_process, "new_unit_name", return_value=_UNIT)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _execute(self, command: str, *directories, start: bool = False):
        runs = _Runs(directories or (self.units,), start=start)
        with mock.patch("subprocess.run", runs):
            return RunCommandTool().execute({"command": command}), runs

    def _assert_refused_by_name(self, result, runs, path: Path) -> None:
        self.assertEqual(runs.launched, [], f"the tool's service started with {path} present")
        self.assertFalse(result.success)
        self.assertTrue(result.blocked)
        self.assertTrue(result.content.startswith("This command was not run:"), result.content)
        self.assertIn(str(path), result.content)


class ADropInTheAccountCanWriteRefusesTheStart(_ADropInGate):
    """X2: the gate, with the launch recorded rather than started."""

    def test_each_drop_in_that_would_apply_is_refused_by_name(self) -> None:
        for name in ("service.d", "intergen-.service.d", "intergen-tool-.service.d",
                     _UNIT + ".d"):
            with self.subTest(name=name):
                path = self.units / name
                path.mkdir()
                try:
                    result, runs = self._execute("df -h")
                finally:
                    path.rmdir()
                self._assert_refused_by_name(result, runs, path)

    def test_a_link_in_the_drop_ins_place_is_refused(self) -> None:
        path = self.units / "service.d"
        path.symlink_to(self.home / "elsewhere")  # dangling: lexists, not exists
        result, runs = self._execute("df -h")
        self._assert_refused_by_name(result, runs, path)

    def test_every_directory_of_the_search_path_is_read(self) -> None:
        later = self.home / "data" / "systemd" / "user"
        (later / "intergen-.service.d").mkdir(parents=True)
        result, runs = self._execute("df -h", self.units, later)
        self._assert_refused_by_name(result, runs, later / "intergen-.service.d")

    def test_a_drop_in_for_another_unit_starts_the_command(self) -> None:
        (self.units / "ferry-wake@x.service.d").mkdir()
        (self.units / "intergen.service.d").mkdir()
        result, runs = self._execute("df -h")
        self.assertEqual(len(runs.launched), 1, runs.launched)
        self.assertTrue(result.success)

    def test_a_drop_in_the_account_owns_is_refused_with_its_write_bits_cleared(self) -> None:
        # Ownership decides, never the mode: the owner can give the write bits
        # back. A drop-in only root has is the control in
        # test_a_unit_directory_the_account_can_change_refuses_a_tool_start.
        if os.geteuid() == 0:
            self.skipTest("run as root, every directory is root's")
        path = self.units / "service.d"
        path.mkdir()
        for each in (path, self.units):
            each.chmod(0o555)
            self.addCleanup(each.chmod, 0o755)
        result, runs = self._execute("df -h")
        self._assert_refused_by_name(result, runs, path)

    def test_a_search_path_the_manager_does_not_give_starts_nothing(self) -> None:
        answers = {
            "exit 1": subprocess.CompletedProcess([], 1, "", "Failed to connect to bus"),
            "not JSON": subprocess.CompletedProcess([], 0, "UnitPath\n", ""),
            "no list": subprocess.CompletedProcess([], 0, '{"type":"as","data":[]}', ""),
            "a relative path": subprocess.CompletedProcess(
                [], 0, '{"type":"as","data":["systemd/user"]}', ""),
            "busctl absent": FileNotFoundError(2, "No such file or directory", "busctl"),
            "no answer in time": subprocess.TimeoutExpired("busctl", 10),
        }
        for why, answer in answers.items():
            with self.subTest(why=why):
                launched = []

                def run(args, *a, **kw):
                    if os.path.basename(str(args[0])) == "busctl":
                        if isinstance(answer, BaseException):
                            raise answer
                        return answer
                    launched.append(args)
                    return subprocess.CompletedProcess(args, 0, "ok\n", "")

                with mock.patch("subprocess.run", run):
                    result = RunCommandTool().execute({"command": "df -h"})
                self.assertEqual(launched, [])
                self.assertFalse(result.success)
                self.assertIn("unit search path could not be read", result.content)


class TheGateAtARealStart(_ADropInGate):
    """X2 with the start made: refused by name with the drop-in, started without it."""

    @classmethod
    def setUpClass(cls) -> None:
        if not user_manager_answers():
            raise unittest.SkipTest(NO_USER_MANAGER)

    def test_service_d_refuses_the_start_and_its_absence_starts_the_command(self) -> None:
        path = self.units / "service.d"
        path.mkdir()
        result, runs = self._execute("printf hello", start=True)
        self._assert_refused_by_name(result, runs, path)
        path.rmdir()
        result, runs = self._execute("printf hello", start=True)
        self.assertEqual(len(runs.launched), 1, runs.launched)
        self.assertTrue(result.success, result.content)
        self.assertEqual(result.content, "hello")

    def test_the_command_sees_the_written_environment_and_nothing_else(self) -> None:
        result, runs = self._execute("env", start=True)
        self.assertTrue(result.success, result.content)
        seen = dict(line.split("=", 1) for line in result.content.splitlines() if "=" in line)
        locale = {k for k, v in os.environ.items()
                  if re.fullmatch(r"LANG|LC_[A-Z]+", k) and re.fullmatch(r"[A-Za-z0-9_.@-]+", v)}
        self.assertEqual(set(seen), _ALLOWLIST | locale,
                         "the user manager's block, or the service's own variables, "
                         "reached the command")
        self.assertEqual(seen["HOME"], pwd.getpwuid(os.getuid()).pw_dir)
        self.assertEqual(seen["PATH"], tool_process.TOOL_PATH)


if __name__ == "__main__":
    unittest.main()
