# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Live service data stays suitable for a person when shown without synthesis."""

import time
from unittest.mock import Mock

from intergen.interfaces.types import LLMResponse, MessageRole
from intergen.llm import LLMRouter
from intergen.router import ConversationRouter
from intergen.state_cache import CachedValue, StateCache


QUESTION = "Tell me what's running on THIS system right now"
LEAKED_INSTRUCTION = "if a service is not in this list, say"
SERVICE = "cups.service loaded active running CUPS Scheduler"


def service_data():
    cache = StateCache()
    for key, value in (
        ("load_average", "0.1 0.2 0.3 1/500 1"),
        ("service_list", SERVICE),
    ):
        cache._cache[key] = CachedValue(
            value=value, timestamp=time.monotonic(), command="fixture",
            stale_after=60,
        )
    return cache.get_system_map_data(QUESTION)


def test_service_data_describes_the_visible_list_without_addressing_the_model():
    text = service_data()
    assert SERVICE in text
    assert LEAKED_INSTRUCTION not in text
    assert "visible" in text.lower()
    assert "bounded" in text.lower()


def test_service_absence_instruction_is_in_the_system_role():
    router = object.__new__(ConversationRouter)
    router._llm = object.__new__(LLMRouter)
    router._conversation_history = []
    router._max_history = 6
    messages = router._build_system_map_messages(QUESTION, service_data())
    qualification = "not among the visible running services"
    assert any(
        message.role == MessageRole.SYSTEM and qualification in message.content
        for message in messages
    )
    assert not any(
        message.role == MessageRole.USER and qualification in message.content
        for message in messages
    )


def test_unsynthesized_service_answer_does_not_repeat_an_internal_instruction():
    router = object.__new__(ConversationRouter)
    router._llm = object.__new__(LLMRouter)
    router._conversation_history = []
    router._max_history = 6
    router._llm.chat = Mock(return_value=LLMResponse(
        text="No usable synthesis.", model="local", local=True,
        quality_passed=False,
    ))
    router._append_history = Mock()
    result = router._try_system_map(QUESTION, service_data())
    assert SERVICE in result.text
    assert LEAKED_INSTRUCTION not in result.text
    assert result.answer_linkage.renderer == "system_map_data_verbatim"
