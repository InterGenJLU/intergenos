# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Exercise generated text and metadata through the library and JSONL CLI."""

import copy
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

from intergen.tests import phrasing_generator as generator


@pytest.fixture
def grammar():
    return {
        "class": "fixture-colours",
        "expected_answerer": "fixture-reader",
        "clauses": [
            {"alternatives": ["{verb} the {colour} tile", "inspect the {colour} tile"]},
            {"alternatives": ["on the table", "beside the box"], "optional": True},
        ],
        "synonyms": {"verb": ["show", "describe"], "colour": ["blue", "green"]},
        "forms": {
            "negation": ["do not {text}"],
            "compound": ["{text}; tell me a story"],
            "follow_up": ["what about that tile?"],
        },
        "answerers": {"negation": "fixture-refusal", "compound": "fixture-compound"},
    }


def test_seed_is_repeatable_without_changing_global_random_state(grammar):
    before = random.getstate()
    one = generator.generate(grammar, seed=17, cap=20)
    assert generator.generate(grammar, seed=17, cap=20) == one
    assert generator.generate(grammar, seed=18, cap=20) != one
    assert random.getstate() == before


@pytest.mark.parametrize("cap", [0, 1, 7, 19])
def test_cap_counts_output_records_including_variants(grammar, cap):
    assert len(generator.generate(grammar, seed=2, cap=cap)) == cap


def test_all_operators_change_the_text_and_keep_the_contract(grammar):
    rows = generator.generate(grammar, seed=3, cap=1000)
    assert {row["operator"] for row in rows} == set(generator.OPERATORS)
    for row in rows:
        assert row["class"] == "fixture-colours"
        assert row["expected_answerer"] == grammar["answerers"].get(
            row["operator"], "fixture-reader")
        assert row["text"]
        assert row["seed"] == 3
        assert isinstance(row["base_index"], int)
        original = row["source_text"]
        if row["operator"] == "base":
            assert row["text"] == original
        else:
            assert row["text"] != original
        if row["operator"] == "typo_drop":
            assert len(original) == len(row["text"]) + 1
            assert any(original[:i] + original[i+1:] == row["text"]
                       for i, char in enumerate(original) if char.isalpha())
        if row["operator"] == "typo_transpose":
            changed = [i for i, (a, b) in enumerate(zip(original, row["text"])) if a != b]
            assert len(changed) == 2 and changed[1] == changed[0] + 1
            i = changed[0]
            assert row["text"][i:i+2] == original[i:i+2][::-1]
        if row["operator"] == "ellipsis":
            assert row["text"] in row["source_clauses"]
        if row["operator"] == "follow_up":
            assert row["context"] == [original]
        else:
            assert row["context"] == []


def test_alternatives_synonyms_and_optional_parts_are_expanded(grammar):
    rows = generator.generate(grammar, seed=0, cap=1000, operators=["base"])
    texts = {row["text"] for row in rows}
    assert "show the blue tile" in texts
    assert "describe the green tile beside the box" in texts
    assert "inspect the blue tile on the table" in texts
    assert all("{" not in text for text in texts)


def test_a_large_grammar_is_bounded_without_materializing_its_product(grammar):
    grammar["clauses"] = [{"alternatives": ["a", "b"]} for _ in range(80)]
    grammar["synonyms"] = {}
    assert len(generator.generate(grammar, seed=9, cap=3)) == 3


def test_nonapplicable_operator_never_claims_an_unchanged_variant(grammar):
    grammar["clauses"] = [{"alternatives": ["a"]}]
    grammar["synonyms"] = {}
    rows = generator.generate(grammar, seed=0, cap=20)
    assert "ellipsis" not in {row["operator"] for row in rows}
    assert "typo_transpose" not in {row["operator"] for row in rows}
    assert all(row["text"] for row in rows)


def test_inapplicable_operator_cannot_scan_an_unbounded_product(grammar):
    grammar["clauses"] = [{"alternatives": ["a"]}]
    grammar["synonyms"] = {f"word{i}": ["a", "b"] for i in range(80)}
    with pytest.raises(ValueError, match="search limit"):
        generator.generate(grammar, seed=0, cap=1, operators=["ellipsis"])


@pytest.mark.parametrize("change", [
    {"clauses": []}, {"clauses": [{"alternatives": []}]},
    {"clauses": [{"alternatives": ["{missing}"]}]},
    {"clauses": [{"alternatives": ["hello"], "optional": True}]},
    {"clauses": [{"alternatives": ["{verb.__class__}"]}]},
    {"class": ""}, {"synonyms": {"verb": []}},
    {"forms": {"negation": ["no"]}}, {"unknown": True},
    {"answerers": {"imaginary": "reader"}},
])
def test_invalid_grammar_is_an_error(grammar, change):
    grammar.update(change)
    with pytest.raises(ValueError):
        generator.generate(grammar, seed=0, cap=1)


@pytest.mark.parametrize("kwargs", [
    {"cap": -1}, {"cap": True}, {"seed": True},
    {"operators": []}, {"operators": ["unknown"]},
    {"operators": ["base", "base"]},
])
def test_invalid_generation_options_are_an_error(grammar, kwargs):
    options = {"seed": 0, "cap": 3, **kwargs}
    with pytest.raises(ValueError):
        generator.generate(grammar, **options)


def test_library_leaves_the_grammar_unchanged(grammar):
    original = copy.deepcopy(grammar)
    generator.generate(grammar, seed=10, cap=30)
    assert grammar == original


def test_cli_jsonl_is_the_same_data_in_a_separate_process(grammar, tmp_path):
    path = tmp_path / "grammar.json"
    path.write_text(json.dumps(grammar))
    argv = [sys.executable, str(Path(generator.__file__)), str(path),
            "--seed", "17", "--cap", "13"]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert [json.loads(line) for line in result.stdout.splitlines()] == (
        generator.generate(grammar, seed=17, cap=13))


def test_cli_rejects_bad_json_without_partial_output(tmp_path):
    path = tmp_path / "grammar.json"
    path.write_text("{invalid")
    result = subprocess.run([sys.executable, generator.__file__, str(path),
                             "--seed", "0", "--cap", "2"],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "ERROR" in result.stderr
