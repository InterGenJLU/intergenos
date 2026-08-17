# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Render what the emitter emits through the SHIPPED chat template.

This is the reality leg of the tool-call-shape work: the emitter's contract is
not "produce a mapping" for its own sake, it is "produce something the template
turns into a tool call with its arguments intact". Only a render proves that.

It lives repo-side rather than in ``intergen/tests/`` because it needs jinja2,
and jinja2 is not a declared intergen dependency — a shipped test may not add
one. The shipped, dependency-free half is
``intergen/tests/test_corpus_to_sft_tool_call_shape.py``, which asserts the
emitted shape directly and pins the template's own ``arguments is mapping``
guard, so the contract is still covered on an installed system where this file
does not exist.

What is proven here, by execution:

* the emitted dispatch turn renders as ``<function=NAME>`` WITH its
  ``<parameter=…>`` blocks;
* the two shapes the emitter used to produce do NOT render — the JSON-in-content
  form produces no ``<function=`` at all, and JSON-string arguments produce the
  function name with every argument dropped. Those two are rendered here as
  explicit negative controls, so this file demonstrates the difference rather
  than asserting the good case alone.
"""
from __future__ import annotations

import json
import unittest

from intergen.tests import corpus_to_sft as sft
from intergen.tests.test_corpus_to_sft_tool_call_shape import (
    _SCHEMAS, _SYSTEM, _entry, _flow_entry, template_source,
)

try:
    from jinja2 import BaseLoader, Environment
    from jinja2.ext import loopcontrols
    _JINJA_ERR = None
except Exception as exc:                                    # pragma: no cover
    _JINJA_ERR = exc


TOOLS = [{
    "type": "function",
    "function": {
        "name": "manage_packages",
        "description": "install/remove packages",
        "parameters": {
            "type": "object",
            "properties": {"action": {"type": "string"},
                           "package": {"type": "string"},
                           "source_of_request": {"type": "string"}},
            "required": ["action", "package", "source_of_request"],
        },
    },
}, {
    "type": "function",
    "function": {
        "name": "manage_services",
        "description": "start/stop/restart services",
        "parameters": {
            "type": "object",
            "properties": {"action": {"type": "string"},
                           "service": {"type": "string"},
                           "source_of_request": {"type": "string"}},
            "required": ["action", "service", "source_of_request"],
        },
    },
}]


def _render(messages):
    """Render a message list and return (full, label) where label is the delta
    the trainer would supervise — the same prefix-delta the round-N trainers
    take, so what is asserted here is the actual training label."""
    env = Environment(loader=BaseLoader(), extensions=[loopcontrols])
    env.policies["json.dumps_kwargs"] = {"ensure_ascii": False}
    tpl = env.from_string(template_source()[1])
    full = tpl.render(messages=messages, tools=TOOLS,
                      add_generation_prompt=False)
    prefix = tpl.render(messages=messages[:-1], tools=TOOLS,
                        add_generation_prompt=True)
    if not full.startswith(prefix):
        raise AssertionError(
            "the rendered prefix is not a prefix of the rendered turn, so the "
            "label cannot be taken as a delta")
    return full, full[len(prefix):]


@unittest.skipIf(_JINJA_ERR is not None,
                 f"jinja2 unavailable ({_JINJA_ERR}) — the shipped shape "
                 f"assertions in intergen/tests/"
                 f"test_corpus_to_sft_tool_call_shape.py still run; what is "
                 f"UNPROVEN without this file is the render itself")
class EmittedDispatchRendersAsACall(unittest.TestCase):
    def _sample(self, obj):
        sft.validate_training_entry(obj, locator="<t>", tool_schemas=_SCHEMAS)
        return sft.entry_to_sample(obj, system_prompt=_SYSTEM)

    def test_dispatch_turn_renders_with_its_arguments_intact(self):
        s = self._sample(_entry())
        _full, label = _render(s["messages"])
        self.assertIn("<function=manage_packages>", label)
        self.assertIn("<parameter=action>", label)
        self.assertIn("<parameter=package>", label)
        self.assertIn("<parameter=source_of_request>", label)
        self.assertIn("btop", label)
        # and the label is NOT the raw JSON form
        self.assertNotIn('"arguments"', label)

    def test_tool_flow_history_call_renders_with_its_arguments_intact(self):
        s = self._sample(_flow_entry())
        # the history call is messages[2]; render up to and including it
        _full, label = _render(s["messages"][:3])
        self.assertIn("<function=manage_services>", label)
        self.assertIn("<parameter=service>", label)
        self.assertIn("cups", label)

    def test_negative_control_json_in_content_renders_no_call_at_all(self):
        """The first shape the emitter used to produce."""
        s = self._sample(_entry())
        payload = json.dumps(
            {"name": "manage_packages",
             "arguments": {"action": "install", "package": "btop",
                           "source_of_request": "user_direct"}},
            ensure_ascii=False, sort_keys=True)
        s["messages"][2] = {"role": "assistant",
                            "content": f"<tool_call>\n{payload}\n</tool_call>"}
        _full, label = _render(s["messages"])
        self.assertNotIn("<function=", label,
                         "the template must not be able to make a call out of "
                         "content text — if it can, the premise of this fix "
                         "changed and the emitter must be re-derived")
        self.assertIn('"arguments"', label)

    def test_negative_control_string_arguments_render_argument_free(self):
        """The second shape the emitter used to produce."""
        s = self._sample(_entry())
        fn = s["messages"][2]["tool_calls"][0]["function"]
        fn["arguments"] = json.dumps(fn["arguments"], sort_keys=True)
        _full, label = _render(s["messages"])
        self.assertIn("<function=manage_packages>", label)
        self.assertNotIn("<parameter=", label,
                         "string arguments must render argument-free — this is "
                         "the measured defect the mapping form exists to avoid")
        self.assertNotIn("btop", label)


if __name__ == "__main__":
    unittest.main()
