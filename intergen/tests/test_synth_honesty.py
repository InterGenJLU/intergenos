# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Synthesis honesty — the failure prompt must distinguish not-executed from errored.

The shutdown fabrication ("executed successfully" on a DENIED dispatch) came from
the synthesis prompt telling the model it executed for EVERY non-success case.
_synthesis_prompt now branches on `executed`: a not-executed dispatch gets a
BINDING "did not run, never claim success" instruction; a ran-but-errored result
keeps the "it executed, describe the output" instruction. Pure string logic.
"""

from __future__ import annotations

import unittest

from intergen.llm import LLMRouter


class SynthesisPromptTests(unittest.TestCase):
    def setUp(self):
        self.r = LLMRouter.__new__(LLMRouter)  # _synthesis_prompt only reads _SYNTHESIS_PROMPT

    def test_not_executed_is_binding_no_success_claim(self):
        p = self.r._synthesis_prompt(success=False, executed=False).lower()
        self.assertIn("not executed", p)
        self.assertIn("did not run", p)
        self.assertIn("must not claim", p)
        # offers escalation rather than a flat dead-end
        self.assertIn("permission", p)

    def test_executed_but_errored_says_it_ran(self):
        p = self.r._synthesis_prompt(success=False, executed=True).lower()
        self.assertIn("executed", p)
        self.assertIn("non-zero", p)
        # must NOT carry the not-executed binding language
        self.assertNotIn("did not run", p)

    def test_not_executed_and_errored_prompts_differ(self):
        # The whole point: the two failure cases are no longer conflated.
        denied = self.r._synthesis_prompt(success=False, executed=False)
        errored = self.r._synthesis_prompt(success=False, executed=True)
        self.assertNotEqual(denied, errored)

    def test_success_is_the_plain_prompt(self):
        p = self.r._synthesis_prompt(success=True, executed=True)
        self.assertEqual(p, self.r._SYNTHESIS_PROMPT)


class ToolResultExecutedFieldTests(unittest.TestCase):
    def test_executed_defaults_true(self):
        from intergen.interfaces.types import ToolResult
        r = ToolResult(call_id="", name="x", content="ok")
        self.assertTrue(r.executed)

    def test_not_executed_is_explicit(self):
        from intergen.interfaces.types import ToolResult
        r = ToolResult(call_id="", name="x", content="denied",
                       success=False, executed=False)
        self.assertFalse(r.executed)


if __name__ == "__main__":
    unittest.main()
