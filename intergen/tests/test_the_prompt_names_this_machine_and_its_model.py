# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The system prompt states where the assistant runs and what is serving it.

Asked what it is, the assistant answered out of whatever its weights had
absorbed — and a local model trained by somebody else names that somebody else,
in the assistant's own voice, about the machine the person is sitting at. The
two facts that make the answer true are both known when the prompt is built:
the operating system this assistant is part of, and the model actually serving
the turn. Stating them in the prompt screens the false answer where it is
cheapest to screen, and leaves the model a true answer to give instead of only
a forbidden one.

These cases pin:

* every prompt path names the operating system;
* every prompt path names the served model when one is known, and the name is
  the one the ENGINE reports, not a second copy stored elsewhere;
* when no model is known the line says a local model on this machine and
  invents no name — a guessed name is the failure this line exists to prevent;
* an engine that cannot be read leaves the prompt buildable; a prompt must not
  fail because an attribute was not what the reader expected;
* every path still clears its own character ceiling WITH a model name present,
  which is the path that actually runs. The ceilings are re-measured in this
  change for exactly that reason.
"""

from __future__ import annotations

import unittest

from intergen import llm
from intergen.llm import (LLMRouter, build_system_prompt,
                          system_prompt_char_budget)

#: The operating system this assistant is part of, written out here rather than
#: imported, so that these cases run against a tree that does not yet name it
#: and fail on the BEHAVIOUR instead of on an import.
MACHINE_NAME = "InterGenOS"

#: A served model name the length of the one this project actually serves.
A_SERVED_MODEL = "Qwen3.5-9B-intergen-round3-Q4_K_M"

PATHS = tuple(llm._SYSTEM_PROMPT_CHAR_BUDGETS)


class _Engine:
    """Stands in for the serving engine, which is what loaded the model file."""

    def __init__(self, name):
        self._name = name

    @property
    def model_name(self):
        if isinstance(self._name, Exception):
            raise self._name
        return self._name


def _router_with(engine):
    router = LLMRouter.__new__(LLMRouter)
    router._serving_engine = engine
    return router


class EveryPathNamesTheSystem(unittest.TestCase):

    def test_every_path_names_the_operating_system(self):
        for query_type, with_tools in PATHS:
            with self.subTest(path=(query_type, with_tools)):
                prompt = build_system_prompt(query_type, with_tools=with_tools,
                                             served_model=A_SERVED_MODEL)
                self.assertIn(MACHINE_NAME, prompt)

    def test_every_path_names_the_served_model(self):
        for query_type, with_tools in PATHS:
            with self.subTest(path=(query_type, with_tools)):
                prompt = build_system_prompt(query_type, with_tools=with_tools,
                                             served_model=A_SERVED_MODEL)
                self.assertIn(A_SERVED_MODEL, prompt,
                              "this path does not tell the model which model "
                              "it is, so an answer naming another vendor is "
                              "screened nowhere")

    def test_the_two_names_are_in_the_same_statement(self):
        """Apart, they are two facts. Together, they are the answer to
        'what are you' — which is the question being screened."""
        line = llm.runtime_identity_line(A_SERVED_MODEL)
        self.assertIn(MACHINE_NAME, line)
        self.assertIn(A_SERVED_MODEL, line)

    def test_with_no_model_known_no_name_is_invented(self):
        line = llm.runtime_identity_line(None)
        self.assertIn(MACHINE_NAME, line)
        self.assertNotIn(A_SERVED_MODEL, line)
        self.assertIn("local model", line)


class TheNameComesFromTheEngine(unittest.TestCase):

    def test_the_prompt_carries_the_name_the_engine_reports(self):
        messages = _router_with(_Engine(A_SERVED_MODEL)).build_system_messages()
        self.assertIn(A_SERVED_MODEL, messages[0].content)

    def test_a_model_swapped_under_the_daemon_changes_the_prompt(self):
        """The name is asked of the engine on every build, so it cannot go
        stale behind a second copy."""
        engine = _Engine(A_SERVED_MODEL)
        router = _router_with(engine)
        self.assertIn(A_SERVED_MODEL, router.build_system_messages()[0].content)
        engine._name = "SomeOther-4B"
        second = router.build_system_messages()[0].content
        self.assertIn("SomeOther-4B", second)
        self.assertNotIn(A_SERVED_MODEL, second)

    def test_no_engine_means_no_name_and_no_failure(self):
        content = _router_with(None).build_system_messages()[0].content
        self.assertIn(MACHINE_NAME, content)
        self.assertIn("local model", content)

    def test_a_router_built_without_its_constructor_still_builds_a_prompt(self):
        """Routers ARE built that way — the router cases construct one with
        __new__ and set only the fields they need. A prompt that could not be
        assembled because the engine field was absent would turn a partially
        built router into one that cannot answer at all. Found by the full
        suite: eighteen router cases failed on exactly this."""
        bare = LLMRouter.__new__(LLMRouter)
        content = bare.build_system_messages()[0].content
        self.assertIn(MACHINE_NAME, content)
        self.assertIn("local model", content)

    def test_an_engine_that_raises_means_no_name_and_no_failure(self):
        engine = _Engine(RuntimeError("the engine is mid-restart"))
        content = _router_with(engine).build_system_messages()[0].content
        self.assertIn(MACHINE_NAME, content)
        self.assertIn("local model", content)

    def test_the_engines_placeholder_is_not_used_as_a_model_name(self):
        """The engine reports a dash when it has never started. A dash is not
        a model name and must not be presented to the model as one."""
        content = _router_with(_Engine("—")).build_system_messages()[0].content
        self.assertIn("local model", content)
        self.assertNotIn("serving this conversation is —", content)


class TheCeilingsStillMeanSomething(unittest.TestCase):

    def test_every_path_clears_its_ceiling_with_a_model_name_present(self):
        """A ceiling measured without the model name would be cleared by less
        than the headroom convention claims on the path that actually runs."""
        for query_type, with_tools in PATHS:
            with self.subTest(path=(query_type, with_tools)):
                measured = len(build_system_prompt(
                    query_type, with_tools=with_tools,
                    served_model=A_SERVED_MODEL))
                ceiling = system_prompt_char_budget(query_type, with_tools)
                self.assertLessEqual(measured, ceiling)


if __name__ == "__main__":
    unittest.main()
