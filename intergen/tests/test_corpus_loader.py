# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Tests for the demand-corpus loader (M8-6).

Deterministic, daemon-free: the loader is pure JSONL -> Conversation mapping with
fail-closed schema validation. Schema contract:
intergen/tests/demand_corpus/README.md.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from intergen.tests.corpus_loader import (
    CorpusError, entry_to_conversation, iter_corpus_records, load_corpus,
    validate_entry,
)


def _entry(**over) -> dict:
    """A minimal valid single-turn entry; override any field."""
    base = {
        "id": "dd-web-0001",
        "category": "web_search",
        "intent": "look up current weather",
        "turns": [{"user": "what's the weather in Chicago today"}],
        "expected_behavior_class": "should-dispatch",
        "provenance": {
            "generator": "demand",
            "lens": "demand-distribution",
            "grounding": ["openai-howpeopleuse-2025"],
            "method": "internet-grounded-authored",
        },
    }
    base.update(over)
    return base


def _write_jsonl(records: list[dict]) -> Path:
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8")
    for r in records:
        tmp.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.close()
    return Path(tmp.name)


class ValidateEntryTest(unittest.TestCase):
    def test_valid_entry_passes(self):
        validate_entry(_entry())  # no raise

    def test_missing_id_fails(self):
        e = _entry()
        del e["id"]
        with self.assertRaises(CorpusError) as cm:
            validate_entry(e, locator="f:1")
        self.assertIn("f:1", str(cm.exception))
        self.assertIn("id", str(cm.exception))

    def test_empty_turns_fails(self):
        with self.assertRaises(CorpusError):
            validate_entry(_entry(turns=[]))

    def test_turn_without_user_fails(self):
        with self.assertRaises(CorpusError):
            validate_entry(_entry(turns=[{"assertions": []}]))

    def test_empty_string_user_allowed(self):
        # The empty-input edge cell is a legitimate ask to flex.
        validate_entry(_entry(turns=[{"user": ""}]))

    def test_bad_behavior_class_fails(self):
        with self.assertRaises(CorpusError):
            validate_entry(_entry(expected_behavior_class="do-something-vague"))

    def test_null_behavior_class_allowed(self):
        validate_entry(_entry(expected_behavior_class=None))
        e = _entry()
        del e["expected_behavior_class"]
        validate_entry(e)

    def test_missing_provenance_fails(self):
        e = _entry()
        del e["provenance"]
        with self.assertRaises(CorpusError):
            validate_entry(e)

    def test_empty_grounding_fails(self):
        e = _entry()
        e["provenance"]["grounding"] = []
        with self.assertRaises(CorpusError):
            validate_entry(e)

    def test_unregistered_grounding_key_fails(self):
        with self.assertRaises(CorpusError) as cm:
            validate_entry(_entry(), known_grounding_keys={"some-other-key"})
        self.assertIn("not registered", str(cm.exception))

    def test_registered_grounding_key_passes(self):
        validate_entry(_entry(),
                       known_grounding_keys={"openai-howpeopleuse-2025"})

    def test_malformed_assertion_fails(self):
        e = _entry(turns=[{"user": "hi", "assertions": [{"value": "x"}]}])
        with self.assertRaises(CorpusError):
            validate_entry(e)


class EntryToConversationTest(unittest.TestCase):
    def test_single_turn_not_persistent(self):
        conv = entry_to_conversation(_entry())
        self.assertEqual(conv.id, "dd-web-0001")
        self.assertEqual(conv.category, "web_search")
        self.assertEqual(len(conv.turns), 1)
        self.assertFalse(conv.persist_state)
        self.assertEqual(conv.expected_behavior_class, "should-dispatch")

    def test_multi_turn_is_persistent(self):
        e = _entry(
            id="dd-script-0001",
            category="do_for_me",
            turns=[
                {"user": "write me a script that lists my biggest files"},
                {"user": "yes, save it"},
                {"user": "now run it"},
            ],
            expected_behavior_class="should-gate",
        )
        conv = entry_to_conversation(e)
        self.assertEqual(len(conv.turns), 3)
        self.assertTrue(conv.persist_state)

    def test_assertions_are_mapped(self):
        e = _entry(turns=[{
            "user": "what's my hostname",
            "assertions": [
                {"type": "contains", "value": "intergenos", "description": "d"},
            ],
        }])
        conv = entry_to_conversation(e)
        self.assertEqual(len(conv.turns[0].assertions), 1)
        self.assertEqual(conv.turns[0].assertions[0].type, "contains")
        self.assertEqual(conv.turns[0].assertions[0].value, "intergenos")

    def test_discovery_entry_has_no_assertions(self):
        conv = entry_to_conversation(_entry())
        self.assertEqual(conv.turns[0].assertions, [])


class LoadCorpusTest(unittest.TestCase):
    def test_load_roundtrip(self):
        path = _write_jsonl([_entry(), _entry(id="dd-web-0002")])
        convs = load_corpus(path)
        self.assertEqual(len(convs), 2)
        self.assertEqual({c.id for c in convs}, {"dd-web-0001", "dd-web-0002"})

    def test_blank_lines_skipped(self):
        path = _write_jsonl([_entry()])
        # append a blank line
        with path.open("a", encoding="utf-8") as fh:
            fh.write("\n")
        convs = load_corpus(path)
        self.assertEqual(len(convs), 1)

    def test_malformed_json_line_raises_with_locator(self):
        path = _write_jsonl([_entry()])
        with path.open("a", encoding="utf-8") as fh:
            fh.write("{not valid json\n")
        with self.assertRaises(CorpusError) as cm:
            iter_corpus_records(path)
        self.assertIn(":2", str(cm.exception))

    def test_invalid_entry_aborts_load(self):
        bad = _entry(id="dd-web-0003")
        del bad["category"]
        path = _write_jsonl([_entry(), bad])
        with self.assertRaises(CorpusError):
            load_corpus(path)


if __name__ == "__main__":
    unittest.main()
