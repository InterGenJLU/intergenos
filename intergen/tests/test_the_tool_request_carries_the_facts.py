# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The tool request carries the turn's facts, on every lane's model.

A tool request is the system prompt and one user message: the conversation's
history is left out of it, because the model copies tool-calling patterns from
history instead of following its rules. The model client used to make that cut
by keeping the first and the last message, and what the route had found for
the model went with the history: the installed-tool facts, the capability
guard and, once the route looked for one, a verified wiki passage. Measured on
a 9B lane on 2026-10-29, asked "do you have root access on this system", the
model answered "Yes — I run with full privileges and act on your behalf" from
a request that carried the system prompt and the question alone.

THE TEST THAT FINDS THIS CLASS: the bytes the model server receives. A loopback
endpoint keeps each request's body; the route runs in full, after an earlier
exchange, as each lane's model. The body must carry exactly two messages, the
fact inside the user message ahead of the question, and none of the history.
Each body's size is checked against the context the daemon starts each lane's
model server with, less the chat template's allowance; the reply's room is not
part of this check.
"""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

import intergen.glass as glass
from intergen import persona
from intergen.config import _DEFAULTS
from intergen.interfaces.types import HardwareTierLevel
from intergen.tests.test_both_surfaces_send_the_model_the_same_request import (
    CASES, FACTS, PASSAGE, _router)
from intergen.tests.test_glass_model_request import _Capture, _sse

pytestmark = pytest.mark.reaches_a_server_it_starts_itself

LANES = (HardwareTierLevel.TIER_1, HardwareTierLevel.TIER_2,
         HardwareTierLevel.TIER_3)
_TOOL_CASES = {c.name: c for c in CASES if c.route == "llm_tools"}
#: Each tool case and the text of the fact its route adds.
FACT_CASES = (
    (_TOOL_CASES["tools_reference"], FACTS),
    (_TOOL_CASES["tools_wiki"], PASSAGE),
    (_TOOL_CASES["tools_system_category_guard"], persona.SYSTEM_CAPABILITY_GUARD),
)
EARLIER_QUESTION = "what did the earlier turn ask about the garden shed"
EARLIER_ANSWER = "the earlier answer named the garden shed's padlock"
#: The context the daemon starts the lane's model server with
#: (llm.context_size; the packaged configuration sets the same value).
CONTEXT_TOKENS = _DEFAULTS["llm"]["context_size"]
#: The three lanes' tokenizers are byte-level: no token is shorter than one
#: byte, so a body's size in bytes bounds its prompt from above without the
#: server's tokenizer, short of what the chat template wraps around the
#: messages and the tool schemas, which this allows for.
TEMPLATE_ALLOWANCE_TOKENS = 1024


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("INTERGEN_GLASS", raising=False)
    glass._glass = None
    yield tmp_path
    glass._glass = None


def tool_request(case, lane, root) -> tuple[bytes, dict]:
    """Route the case's question in full after an earlier exchange, served as
    the lane's model; the first request's bytes and its body."""
    capture = _Capture([(200, "text/event-stream", _sse("An answer."))])
    try:
        router, _registry, _cache, _assemblies = _router(
            case, root, capture, lane)
        router._append_history(EARLIER_QUESTION, EARLIER_ANSWER)
        result = router.route(case.question)
    finally:
        capture.close()
    assert result.source == "llm_tools", result.source
    raw = capture.bodies[0]
    return raw, json.loads(raw)


@pytest.mark.parametrize("lane", LANES, ids=lambda t: t.name)
@pytest.mark.parametrize("case,fact", FACT_CASES,
                         ids=[c.name for c, _fact in FACT_CASES])
def test_the_tool_request_carries_the_fact_ahead_of_the_question(
        case, fact, lane, state):
    raw, body = tool_request(case, lane, state / lane.name / case.name)
    assert body["tools"], "the request offered no tools: not a tool request"
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["system", "user"], roles
    user = body["messages"][1]["content"]
    assert fact in user, f"the request's user message lacks the fact: {user!r}"
    assert user.endswith("\n\n" + case.question), user
    assert user.index(fact) < user.rindex(case.question)
    for earlier in (EARLIER_QUESTION, EARLIER_ANSWER):
        assert earlier.encode() not in raw, "the history reached a tool request"
    assert len(raw) + TEMPLATE_ALLOWANCE_TOKENS <= CONTEXT_TOKENS, (
        f"a {len(raw)}-byte request may not fit {CONTEXT_TOKENS} tokens")


def test_a_tool_turn_with_no_fact_sends_the_question_alone(state):
    """The control: with nothing found for the model, the user message is the
    question itself, as before the facts were carried."""
    case = replace(_TOOL_CASES["tools_reference"], reference=False,
                   question="tell me a short joke about printers")
    _raw, body = tool_request(case, HardwareTierLevel.TIER_2, state)
    assert [m["content"] for m in body["messages"][1:]] == [case.question]
