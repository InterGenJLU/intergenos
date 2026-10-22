# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A unit directory the account can change never applies to a tool's service.

Beside a unit's file the user manager reads a drop-in directory (.d), whose
files change the unit, and three dependency directories (.wants, .requires,
.upholds), whose entries are units it starts with the unit, each under every
name systemd applies to the unit: its own, each dash-ended prefix of it, and
its type alone. These cells hold the gate's answer: the names are derived by
systemd's own rule, and a directory present under any of them refuses the
start, named with the reason, unless it, every directory above it and
everything within it are root's alone, decided by ownership and never by a
mode the owner can change.

No cell writes into the user manager's own search path: the entries are made
in scratch directories, and the launcher's read of the search path is answered
with those directories.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from intergen import tool_process
from intergen.interfaces.mcp import MCPServerConfig, MCPTrustTier
from intergen.mcp_client import _MCPServerConnection
from intergen.tests.tool_process_fixture import (
    install_lives_in, unit_search_path_is, visible_directory, write_fake_credentials,
)
from intergen.tools.run_command import RunCommandTool

_UNIT = "intergen-tool-0123456789abcdef.service"
_NAMES = (_UNIT, "intergen-tool-.service", "intergen-.service", "service")
_SUFFIXES = (".wants", ".requires", ".upholds", ".d")
_WAYS = ("a directory", "a directory, it and its unit directory at 0555",
         "a link to a directory elsewhere", "a link, its unit directory at 0555")
_AS_ROOT = "run as root, every entry is root's"


class TheNamesAreSystemds(unittest.TestCase):
    """unit_directory_names follows src/shared/dropin.c at systemd 259.1."""

    def test_a_tool_service_takes_its_name_its_two_prefixes_and_its_type(self) -> None:
        self.assertEqual(tool_process.unit_directory_names(_UNIT), list(_NAMES))

    def test_the_suffixes_are_the_four_the_manager_reads(self) -> None:
        self.assertEqual(tool_process.UNIT_DIRECTORY_SUFFIXES, _SUFFIXES)

    def test_the_rule_case_by_case(self) -> None:
        # Each list read off unit_file_find_dirs (143-238) and
        # unit_file_find_dropin_paths (240-295): the name, an instance's
        # template, the name built from the dash-ended prefix (a trailing dash
        # chopped once, none taken at a leading dash), each searched the same
        # way, and the type alone last.
        cases = {
            "a-b-c.service": ["a-b-c.service", "a-b-.service", "a-.service", "service"],
            "foo-.service": ["foo-.service", "service"],
            "-foo.service": ["-foo.service", "service"],
            "foo--bar.service": ["foo--bar.service", "foo--.service", "foo-.service", "service"],
            "getty@tty1.service": ["getty@tty1.service", "getty@.service", "service"],
            "a-b@x-y.service": ["a-b@x-y.service", "a-b@.service", "a-.service",
                                "a-@x-y.service", "a-@.service", "service"],
            "foo@.service": ["foo@.service", "service"],
            "app-gnome-x.scope": ["app-gnome-x.scope", "app-gnome-.scope", "app-.scope", "scope"],
        }
        for unit, names in cases.items():
            with self.subTest(unit=unit):
                self.assertEqual(tool_process.unit_directory_names(unit), names)


@unittest.skipIf(os.geteuid() == 0, _AS_ROOT)
class AnEntryTheAccountMakesRefusesTheStart(unittest.TestCase):
    """Every name x every suffix x two unit directories x four ways: refused by name."""

    def test_every_form_every_way_in_either_unit_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="intergen-unit-dirs-") as root:
            made = 0
            for where in ("config", "data"):
                for name in _NAMES:
                    for suffix in _SUFFIXES:
                        for way in _WAYS:
                            made += 1
                            case = Path(root, f"c{made:03d}")
                            units = case / where / "systemd" / "user"
                            units.mkdir(parents=True)
                            (case / "elsewhere").mkdir()
                            entry = units / (name + suffix)
                            if way.startswith("a link"):
                                entry.symlink_to(case / "elsewhere")
                            else:
                                entry.mkdir()
                            if "0555" in way:
                                if not entry.is_symlink():
                                    entry.chmod(0o555)
                                units.chmod(0o555)
                            try:
                                with self.subTest(entry=f"{where}/{entry.name}", way=way), \
                                        unit_search_path_is("/nonexistent/first", units):
                                    with self.assertRaises(tool_process.ToolProcessNotStarted) as caught:
                                        tool_process.check_unit_dropins(_UNIT)
                                    self.assertIn(f"{entry} is ", str(caught.exception))
                                    self.assertIn(f"{entry} is owned by this account", str(caught.exception))
                            finally:
                                units.chmod(0o755)
                                if not entry.is_symlink():
                                    entry.chmod(0o755)
            self.assertEqual(made, 128)

    def test_a_directory_for_another_unit_is_not_read(self) -> None:
        with tempfile.TemporaryDirectory(prefix="intergen-unit-dirs-") as root:
            units = Path(root, "systemd", "user")
            for name in ("intergen.service.d", "intergen.service.wants", "ferry-wake@x.service.d",
                         "intergen-toolbox.service.d", "intergen-tool.service.wants"):
                (units / name).mkdir(parents=True)
            with unit_search_path_is(units):
                tool_process.check_unit_dropins(_UNIT)


@unittest.skipIf(os.geteuid() == 0, _AS_ROOT)
class TheRefusalSaysWhy(unittest.TestCase):
    """The words name the entry, what it is, why the account can change it, and the remedy."""

    def test_a_drop_in_and_a_dependency_directory_in_words(self) -> None:
        with tempfile.TemporaryDirectory(prefix="intergen-unit-dirs-") as root:
            units = Path(root, "systemd", "user")
            for suffix, kind in ((".d", "a drop-in directory, whose files change the service"),
                                 (".upholds", "a dependency directory (.upholds), whose entries "
                                              "the manager starts with the service")):
                entry = units / ("intergen-.service" + suffix)
                entry.mkdir(parents=True)
                with self.subTest(suffix=suffix), unit_search_path_is(units), \
                        self.assertRaises(tool_process.ToolProcessNotStarted) as caught:
                    tool_process.check_unit_dropins(_UNIT)
                entry.rmdir()
                self.assertEqual(
                    str(caught.exception),
                    f"{entry} is {kind}, in the user manager's unit search path and applied "
                    f"to the tool's service; {entry} is owned by this account (uid "
                    f"{os.getuid()}), not root, so this account can change what it holds, and "
                    f"the tool's process was not started. Remove it if you did not create it.")


class ARootOwnedDropInIsTheAdministrators(unittest.TestCase):
    """The control: a drop-in directory the system ships, root's along its whole path."""

    @staticmethod
    def _shipped() -> tuple[str, str, str] | None:
        for directory in ("/etc/systemd/user", "/usr/lib/systemd/user"):
            try:
                names = sorted(os.listdir(directory))
            except OSError:
                continue
            for name in names:
                stem, _, kind = name[:-2].rpartition(".")
                path = os.path.join(directory, name)
                if (name.endswith(".d") and stem and kind in tool_process._UNIT_TYPES
                        and not os.path.islink(path) and os.path.isdir(path)
                        and os.lstat(path).st_uid == 0 and os.lstat(directory).st_uid == 0):
                    return directory, name[:-2], path
        return None

    def test_it_is_logged_and_the_start_goes_ahead(self) -> None:
        found = self._shipped()
        if found is None:
            self.skipTest("this system ships no root-owned drop-in directory in "
                          "/etc/systemd/user or /usr/lib/systemd/user")
        directory, unit, path = found
        with unit_search_path_is(directory), \
                self.assertLogs("intergen.tool_process", level="WARNING") as logged:
            tool_process.check_unit_dropins(unit)
        self.assertTrue([line for line in logged.output
                         if path in line and "as the administrator's" in line], logged.output)


@unittest.skipIf(os.geteuid() == 0, _AS_ROOT)
class EveryLaunchMeetsTheGate(unittest.TestCase):
    """run_command's command and an MCP server both start through launch_argv's gate."""

    def setUp(self) -> None:
        directory = visible_directory("intergen-unit-dirs-case-")
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)
        write_fake_credentials(self.home)
        for patcher in (install_lives_in(self.home),
                        mock.patch.object(tool_process, "new_unit_name", return_value=_UNIT)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.units = self.home / "config" / "systemd" / "user"
        self.entry = self.units / "intergen-tool-.service.wants"
        self.entry.mkdir(parents=True)

    def test_a_command_is_refused_by_name(self) -> None:
        launched = []

        def run(args, *a, **kw):
            launched.append([str(x) for x in args])
            return subprocess.CompletedProcess(args, 0, "ok\n", "")

        with unit_search_path_is(self.units), mock.patch("subprocess.run", run):
            result = RunCommandTool().execute({"command": "df -h"})
        self.assertEqual(launched, [], "the tool's service was started")
        self.assertFalse(result.success)
        self.assertIn(str(self.entry), result.content)

    def test_an_mcp_server_is_refused_by_name(self) -> None:
        config = MCPServerConfig(name="case", command="/usr/bin/python3", args=["server.py"],
                                 env={}, trust_tier=MCPTrustTier.UNTRUSTED)
        with unit_search_path_is(self.units), contextlib.ExitStack() as stack:
            popen = stack.enter_context(mock.patch.object(subprocess, "Popen"))
            with self.assertRaises(tool_process.ToolProcessNotStarted) as caught:
                _MCPServerConnection("case", config).connect()
        popen.assert_not_called()
        self.assertIn(str(self.entry), str(caught.exception))


@unittest.skipIf(os.geteuid() == 0, _AS_ROOT)
class ThePrivilegedDispatchMeetsTheGate(unittest.TestCase):
    """The privileged dispatch's own unit, intergen-privileged-<hex>.service, meets the gate before its systemd-run.

    A directory the account can change under any of its names could replace the
    command the person's authentication approves. Every process is stubbed: no
    unit is asked for and nothing reaches PolicyKit.
    """

    def _dispatch(self, *entries: str):
        from intergen import privileged_request, tool_registry
        from intergen.interfaces.types import Provenance, ToolCall
        from intergen.tests.escalation_posture import escalation_allowed_for_a_stubbed_dispatch

        with tempfile.TemporaryDirectory(prefix="intergen-unit-dirs-priv-") as runtime:
            units = Path(runtime, "systemd", "user")
            units.mkdir(parents=True)
            for name in entries:
                (units / name).mkdir()
            run = mock.Mock(return_value=subprocess.CompletedProcess([], 0, "done\n", ""))
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": runtime}), \
                    mock.patch.object(tool_registry.subprocess, "run", run), \
                    unit_search_path_is(units), escalation_allowed_for_a_stubbed_dispatch():
                result = tool_registry.ToolRegistry._dispatch_via_pkexec(
                    ToolCall(name="manage_packages", arguments={"action": "upgrade"},
                             call_id="unit-directory-case",
                             source_of_request=Provenance.USER_DIRECT),
                    "manage_packages", {"action": "upgrade"}, "token-placeholder")
                requests = Path(privileged_request.request_dir())
                left = sorted(p.name for p in requests.iterdir()) if requests.is_dir() else []
            started = [c.args[0] for c in run.call_args_list
                       if c.args and os.path.basename(str(c.args[0][0])) == "systemd-run"]
            return result, started, units, left

    def test_each_kind_of_name_refuses_the_dispatch_before_systemd_run(self) -> None:
        for name in ("intergen-privileged-.service.d", "intergen-.service.wants", "service.d"):
            with self.subTest(name=name):
                result, started, units, left = self._dispatch(name)
                self.assertEqual(started, [], "systemd-run was asked to start the dispatch")
                self.assertFalse(result.success)
                self.assertFalse(result.executed)
                self.assertIn(f"{units / name} is ", result.content)
                self.assertIn("was not dispatched, and no password was asked for", result.content)
                self.assertEqual(left, [], "the request holding the approval was left on disk")

    def test_without_one_the_dispatch_is_started(self) -> None:
        result, started, _, _ = self._dispatch()
        self.assertEqual(len(started), 1, started)
        self.assertIn("--unit=intergen-privileged-", " ".join(started[0]))
        self.assertTrue(result.success, result.content)


if __name__ == "__main__":
    unittest.main()
