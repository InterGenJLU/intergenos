# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The emitter must write tool calls in the ONE form the chat template renders.

Decided 2026-08-13, on a measurement rather than a reading. The chat template
(``intergen/data/internvl-tool-template.jinja``) serialises an assistant tool
call as an XML block::

    <tool_call>
    <function=manage_packages>
    <parameter=action>
    install
    </parameter>
    </function>
    </tool_call>

and it emits the ``<parameter=…>`` blocks **only** when ``tool_call.arguments``
is a mapping — the template's own guard, ``{%- if tool_call.arguments is
mapping %}``. Two other shapes therefore render wrong, and both were being
emitted:

* a call written as a JSON blob inside the assistant's **content** renders as
  that literal text and produces no ``<function=`` block at all — the template
  never sees a tool call, and the template's system block instructs the model in
  the same rendered row that a call MUST be an inner ``<function=…>`` block;
* a structured call whose ``arguments`` are a JSON **string** renders as the
  function name with **every argument dropped**.

Both were measured against the real template before this change; the render
itself is proven in ``tests/intergen/test_corpus_to_sft_template_render.py``,
which is repo-side because it needs jinja2 and jinja2 is not an intergen
dependency. THIS file is the shipped half and adds no dependency: it asserts the
emitted shape directly, proves the emitter's gate can actually fail (three
negative controls), and pins the template guard the whole contract rests on, so
a template change that moved the goalposts fails here rather than silently.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from intergen.tests.corpus_loader import CorpusError
from intergen.tests import corpus_to_sft as sft

_SYSTEM = "You are InterGen."

_SCHEMAS = {
    "manage_packages": {
        "type": "object",
        "properties": {"action": {"type": "string"},
                       "package": {"type": "string"}},
        "required": ["action", "package"],
    },
    "manage_services": {
        "type": "object",
        "properties": {"action": {"type": "string"},
                       "service": {"type": "string"}},
        "required": ["action", "service"],
    },
}


def _entry(turns=None, tclass="imperative-dispatch"):
    return {
        "id": "tcs-0001", "category": "package_management",
        "intent": "install a package", "expected_behavior_class": "should-dispatch",
        "turns": turns or [{
            "user": "install btop",
            "gold": {"tool_call": {
                "name": "manage_packages",
                "arguments": {"package": "btop", "action": "install",
                              "source_of_request": "user_direct"}}},
        }],
        "provenance": {"generator": "g", "lens": "l", "grounding": ["k"],
                       "method": "m"},
        "training_provenance": {"class": tclass, "origin": "authored"},
    }


def _flow_entry():
    return {
        "id": "tcs-0002", "category": "service_management",
        "intent": "approval flow", "expected_behavior_class": "should-dispatch",
        "turns": [{
            "user": "restart cups",
            "tool_flow": {
                "tool_call": {"name": "manage_services",
                              "arguments": {"service": "cups", "action": "restart",
                                            "source_of_request": "user_direct"}},
                "tool_result": "Restarted cups: active (running)",
                "success": True, "executed": True,
            },
            "gold": {"content": "Done — cups is back up."},
        }],
        "provenance": {"generator": "g", "lens": "l", "grounding": ["k"],
                       "method": "m"},
        "training_provenance": {"class": "class2-approval-flow",
                                "origin": "authored"},
    }


def _emit_one(obj):
    sft.validate_training_entry(obj, locator="<t>", tool_schemas=_SCHEMAS)
    return sft.entry_to_sample(obj, system_prompt=_SYSTEM)


def _calls(sample):
    """Every tool call in an emitted sample, in order."""
    out = []
    for m in sample["messages"]:
        for c in (m.get("tool_calls") or []):
            out.append(c)
    return out


class DispatchGoldShape(unittest.TestCase):
    def test_dispatch_gold_emits_a_structured_call_with_mapping_arguments(self):
        s = _emit_one(_entry())
        asst = s["messages"][2]
        self.assertEqual(asst["role"], "assistant")
        self.assertIsNone(asst["content"],
                          "a dispatch gold's answer IS the call; content must be null")
        calls = asst["tool_calls"]
        self.assertEqual(len(calls), 1)
        fn = calls[0]["function"]
        self.assertEqual(fn["name"], "manage_packages")
        self.assertIsInstance(fn["arguments"], dict,
                              "arguments must be a MAPPING — the template emits "
                              "<parameter=…> blocks only for a mapping")
        self.assertEqual(fn["arguments"]["package"], "btop")
        self.assertEqual(fn["arguments"]["action"], "install")
        self.assertEqual(fn["arguments"]["source_of_request"], "user_direct")

    def test_dispatch_gold_never_writes_a_tool_call_blob_into_content(self):
        """The defect shape, asserted ABSENT rather than merely not looked for."""
        s = _emit_one(_entry())
        blob = json.dumps(s, ensure_ascii=False)
        for m in s["messages"]:
            c = m.get("content")
            if isinstance(c, str):
                self.assertNotIn("<tool_call>", c,
                                 "no message may carry literal <tool_call> text")
                self.assertNotIn("<function=", c)
        # and the whole sample carries no hand-built XML anywhere
        self.assertNotIn("<tool_call>", blob)

    def test_emitted_argument_keys_are_sorted_so_the_bytes_are_deterministic(self):
        """The old renderer pinned determinism with sort_keys; the mapping form
        keeps that property by sorting the mapping itself, so two emissions of
        the same gold are byte-identical."""
        a = _emit_one(_entry())
        b = _emit_one(_entry())
        self.assertEqual(json.dumps(a, ensure_ascii=False),
                         json.dumps(b, ensure_ascii=False))
        args = a["messages"][2]["tool_calls"][0]["function"]["arguments"]
        self.assertEqual(list(args), sorted(args),
                         "argument keys must be emitted in sorted order")


class ToolFlowHistoryShape(unittest.TestCase):
    def test_history_call_carries_mapping_arguments_not_a_json_string(self):
        s = _emit_one(_flow_entry())
        fn = s["messages"][2]["tool_calls"][0]["function"]
        self.assertNotIsInstance(
            fn["arguments"], str,
            "a JSON-string arguments value renders the call with every argument "
            "dropped — the exact shape this emitter must never produce")
        self.assertIsInstance(fn["arguments"], dict)
        self.assertEqual(fn["arguments"]["service"], "cups")
        self.assertEqual(list(fn["arguments"]), sorted(fn["arguments"]))

    def test_both_turn_shapes_agree_on_the_call_form(self):
        """A dispatch gold and a tool_flow history call must be the SAME shape;
        they were not, and that divergence is how one of them went unnoticed."""
        d = _calls(_emit_one(_entry()))[0]
        h = _calls(_emit_one(_flow_entry()))[0]
        self.assertEqual(sorted(d), sorted(h))
        self.assertEqual(sorted(d["function"]), sorted(h["function"]))
        self.assertIsInstance(d["function"]["arguments"], dict)
        self.assertIsInstance(h["function"]["arguments"], dict)


class TheGateCanActuallyFail(unittest.TestCase):
    """Three negative controls. A gate never shown to reject is not a gate —
    and an aggregate check that passes on the defect it exists to catch is the
    cautionary tale this cut was written from."""

    def test_gate_refuses_json_string_arguments(self):
        s = _emit_one(_entry())
        fn = s["messages"][2]["tool_calls"][0]["function"]
        fn["arguments"] = json.dumps(fn["arguments"], sort_keys=True)
        with self.assertRaisesRegex(CorpusError, "mapping"):
            sft.assert_renderable_tool_calls(s, locator="<neg>")

    def test_gate_refuses_literal_tool_call_text_in_content(self):
        s = _emit_one(_entry())
        s["messages"][2]["content"] = (
            '<tool_call>\n{"arguments": {"package": "btop"}, '
            '"name": "manage_packages"}\n</tool_call>')
        with self.assertRaisesRegex(CorpusError, "<tool_call>"):
            sft.assert_renderable_tool_calls(s, locator="<neg>")

    def test_gate_refuses_an_empty_arguments_mapping(self):
        """An empty mapping is a mapping, and it still renders NO <parameter=>
        blocks — so 'is a mapping' alone is not the property that matters. The
        gate requires the arguments to be non-empty for exactly that reason."""
        s = _emit_one(_entry())
        s["messages"][2]["tool_calls"][0]["function"]["arguments"] = {}
        with self.assertRaisesRegex(CorpusError, "no arguments"):
            sft.assert_renderable_tool_calls(s, locator="<neg>")

    def test_gate_passes_the_real_emission(self):
        """The positive control: the same gate must accept what the emitter
        actually produces, or the three refusals above prove nothing."""
        sft.assert_renderable_tool_calls(_emit_one(_entry()), locator="<pos>")
        sft.assert_renderable_tool_calls(_emit_one(_flow_entry()), locator="<pos>")

    def test_the_gate_runs_on_every_emitted_sample_not_only_when_called(self):
        """entry_to_sample itself must be gated; a check a caller has to
        remember is the convention this replaces."""
        import unittest.mock as mock
        with mock.patch.object(sft, "render_gold_message",
                               return_value={"role": "assistant",
                                             "content": "<tool_call>\n{}\n</tool_call>"}):
            with self.assertRaises(CorpusError):
                _emit_one(_entry())


def template_source():
    """(path, text) of the shipped chat template, wherever it lives.

    Module-level so the repo-side render proof can import the FUNCTION rather
    than the TestCase — importing the class made unittest collect and re-run its
    cases in both files, which inflates counts and hides which file proved what.

    Fails loudly rather than skipping: a test that asserts the template contract
    has nothing to say if it cannot find the template.
    """
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "data" / "internvl-tool-template.jinja",   # repo tree
        here.parents[1] / "data" / "internvl-tool-template.jinja",
        Path("/usr/share/intergen/internvl-tool-template.jinja"),     # installed
    ]
    for p in candidates:
        if p.is_file():
            return p, p.read_text(encoding="utf-8")
    raise AssertionError(
        "internvl-tool-template.jinja not found in any known location: "
        + ", ".join(str(c) for c in candidates))


class TemplateContract(unittest.TestCase):
    """Pin the template guard the whole contract rests on.

    This is the cheap, dependency-free half of the reality check: if upstream
    ever changes how the template decides to emit parameters, the emitter's
    mapping-form output stops being the right answer and this fails loudly
    instead of the corpus quietly training the wrong text.
    """

    @staticmethod
    def _template_text():
        return template_source()

    def test_template_still_emits_parameters_only_for_a_mapping(self):
        path, text = self._template_text()
        self.assertIn("{%- if tool_call.arguments is mapping %}", text,
                      f"{path}: the mapping guard this emitter targets is gone; "
                      "re-derive the required argument form before trusting the "
                      "emitted corpus")

    def test_template_still_writes_the_function_and_parameter_blocks(self):
        _path, text = self._template_text()
        self.assertIn("'<tool_call>\\n<function=' + tool_call.name + '>\\n'", text)
        self.assertIn("'<parameter=' + args_name + '>\\n'", text)


class DistributionReport(unittest.TestCase):
    def test_report_separates_a_trained_dispatch_from_a_history_call(self):
        """Both now carry content None + tool_calls, so the report can no longer
        tell them apart by content shape. A history call is the one followed by
        a tool-role message."""
        d = _emit_one(_entry())
        h = _emit_one(_flow_entry())
        report = sft.distribution_report([d, h])
        self.assertIn("dispatch 1 /", report)
        self.assertIn("dispatch-history 1", report)
        self.assertIn("class imperative-dispatch: 1", report)
        self.assertIn("class class2-approval-flow: 1", report)


if __name__ == "__main__":
    unittest.main()
