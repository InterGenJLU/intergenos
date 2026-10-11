# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A call the registry refuses before the dispatcher gate is recorded.

The design's audit log says every tool call records. ToolRegistry refused
three kinds of call before the gate was consulted, and returned without writing
anything: a call whose arguments the tool refuses at validation, a call the
tool classifies in the BLOCKED safety tier, and a call to a tool that is not
registered. So a request to restart a unit this machine's service manager does
not know, refused at validation, left no trace in the log a person reviews.

Each of the three refusals is now recorded before the registry returns, in the
shape of the gate's own reject: the outcome "denied", exit code 1, the reason as
the summary, and the effective provenance the gate computes for the call.

The cases drive the real ToolRegistry with its real tools. Nothing runs on the
machine: the audit writer is replaced by a list, the service manager's read by
a stand-in, and the two tools a control executes by recorders. Each refusal's
case is paired with a control whose call passes and runs, and whose record is
the executed one it always was.
"""

from __future__ import annotations

import unittest
from unittest import mock

from intergen import tool_registry as registry_module
from intergen.interfaces.provenance import IngressTracker, Provenance
from intergen.interfaces.types import ToolCall, ToolResult
from intergen.tool_registry import ToolRegistry
from intergen.tools.manage_services import ManageServicesTool
from intergen.tools.run_command import RunCommandTool


def _the_manager_knows_no_such_unit(name, *, user_mode=False):
    """The service manager, stood in: it knows no unit by any name it is asked
    about, as this machine's manager does not know a unit the model invented.
    A case never asks the service manager of the machine it runs on."""
    return "unknown", "", f"the service manager knows no unit named {name!r}"


def _the_manager_knows_it(name, *, user_mode=False):
    """The service manager, stood in: it knows every unit it is asked about."""
    return "known", name, ""


def _call(name: str, arguments: dict, call_id: str,
          provenance: Provenance = Provenance.USER_DIRECT) -> ToolCall:
    return ToolCall(name=name, arguments=arguments, call_id=call_id,
                    source_of_request=provenance)


class _Case(unittest.TestCase):

    def setUp(self) -> None:
        self.records: list = []
        self.ran: list = []
        self.registry = ToolRegistry()
        self.registry.discover_tools()
        for patcher in (
            mock.patch.object(registry_module, "write_record",
                              side_effect=self._record),
            mock.patch.object(ManageServicesTool, "execute",
                              side_effect=self._ran("manage_services"),
                              autospec=False),
            mock.patch.object(RunCommandTool, "execute",
                              side_effect=self._ran("run_command"),
                              autospec=False),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _record(self, record, *a, **k) -> bool:
        self.records.append(record)
        return True

    def _ran(self, name: str):
        def run(arguments, *a, **k) -> ToolResult:
            self.ran.append((name, dict(arguments)))
            return ToolResult(call_id="", name=name, content=f"{name} ran",
                              success=True)
        return run

    def _manager(self, read) -> None:
        patcher = mock.patch.object(ManageServicesTool, "_read_service_unit",
                                    staticmethod(read))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _one_record(self):
        self.assertEqual(len(self.records), 1,
                         f"expected one audit record, got {self.records}")
        return self.records[0]

    def assertRecordedAsRefused(self, call: ToolCall, result: ToolResult,
                                effective: str = "user_direct") -> None:
        self.assertFalse(result.success)
        self.assertFalse(result.executed)
        self.assertEqual(self.ran, [], "a refused call must run nothing")
        record = self._one_record()
        self.assertEqual(record.tool_name, call.name)
        self.assertEqual(record.arguments, call.arguments)
        self.assertEqual(record.user_decision, "denied")
        self.assertEqual(record.exit_code, 1)
        self.assertEqual(record.declared_provenance,
                         call.source_of_request.value)
        self.assertEqual(record.effective_provenance, effective)
        self.assertTrue(record.result_summary,
                        "the record must carry the refusal's reason")


class ACallRefusedAtValidationIsRecorded(_Case):

    def test_a_state_changing_call_refused_at_validation_is_recorded(self):
        self._manager(_the_manager_knows_no_such_unit)
        call = _call("manage_services",
                     {"action": "restart", "service": "no-such-unit"}, "v1")
        result = self.registry.execute(call)
        self.assertTrue(result.content.startswith("Validation error: "),
                        result.content)
        self.assertRecordedAsRefused(call, result)
        self.assertEqual(self.records[0].result_summary, result.content[:256])
        self.assertIn("no-such-unit", self.records[0].result_summary)

    def test_a_read_only_call_refused_at_validation_is_recorded(self):
        call = _call("read_file", {}, "v2")
        result = self.registry.execute(call)
        self.assertEqual(result.content,
                         "Validation error: Missing required parameter: path")
        self.assertRecordedAsRefused(call, result)
        self.assertEqual(self.records[0].result_summary, result.content)

    def test_the_refusal_is_recorded_with_the_provenance_the_gate_computes(self):
        # An ingress tool fired earlier in the turn: the gate escalates a
        # user_direct call to user_implied, and so does the refusal's record.
        self._manager(_the_manager_knows_no_such_unit)
        tracker = IngressTracker()
        tracker.record_tool_call("web_search")
        call = _call("manage_services",
                     {"action": "restart", "service": "no-such-unit"}, "v3")
        result = self.registry.execute(call, ingress_tracker=tracker)
        self.assertRecordedAsRefused(call, result, effective="user_implied")
        self.assertEqual(self.records[0].ingress_tools_this_turn,
                         ["web_search"])

    def test_control_a_call_that_passes_validation_runs_and_is_recorded_as_executed(self):
        self._manager(_the_manager_knows_it)
        call = _call("manage_services",
                     {"action": "status", "service": "sshd"}, "v4")
        result = self.registry.execute(call)
        self.assertTrue(result.success, result.content)
        self.assertEqual(self.ran, [("manage_services", call.arguments)])
        record = self._one_record()
        self.assertEqual(record.user_decision, "executed")
        self.assertEqual(record.exit_code, 0)
        self.assertEqual(record.result_summary, "manage_services ran")
        self.assertEqual(record.effective_provenance, "user_direct")


class ACallInTheBlockedTierIsRecorded(_Case):

    def test_a_blocked_tier_call_is_recorded(self):
        call = _call("run_command", {"command": "rm -rf /"}, "b1")
        result = self.registry.execute(call)
        self.assertTrue(result.blocked)
        self.assertRecordedAsRefused(call, result)
        self.assertIn("BLOCKED", self.records[0].result_summary)

    def test_control_a_command_that_is_not_blocked_runs_and_is_recorded_as_executed(self):
        call = _call("run_command", {"command": "uname -r"}, "b2")
        result = self.registry.execute(call)
        self.assertTrue(result.success, result.content)
        self.assertEqual(self.ran, [("run_command", call.arguments)])
        record = self._one_record()
        self.assertEqual(record.user_decision, "executed")
        self.assertEqual(record.exit_code, 0)
        self.assertEqual(record.result_summary, "run_command ran")


class ACallToAToolThatIsNotRegisteredIsRecorded(_Case):

    def test_a_call_to_an_unregistered_tool_is_recorded(self):
        call = _call("no_such_tool", {"x": "y"}, "u1")
        result = self.registry.execute(call)
        self.assertEqual(result.content, "Unknown tool: no_such_tool")
        self.assertRecordedAsRefused(call, result)
        self.assertEqual(self.records[0].result_summary, result.content)


if __name__ == "__main__":
    unittest.main()
