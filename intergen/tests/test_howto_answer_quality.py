# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Every curated answer stands alone and contains user-facing prose."""

import json
import re

import pytest

from intergen.howto import HowtoCorpus
from intergen import safety
from intergen.tests.howto_content_helpers import DATA


def raw_entries():
    result = []
    for path in sorted(DATA.glob("*.json")):
        rows = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(rows, list), path.name
        result.extend((path.name, item) for item in rows)
    assert result, "the shipped corpus must not be empty"
    return result


_DANGLING = re.compile(
    r"\b(?:next|previous|following|preceding)\s+(?:answer|response|entry)\b"
    r"|\b(?:answer|question|case|response|entry)\s+(?:above|below)\b"
    r"|\bsee\s+[\"“](?:what|how|why|where|which|can|is)\b[^\"”]+[\"”]\s+for\b",
    re.I,
)
_AUTHOR = re.compile(
    r"\bhonest answer\s+(?:to\s+)?give\s+the\s+user\b"
    r"|\bhonest answer\s*:\s*[\"“]"
    r"|\b(?:tell|give|show|instruct)\s+the\s+user\b"
    r"|\b(?:do\s+not|don['’]t)\s+suggest\b"
    r"|\bverify\s+before\s+(?:this\s+)?ships\b",
    re.I,
)


def prose_findings(rows, pattern):
    return [
        f"{filename}:{item['id']}: {match.group(0)}"
        for filename, item in rows
        if (match := pattern.search(" ".join(item["answer"].split())))
    ]


def assert_no_dangling_references(rows):
    findings = prose_findings(rows, _DANGLING)
    assert not findings, "cross-answer references: " + "; ".join(findings)


def assert_no_author_instructions(rows):
    findings = prose_findings(rows, _AUTHOR)
    assert not findings, "author instructions: " + "; ".join(findings)


def test_all_curated_answers_are_standalone():
    assert_no_dangling_references(raw_entries())


def test_all_curated_answers_address_the_user_not_the_author():
    assert_no_author_instructions(raw_entries())


@pytest.mark.parametrize("text", [
    "See the next answer.",
    "Refer to the preceding response.",
    "The details are in the case below.",
    'See "how do I add a new user" for the steps.',
    "Read the following entry.",
])
def test_standalone_check_detects_a_planted_reference(text):
    with pytest.raises(AssertionError, match="cross-answer references"):
        assert_no_dangling_references([("example.json", {"id": "example", "answer": text})])


@pytest.mark.parametrize("text", [
    'Honest answer to give the user: "Install it."',
    "Tell the user to install the package.",
    "Do NOT suggest a different tool.",
    "Check the configuration and verify before this ships.",
    'Honest answer: "The package is available."',
])
def test_author_check_detects_a_planted_instruction(text):
    with pytest.raises(AssertionError, match="author instructions"):
        assert_no_author_instructions([("example.json", {"id": "example", "answer": text})])


def test_normal_instructions_to_the_person_are_not_author_notes():
    rows = [("example.json", {
        "id": "example",
        "answer": "Verify the package before using it. Read `man chmod` for details.",
    })]
    assert_no_dangling_references(rows)
    assert_no_author_instructions(rows)


def test_the_production_loader_preserves_every_entry_and_its_schema(caplog):
    rows = raw_entries()
    corpus = HowtoCorpus(data_dir=DATA)
    by_id = {item.id: item for item in corpus._entries}
    assert len(by_id) == corpus.entry_count == len(rows)
    assert not [r for r in caplog.records if r.name == "intergen.howto" and r.levelno >= 30]
    for filename, raw in rows:
        for field in ("id", "domain", "answer", "doc_source"):
            assert isinstance(raw[field], str) and raw[field].strip(), (filename, field)
        assert isinstance(raw["triggers"], list) and raw["triggers"]
        assert all(isinstance(q, str) and q.strip() for q in raw["triggers"])
        assert isinstance(raw.get("requires", []), list)
        assert all(isinstance(name, str) for name in raw.get("requires", []))
        parsed = by_id[raw["id"]]
        assert parsed.answer == raw["answer"]
        assert parsed.triggers == tuple(raw["triggers"])
        assert parsed.requires == tuple(raw.get("requires", []))
        if "action" in raw:
            assert set(raw["action"]) == {"command", "tool"}
            assert all(isinstance(value, str) and value for value in raw["action"].values())
            assert parsed.action.command == raw["action"]["command"]
            assert parsed.action.tool == raw["action"]["tool"]


def test_curated_first_party_commands_pass_the_real_capability_screen(monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(safety, "_CAP_SURFACE_PATH", DATA.parent / "capability-surface.json")
        safety.reset_surface_cache()
        try:
            for filename, item in raw_entries():
                verdict, marker = safety.screen_capability_claim(item["answer"])
                assert verdict == "clean", (filename, item["id"], verdict, marker)
        finally:
            safety.reset_surface_cache()
