# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""RED-provable, daemon-free tests for the training-set emitter
(corpus_to_sft.py).

The emitter's job is to refuse a malformed training bank loudly and to render
a valid one deterministically. Each refusal here is a defect class that would
otherwise reach the trainer silently: a turn with no completion, a dispatch
gold the runtime would reject (no source_of_request), an argument the tool's
real schema does not declare, an empty system prompt.

Tool-schema validation uses the REAL registry discovery in one pinning test
and a small explicit schema dict everywhere else, so most cases run in
microseconds while the one that matters proves the real surface.
"""

from __future__ import annotations

import json
import unittest

from intergen.tests.corpus_loader import CorpusError
from intergen.tests.corpus_to_sft import (
    distribution_report, emit, entry_to_sample, load_tool_schemas,
    render_gold_message, validate_training_entry,
)

_SCHEMAS = {
    "manage_packages": {
        "type": "object",
        "properties": {"action": {}, "package": {}, "query": {}},
        "required": ["action"],
    },
}

_SYSTEM = "You are InterGen."


def _entry(**overrides) -> dict:
    base = {
        "id": "t-imperative-001",
        "category": "tool-action",
        "intent": "install imperative dispatches",
        "provenance": {"generator": "authored", "lens": "training-set",
                       "grounding": ["round1-triage"], "method": "hand"},
        "training_provenance": {"class": "imperative-dispatch",
                                "origin": "authored"},
        "turns": [{
            "user": "Install ncdu",
            "gold": {"tool_call": {"name": "manage_packages", "arguments": {
                "action": "install", "package": "ncdu",
                "source_of_request": "user_direct"}}},
        }],
    }
    base.update(overrides)
    return base


def _validate(obj):
    validate_training_entry(obj, locator="<t>", tool_schemas=_SCHEMAS)


class GoldValidation(unittest.TestCase):
    def test_valid_dispatch_entry_passes(self):
        _validate(_entry())

    def test_turn_without_gold_is_refused(self):
        e = _entry()
        del e["turns"][0]["gold"]
        with self.assertRaisesRegex(CorpusError, "gold"):
            _validate(e)

    def test_gold_with_both_shapes_is_refused(self):
        e = _entry()
        e["turns"][0]["gold"]["content"] = "also prose"
        with self.assertRaisesRegex(CorpusError, "exactly one"):
            _validate(e)

    def test_unknown_tool_is_refused_naming_the_known_set(self):
        e = _entry()
        e["turns"][0]["gold"]["tool_call"]["name"] = "no_such_tool"
        with self.assertRaisesRegex(CorpusError, "no_such_tool.*manage_packages"):
            _validate(e)

    def test_missing_source_of_request_is_refused(self):
        e = _entry()
        del e["turns"][0]["gold"]["tool_call"]["arguments"]["source_of_request"]
        with self.assertRaisesRegex(CorpusError, "source_of_request"):
            _validate(e)

    def test_invalid_source_of_request_is_refused(self):
        e = _entry()
        e["turns"][0]["gold"]["tool_call"]["arguments"][
            "source_of_request"] = "because_i_felt_like_it"
        with self.assertRaisesRegex(CorpusError, "source_of_request"):
            _validate(e)

    def test_undeclared_argument_key_is_refused(self):
        e = _entry()
        e["turns"][0]["gold"]["tool_call"]["arguments"]["flavor"] = "spicy"
        with self.assertRaisesRegex(CorpusError, "flavor"):
            _validate(e)

    def test_missing_required_argument_is_refused(self):
        e = _entry()
        del e["turns"][0]["gold"]["tool_call"]["arguments"]["action"]
        with self.assertRaisesRegex(CorpusError, "action"):
            _validate(e)

    def test_missing_training_provenance_is_refused(self):
        e = _entry()
        del e["training_provenance"]
        with self.assertRaisesRegex(CorpusError, "training_provenance"):
            _validate(e)

    def test_bad_origin_is_refused(self):
        e = _entry()
        e["training_provenance"]["origin"] = "vibes"
        with self.assertRaisesRegex(CorpusError, "origin"):
            _validate(e)

    def test_empty_prose_gold_is_refused(self):
        e = _entry()
        e["turns"][0]["gold"] = {"content": "   "}
        with self.assertRaisesRegex(CorpusError, "non-empty"):
            _validate(e)


class Rendering(unittest.TestCase):
    def test_dispatch_gold_renders_a_structured_call_deterministically(self):
        """Changed 2026-08-13: this used to assert the Hermes block
        ``<tool_call>{json}</tool_call>`` written into the assistant's content.
        That form is not what the chat template renders — it produces no
        ``<function=`` block at all — so the emitter now writes a structured
        call and this test follows it. Shape coverage and the defect shapes
        asserted absent live in test_corpus_to_sft_tool_call_shape.py."""
        gold = _entry()["turns"][0]["gold"]
        msg = render_gold_message(gold)
        self.assertEqual(msg["role"], "assistant")
        self.assertIsNone(msg["content"])
        fn = msg["tool_calls"][0]["function"]
        self.assertEqual(fn["name"], "manage_packages")
        self.assertIsInstance(fn["arguments"], dict)
        self.assertEqual(fn["arguments"]["source_of_request"], "user_direct")
        # sorted argument keys pin determinism: same gold, same bytes.
        self.assertEqual(json.dumps(msg, sort_keys=False),
                         json.dumps(render_gold_message(gold), sort_keys=False))
        self.assertEqual(list(fn["arguments"]), sorted(fn["arguments"]))

    def test_prose_gold_still_renders_as_plain_content(self):
        msg = render_gold_message({"content": "Noted."})
        self.assertEqual(msg, {"role": "assistant", "content": "Noted."})
        self.assertNotIn("tool_calls", msg)

    def test_sample_carries_system_then_alternating_turns(self):
        e = _entry(turns=[
            {"user": "My editor is vim",
             "gold": {"content": "Noted — want me to remember that?"}},
            {"user": "Yes, please",
             "gold": {"content": "Done — I'll remember that your editor is vim."}},
        ])
        s = entry_to_sample(e, system_prompt=_SYSTEM)
        roles = [m["role"] for m in s["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant",
                                 "user", "assistant"])
        self.assertEqual(s["messages"][0]["content"], _SYSTEM)
        self.assertEqual(s["provenance"]["training"]["class"],
                         "imperative-dispatch")

    def test_empty_system_prompt_is_refused(self):
        with self.assertRaisesRegex(CorpusError, "system prompt"):
            emit([], system_prompt="  ", tool_schemas=_SCHEMAS)


class EmitAndReport(unittest.TestCase):
    def test_emit_reads_a_bank_and_reports_distribution(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl",
                                         delete=False) as fh:
            fh.write(json.dumps(_entry()) + "\n")
            fh.write(json.dumps(_entry(
                id="t-informational-001",
                intent="informational twin does not dispatch",
                training_provenance={"class": "imperative-dispatch",
                                     "origin": "authored"},
                turns=[{"user": "how would I get ncdu",
                        "gold": {"content":
                                 "You could install it with pkm: pkm install "
                                 "ncdu. Want me to run that?"}}],
            )) + "\n")
            bank = fh.name
        samples = emit([bank], system_prompt=_SYSTEM, tool_schemas=_SCHEMAS)
        self.assertEqual(len(samples), 2)
        report = distribution_report(samples)
        self.assertIn("dispatch 1 / prose 1", report)
        self.assertIn("class imperative-dispatch: 2", report)

    def test_empty_bank_is_refused(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl",
                                         delete=False) as fh:
            bank = fh.name
        with self.assertRaisesRegex(CorpusError, "empty"):
            emit([bank], system_prompt=_SYSTEM, tool_schemas=_SCHEMAS)

    def test_first_invalid_entry_aborts_with_its_locator(self):
        import tempfile
        bad = _entry()
        del bad["turns"][0]["gold"]
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl",
                                         delete=False) as fh:
            fh.write(json.dumps(_entry()) + "\n")
            fh.write(json.dumps(bad) + "\n")
            bank = fh.name
        with self.assertRaisesRegex(CorpusError, "entry#2"):
            emit([bank], system_prompt=_SYSTEM, tool_schemas=_SCHEMAS)


class RealRegistrySurface(unittest.TestCase):
    """The one case that proves the REAL discovery surface (no shadow list)."""

    def test_real_discovery_knows_manage_packages_and_gates_a_real_entry(self):
        schemas = load_tool_schemas()
        self.assertIn("manage_packages", schemas)
        self.assertIn("action",
                      schemas["manage_packages"].get("properties", {}))
        # A valid entry validates against the REAL schemas end-to-end.
        validate_training_entry(_entry(), locator="<real>",
                                tool_schemas=schemas)


if __name__ == "__main__":
    unittest.main()
