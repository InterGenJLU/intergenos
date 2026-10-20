# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A printer-list failure explains its meaning and retains the original output."""

from unittest.mock import Mock

import pytest

from intergen.interfaces.types import LLMResponse, ToolResult
from intergen.llm import LLMRouter
from intergen.router import ConversationRouter
from intergen.tests.test_router_explain import _repo_explain_router


EMPTY_PRINTERS = "[stderr]\nlpstat: No destinations added.\n\n[exit code: 1]"
RECORDED_REPLY = (
    "The command `lpstat -p -d` resulted in an error with the message: "
    "`lpstat: No destinations added.` The exit code was 1."
)


def test_accepting_a_printer_list_explains_the_recorded_result():
    router = _repo_explain_router()
    router._pending_action_offer = ("lpstat -p -d", "run_command", "list printers")
    router._ingress_tracker = None
    router._trust_state = None
    router._review_callback = None
    router._record = Mock()
    router._tools = Mock()
    router._tools.execute.return_value = ToolResult(
        call_id="printer-list", name="run_command", content=EMPTY_PRINTERS,
        success=False, executed=True,
    )
    router._llm = object.__new__(LLMRouter)
    router._llm._last_finish_reason = "stop"
    router._llm._last_semantic_flags = []
    router._llm.chat = Mock(return_value=LLMResponse(
        text=RECORDED_REPLY, model="local", local=True,
    ))
    result = router._resolve_pending_action_offer("yes", 0.0)
    assert "no configured printers" in result.text.lower()
    assert "exit code" not in result.text.lower()
    assert result.full_output == EMPTY_PRINTERS
    assert result.tool_results[0].success is False
    assert result.answer_linkage.kind == "dispatch"
    assert result.answer_linkage.renderer == "template"
    assert result.used_llm is False
    assert router._tools.execute.call_args.args[0].arguments == {
        "command": "lpstat -p -d",
    }
    router._llm.chat.assert_not_called()


@pytest.mark.parametrize("output", [
    "[stderr]\nlpstat: Scheduler is not running.\n[exit code: 1]",
    "[stderr]\npermission denied\n[exit code: 1]",
    EMPTY_PRINTERS + "\nAn additional error occurred.",
])
def test_other_errors_are_not_reported_as_an_empty_printer_list(output):
    assert ConversationRouter._template_synthesis(
        "lpstat -p -d", output, "run_command",
        executed_command="lpstat -p -d", succeeded=False) is None


def test_a_different_tools_text_does_not_become_a_printer_result():
    assert ConversationRouter._template_synthesis(
        "list printers", EMPTY_PRINTERS, "read_file",
        executed_command="lpstat -p -d", succeeded=False) is None


def test_a_raw_request_keeps_the_error_and_exit_status():
    assert ConversationRouter._template_synthesis(
        "show the exact output", EMPTY_PRINTERS, "run_command",
        executed_command="lpstat -p -d", succeeded=False) == EMPTY_PRINTERS


@pytest.mark.parametrize(("command", "succeeded"), [
    ("printf example", False),
    ("lpstat -p -d", True),
    ("lpstat -p -d", None),
    (None, False),
    ("lpstat '", False),
])
def test_printer_interpretation_requires_the_executed_command_and_failure(
        command, succeeded):
    assert ConversationRouter._template_synthesis(
        "lpstat -p -d", EMPTY_PRINTERS, "run_command",
        executed_command=command, succeeded=succeeded) is None


def test_no_default_destination_also_keeps_the_known_empty_printer_meaning():
    output = "no system default destination\n\n" + EMPTY_PRINTERS
    assert ConversationRouter._template_synthesis(
        "which printers are configured", output, "run_command",
        executed_command="/usr/bin/lpstat -p -d", succeeded=False
    ) == "CUPS reports no configured printers."
