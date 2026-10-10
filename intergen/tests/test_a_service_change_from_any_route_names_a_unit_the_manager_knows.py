# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A service change from any route names a unit the service manager knows.

THE DEFECT, read on the tree in intergen/tools/manage_services.py. The router
proposes a service change from a keyword match only when the service manager
knows the unit, but the model's own tool turn reaches the tool directly, and the
tool's schema requires only the action. Measured after a keyword decline, the
model's turn asked for "restart" with no service at all. The tool checked
nothing about the unit before the dispatcher gate, so such a call reached the
review card: the person was asked to approve a restart of nothing named, or of
a unit this machine does not have, and approving it ran nothing they asked for
(the tool's own refusal of an empty name came only after the approval).

THE RULE these cells hold the tool to: a state-changing action that acts on a
unit (start, stop, restart, reload, enable, disable, mask, unmask) is refused
in words before the dispatcher gate when it names no unit, names a unit the
service manager does not know as spelled, or the manager cannot be read. The
question goes to the manager the call acts on: the person's own with
``user_mode``, the system's otherwise. Every route dispatches through the tool
registry, so the rule holds for the keyword, semantic and model routes alike.

THE CONTROLS beside the cells are green at both ends: a unit the manager knows
still reaches the review card, under its alias too ("ssh" is the unit sshd); a
status question is not asked about and never reaches the card; and
daemon-reload, which names no unit, still reaches the card.

Each case dispatches through the tool registry with a review callback that
records the card and refuses it. This machine's service manager is never asked:
for the length of a case `subprocess.run` is a manager of the case's own, which
answers `systemctl show` and `systemctl list-unit-files` for the system and the
person's own scope from fixed sets of unit files, records every question and
refuses any other command. The privileged dispatcher is stood in and the
dispatch record's writer is recorded, so nothing is run and nothing on this
machine is written.
"""
from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from intergen.interfaces.provenance import Provenance
from intergen.interfaces.types import ToolCall, ToolResult
from intergen.tool_registry import ToolRegistry

SYSTEMCTL = "/usr/bin/systemctl"
SYSTEM_UNITS = ("NetworkManager.service", "sshd.service")
USER_UNITS = ("pipewire.service",)
# The state-changing actions that act on a unit.
UNIT_CHANGING = ("start", "stop", "restart", "reload", "enable", "disable",
                 "mask", "unmask")


class _Manager:
    """A service manager of the case's own, standing in for ``subprocess.run``:
    the load state of one name and the list of service unit files, for the
    system's scope and the person's own (``--user``); every question recorded,
    every other command refused."""

    def __init__(self, *, show_raises=None) -> None:
        self.show_raises = show_raises
        self.asked: list[list[str]] = []
        self.refused: list[str] = []

    def run(self, args, *a, **kw):
        argv = [str(x) for x in args] if not isinstance(args, str) else [args]
        scope, rest = (USER_UNITS, argv[2:]) if argv[1:2] == ["--user"] else (
            SYSTEM_UNITS, argv[1:])
        if argv[:1] == [SYSTEMCTL] and rest[:1] == ["show"]:
            self.asked.append(argv)
            if self.show_raises is not None:
                raise self.show_raises
            name = argv[-1]
            unit = name if name.endswith(".service") else name + ".service"
            state = "loaded" if unit in scope else "not-found"
            return subprocess.CompletedProcess(argv, 0, state + "\n", "")
        if argv[:1] == [SYSTEMCTL] and rest[:1] == ["list-unit-files"]:
            self.asked.append(argv)
            out = "".join("%s enabled enabled\n" % u for u in scope)
            return subprocess.CompletedProcess(argv, 0, out, "")
        self.refused.append(" ".join(argv))
        raise OSError("a command this case's manager does not answer, refused")


class _Case(unittest.TestCase):
    """The registry, the case's manager and one dispatch."""

    manager_kwargs: dict = {}

    def setUp(self) -> None:
        self.registry = ToolRegistry()
        self.registry.discover_tools()
        self.manager = _Manager(**self.manager_kwargs)
        self.written: list[object] = []
        self.escalated: list[dict] = []

        def _pkexec(call, tool_name, arguments, dispatch_token=None):
            self.escalated.append(dict(arguments))
            return ToolResult(call_id=call.call_id, name=tool_name,
                              content="(stood in)", success=True)

        for patcher in (
                mock.patch("subprocess.run", self.manager.run),
                mock.patch("intergen.tool_registry.write_record",
                           lambda record, *a, **kw: self.written.append(record)),
                mock.patch.object(self.registry, "_dispatch_via_pkexec", _pkexec)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _dispatch(self, arguments: dict) -> ToolResult:
        """One call through the registry; the review card recorded and refused."""
        self.cards: list[object] = []
        call = ToolCall(name="manage_services", arguments=dict(arguments),
                        source_of_request=Provenance.USER_DIRECT)
        return self.registry.execute(
            call, review_callback=lambda c, d: self.cards.append(d) or "deny")

    def assertRefusedBeforeTheCard(self, arguments: dict, *says: str) -> ToolResult:
        result = self._dispatch(arguments)
        self.assertEqual(self.cards, [],
                         "%r reached the review card" % (arguments,))
        self.assertFalse(result.success, "%r was not refused" % (arguments,))
        self.assertFalse(result.executed, "%r ran" % (arguments,))
        for words in says:
            self.assertIn(words, result.content)
        self.assertEqual(self.escalated, [])
        self.assertEqual(self.written, [],
                         "%r was recorded as a dispatch decision" % (arguments,))
        self.assertEqual(self.manager.refused, [])
        return result


# ── the cells: red before the correction ────────────────────────────────────

class AStateChangeNamingNoUnitIsRefusedBeforeTheCard(_Case):

    def test_every_unit_changing_action_with_no_service(self) -> None:
        for action in UNIT_CHANGING:
            with self.subTest(action=action):
                self.assertRefusedBeforeTheCard(
                    {"action": action}, "'%s' changes a service and names none" % action)
                self.assertEqual(self.manager.asked, [])


class AStateChangeNamingAnUnknownUnitIsRefusedBeforeTheCard(_Case):

    def test_restart_everything(self) -> None:
        self.assertRefusedBeforeTheCard(
            {"action": "restart", "service": "everything"},
            "names no unit this machine has",
            "the service manager knows no unit named 'everything'")
        self.assertEqual([argv[1] for argv in self.manager.asked],
                         ["show", "list-unit-files"])

    def test_a_name_the_manager_knows_only_in_another_case(self) -> None:
        self.assertRefusedBeforeTheCard(
            {"action": "restart", "service": "networkmanager"},
            "no unit named 'networkmanager'", "it has 'NetworkManager'")

    def test_a_user_mode_change_asks_the_persons_own_manager(self) -> None:
        self.assertRefusedBeforeTheCard(
            {"action": "restart", "service": "sshd", "user_mode": True},
            "names no unit this machine has")
        self.assertEqual(self.manager.asked[0][:3], [SYSTEMCTL, "--user", "show"])


class AManagerThatCannotBeReadProposesNothing(_Case):

    manager_kwargs = {"show_raises": OSError("the manager is not answering")}

    def test_restart_sshd(self) -> None:
        self.assertRefusedBeforeTheCard(
            {"action": "restart", "service": "sshd"},
            "the service manager could not be read")


# ── the controls: green at both ends ────────────────────────────────────────

class AKnownUnitStillReachesTheCard(_Case):

    def test_restart_sshd_and_its_alias(self) -> None:
        for service in ("sshd", "ssh"):
            with self.subTest(service=service):
                result = self._dispatch({"action": "restart", "service": service})
                self.assertEqual(len(self.cards), 1)
                self.assertFalse(result.success)
                self.assertEqual(self.escalated, [])
                self.assertEqual(self.manager.refused, [])

    def test_a_user_mode_change_to_a_unit_the_person_has(self) -> None:
        self._dispatch({"action": "restart", "service": "pipewire", "user_mode": True})
        self.assertEqual(len(self.cards), 1)
        self.assertEqual(self.manager.refused, [])
        self.assertEqual(self.escalated, [])

    def test_daemon_reload_names_no_unit_and_still_reaches_the_card(self) -> None:
        self._dispatch({"action": "daemon-reload"})
        self.assertEqual(len(self.cards), 1)
        self.assertEqual(self.manager.asked, [])


class AStatusQuestionIsNotAskedAbout(_Case):

    def test_status_of_a_unit_the_machine_does_not_have(self) -> None:
        self._dispatch({"action": "status", "service": "everything"})
        self.assertEqual(self.cards, [])
        self.assertEqual(self.manager.asked, [])
        self.assertEqual(self.manager.refused, ["systemctl status everything"])


if __name__ == "__main__":
    unittest.main()
