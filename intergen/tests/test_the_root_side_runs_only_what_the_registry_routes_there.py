# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The root side runs only a call the registry itself routes there.

The registry sends a call across the privileged boundary exactly when
_classify_risk_tier calls it PRIVILEGED_STATE_CHANGING (decision.needs_pkexec).
A read-only call of a tool on the privileged list, and a change on the person's
own service manager (manage_services with user_mode), run in the person's own
process. privileged_dispatch re-checked the allowlist, the approval token, the
arguments and the BLOCKED tier, but not that route: a request for either kind
that reached it carrying a valid token ran as root, on the caller's word that it
belonged there — and as root, systemctl --user reaches root's own manager, not
the person's.

These cases drive main() with the REAL tools' own classification and
validation; the registry's discovery is mocked, each tool's execute and the
service manager's answer about a unit are stood in, and the stores are
redirected (the lifecycle module's harness). Nothing runs as root.
"""

from __future__ import annotations

import os
import tempfile
from unittest import mock

from intergen import dispatch_token as dt
from intergen import privileged_dispatch as pd
from intergen import tool_registry as tr
from intergen.interfaces.provenance import ToolRiskTier
from intergen.interfaces.types import ToolResult
from intergen.tests.test_privileged_dispatch_lifecycle import (
    _KEY, _LifecycleTestCase)
from intergen.tools.manage_packages import ManagePackagesTool
from intergen.tools.manage_services import ManageServicesTool
from intergen.tools.run_command import RunCommandTool
from intergen.tools.write_file import WriteFileTool


class TheRouteIsReDerivedAtTheRootSide(_LifecycleTestCase):

    def stand_in(self, tool):
        """The real tool, its execute and the service manager's answer about
        a unit stood in; returns what each recorded."""
        ran, asked = [], []

        def _execute(arguments):
            ran.append(dict(arguments))
            return ToolResult(call_id="", name=tool.name, content="ran",
                              success=True)

        def _read_service_unit(service, user_mode=False):
            asked.append((service, user_mode))
            return "known", service, ""

        tool.execute = _execute
        if isinstance(tool, ManageServicesTool):
            tool._read_service_unit = _read_service_unit
        self.set_tool(tool)
        return ran, asked

    def send(self, name, args):
        """One request for (name, args) with its own valid approval token;
        returns (exit code, the dispatcher's messages, the recorded states)."""
        token = dt.mint_token(name, args, self.uid, key=_KEY, ttl_seconds=120)
        nonce = dt.verify_token(token, name, args, self.uid,
                                username=self.user).nonce
        with mock.patch.object(pd, "_emit") as emitted:
            code, _ = self.dispatch(token, tool=name, args=args)
        said = " ".join(str(c.args[0]) for c in emitted.call_args_list)
        return code, said, self.states(nonce)

    def test_a_user_mode_service_act_is_refused_before_anything_runs(self):
        tool = ManageServicesTool()
        ran, asked = self.stand_in(tool)
        args = {"action": "restart", "service": "sshd", "user_mode": True}
        self.assertIs(tr._classify_risk_tier(tool, args, "manage_services"),
                      ToolRiskTier.USER_SCOPE_STATE_CHANGING,
                      "the registry no longer keeps this act in-person")

        code, said, states = self.send("manage_services", args)

        self.assertNotEqual(code, 0)
        self.assertEqual(ran, [], "a user-manager act ran as root")
        self.assertEqual(asked, [], "the service manager was asked, as root, "
                                    "before the refusal")
        self.assertIn("user_mode true", said)
        self.assertIn("refusing dispatch", said)
        self.assertEqual(states, [pd.STATE_CLAIMED, pd.STATE_TERMINAL_REFUSED],
                         "the refusal is not recorded, or the approval spent")

    def test_a_read_only_call_is_refused_before_anything_runs(self):
        for name, tool, args in (
                ("manage_services", ManageServicesTool(),
                 {"action": "status", "service": "sshd"}),
                ("manage_packages", ManagePackagesTool(), {"action": "list"}),
                ("run_command", RunCommandTool(), {"command": "uname -r"})):
            with self.subTest(tool=name, arguments=args):
                ran, asked = self.stand_in(tool)
                self.assertIs(tr._classify_risk_tier(tool, args, name),
                              ToolRiskTier.READ_ONLY)

                code, said, states = self.send(name, args)

                self.assertNotEqual(code, 0)
                self.assertEqual(ran, [], "a read-only call ran as root")
                self.assertEqual(asked, [])
                self.assertIn("read_only", said)
                self.assertEqual(
                    states, [pd.STATE_CLAIMED, pd.STATE_TERMINAL_REFUSED])

    def test_a_call_the_registry_routes_here_still_runs(self):
        target = tempfile.TemporaryDirectory(prefix="root-route-")
        self.addCleanup(target.cleanup)
        for name, tool, args in (
                ("manage_services", ManageServicesTool(),
                 {"action": "restart", "service": "sshd"}),
                ("manage_packages", ManagePackagesTool(),
                 {"action": "install", "package": "htop"}),
                ("run_command", RunCommandTool(),
                 {"command": "touch " + os.path.join(target.name, "x")}),
                ("write_file", WriteFileTool(),
                 {"path": os.path.join(target.name, "notes.txt"),
                  "content": "x"})):
            with self.subTest(tool=name, arguments=args):
                ran, _asked = self.stand_in(tool)
                self.assertIs(tr._classify_risk_tier(tool, args, name),
                              ToolRiskTier.PRIVILEGED_STATE_CHANGING)

                code, said, states = self.send(name, args)

                self.assertEqual(code, 0, said)
                self.assertEqual(ran, [args])
                self.assertEqual(states[-1], pd.STATE_TERMINAL_OK)
