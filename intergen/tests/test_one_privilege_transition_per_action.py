# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Exactly one brokered privilege transition per approved action.

Background. A privileged tool call crosses the privilege boundary once: the
assistant asks its user manager to start a transient unit, the unit runs
`pkexec /usr/bin/intergen-privileged-runner <request>`, the user authenticates
to PolicyKit against the narrow action bound to that runner path, and the
root-side dispatcher verifies the human-approval token before anything runs.
One approval, one crossing, one gate.

The package-management path built a SECOND one. `manage_packages` constructed
`["pkexec", "pkm", <action>, <package>]` for every state-changing action — and
by the time that command builder runs, the tool is ALREADY executing as root
inside the privileged dispatcher. So the second crossing buys nothing and costs
a privilege construction that:

  * has no PolicyKit action of its own — it would fall to a general one,
    not the purpose-built narrow one the first crossing used;
  * carries no approval token, so nothing binds it to what a person approved;
  * runs from an environment the runner deliberately scrubbed, with no session
    variables, so it could not raise a prompt a person could answer even if it
    tried.

These tests pin the invariant across all four privileged tools rather than only
the one that broke it, because the next tool to grow a command builder should
meet the rule rather than repeat the mistake. Two complementary tables:

  1. BEHAVIOURAL, over manage_packages' real command builder: every action it
     accepts, read-only and state-changing alike, and none of them may produce
     an escalating command.
  2. STRUCTURAL, over all four privileged tools' source: no module may build a
     command list whose first element is an escalation binary. This is read
     from the abstract syntax tree, so nothing is executed and a string that
     merely MENTIONS one of those names — write_file's /etc/sudoers denylist
     entry, for instance — is correctly not a hit.

Nothing here executes a command, a tool, pkexec, or systemd-run.
"""

from __future__ import annotations

import ast
import inspect
import unittest

from intergen.tool_registry import _PRIVILEGED_TOOLS
from intergen.tools.manage_packages import (
    AUTO_SUBCOMMANDS,
    CONFIRM_SUBCOMMANDS,
    ManagePackagesTool,
)

#: Programs whose whole purpose is to cross a privilege boundary. A tool that
#: is already running as root has no business invoking any of them.
ESCALATION_BINARIES = frozenset({
    "pkexec", "sudo", "run0", "doas", "su", "pkttyagent",
})


class ManagePackagesBuildsNoTransitionTests(unittest.TestCase):
    """The behavioural table: every action the tool accepts."""

    def setUp(self):
        self.tool = ManagePackagesTool()

    def _command(self, action, package="cowsay", query="cowsay"):
        return self.tool._build_command(action, package, query)

    def test_no_action_produces_an_escalating_command(self):
        for action in sorted(AUTO_SUBCOMMANDS | CONFIRM_SUBCOMMANDS):
            with self.subTest(action=action):
                command = self._command(action)
                if command is None:
                    continue
                self.assertNotIn(
                    command[0], ESCALATION_BINARIES,
                    f"the {action!r} action constructs a second privilege "
                    f"transition: {command}",
                )

    def test_no_action_mentions_an_escalating_binary_anywhere_in_the_command(self):
        for action in sorted(AUTO_SUBCOMMANDS | CONFIRM_SUBCOMMANDS):
            with self.subTest(action=action):
                command = self._command(action)
                if command is None:
                    continue
                for word in command:
                    self.assertNotIn(word, ESCALATION_BINARIES, command)

    def test_state_changing_actions_still_invoke_the_package_manager(self):
        """Removing the escalation must not remove the command.

        A tool that builds nothing is as broken as one that builds two
        transitions, so the positive half is pinned in the same table.
        """
        for action in sorted(CONFIRM_SUBCOMMANDS):
            with self.subTest(action=action):
                command = self._command(action)
                self.assertIsNotNone(
                    command, f"{action!r} builds no command at all",
                )
                self.assertEqual(
                    command[0], "pkm",
                    f"{action!r} no longer invokes the package manager: "
                    f"{command}",
                )

    def test_read_only_actions_are_unchanged(self):
        self.assertEqual(self._command("list"), ["pkm", "list"])
        self.assertEqual(self._command("search"), ["pkm", "search", "cowsay"])
        self.assertEqual(self._command("info"), ["pkm", "info", "cowsay"])
        self.assertEqual(self._command("verify"), ["pkm", "verify", "cowsay"])

    def test_update_without_a_package_still_updates_everything(self):
        self.assertEqual(
            self.tool._build_command("update", "", ""), ["pkm", "update"],
        )

    def test_an_unknown_action_still_builds_nothing(self):
        self.assertIsNone(self._command("not-a-real-action"))


class WhyTheSecondTransitionIsUnnecessaryTests(unittest.TestCase):
    """The invariant that makes removing it correct rather than merely tidy.

    Dropping the escalation would be a defect if these actions could ever run
    unprivileged. They cannot: manage_packages is on the privileged allowlist,
    and every state-changing action classifies into the tier that routes
    through the runner. If either of those ever stops being true, the removal
    stops being safe — so both are asserted here, next to the removal.
    """

    def test_manage_packages_is_on_the_privileged_allowlist(self):
        self.assertIn("manage_packages", _PRIVILEGED_TOOLS)

    def test_every_state_changing_action_classifies_as_confirm(self):
        from intergen.interfaces.types import SafetyTier
        tool = ManagePackagesTool()
        for action in sorted(CONFIRM_SUBCOMMANDS):
            with self.subTest(action=action):
                self.assertEqual(
                    tool.classify_safety({"action": action, "package": "x"}),
                    SafetyTier.CONFIRM,
                    f"{action!r} no longer routes through the privileged "
                    f"dispatcher, so it would run unprivileged with the "
                    f"escalation removed",
                )

    def test_read_only_actions_stay_unprivileged(self):
        from intergen.interfaces.types import SafetyTier
        tool = ManagePackagesTool()
        for action in sorted(AUTO_SUBCOMMANDS):
            with self.subTest(action=action):
                self.assertEqual(
                    tool.classify_safety({"action": action}), SafetyTier.AUTO,
                )


class NoPrivilegedToolConstructsATransitionTests(unittest.TestCase):
    """The structural table, over all four privileged tools.

    Read from the syntax tree rather than by executing anything or by grepping
    for a substring. A module that merely NAMES one of these binaries in a
    denylist string is not building a command with it, and this check knows the
    difference.
    """

    @staticmethod
    def _tool_module(tool_name):
        return __import__(
            f"intergen.tools.{tool_name}", fromlist=["_"],
        )

    def _escalating_command_lists(self, module):
        """Return every list/tuple literal in the module whose first element is
        an escalation binary."""
        source = inspect.getsource(module)
        tree = ast.parse(source)
        hits = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.List, ast.Tuple)):
                continue
            if not node.elts:
                continue
            first = node.elts[0]
            if isinstance(first, ast.Constant) and first.value in ESCALATION_BINARIES:
                hits.append((first.value, getattr(node, "lineno", "?")))
        return hits

    def test_the_allowlist_is_the_four_tools_this_table_covers(self):
        """If a fifth privileged tool appears, this table must grow with it —
        a rule that silently stops covering the population is not a rule."""
        self.assertEqual(
            _PRIVILEGED_TOOLS,
            frozenset({"manage_services", "manage_packages", "run_command",
                       "write_file"}),
            "the privileged-tool allowlist changed; extend this table to cover "
            "the new member before relying on it",
        )

    def test_no_privileged_tool_builds_an_escalating_command(self):
        for tool_name in sorted(_PRIVILEGED_TOOLS):
            with self.subTest(tool=tool_name):
                module = self._tool_module(tool_name)
                hits = self._escalating_command_lists(module)
                self.assertEqual(
                    hits, [],
                    f"intergen/tools/{tool_name}.py builds a privilege "
                    f"transition at {hits}; by the time a privileged tool runs "
                    f"it is already root, so a second crossing is "
                    f"unauthenticated, untokened, and gated by nothing",
                )

    def test_the_structural_check_can_actually_see_a_transition(self):
        """A positive control. An instrument never shown to detect the thing it
        looks for cannot certify its absence."""
        planted = ast.parse('cmd = ["pkexec", "pkm", "install", "x"]\n')
        hits = []
        for node in ast.walk(planted):
            if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
                first = node.elts[0]
                if isinstance(first, ast.Constant) and first.value in ESCALATION_BINARIES:
                    hits.append(first.value)
        self.assertEqual(
            hits, ["pkexec"],
            "the structural check cannot see a planted transition, so its "
            "clean result over the real modules proves nothing",
        )

    def test_a_mentioned_binary_is_not_a_hit(self):
        """The negative control on the same instrument: naming a path that
        contains one of these words is not building a command with it."""
        mentioned = ast.parse('DENY = ["/etc/sudoers", "/boot/vmlinuz"]\n')
        hits = []
        for node in ast.walk(mentioned):
            if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
                first = node.elts[0]
                if isinstance(first, ast.Constant) and first.value in ESCALATION_BINARIES:
                    hits.append(first.value)
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
