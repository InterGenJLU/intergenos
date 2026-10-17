# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""First-party capability and trust claims stay within the documented surface."""

from intergen.tests.howto_content_helpers import answer, commands, entry


def test_signing_and_installed_file_verification_are_distinct_claims():
    item = entry("igos-signing-trust")
    assert ["sudo", "pkm", "verify", "--all"] in commands(item.answer)
    assert item.action.command == "sudo pkm verify --all"
    assert "release" in item.answer.lower()
    assert "existence-only" in item.answer.lower()
    assert "undetermined" in item.answer.lower()
    assert "proves your on-disk files haven't changed" not in item.answer


def test_live_information_answer_acknowledges_explicit_web_search():
    text = answer("igos-live-web-and-realtime-data")
    assert "search the web for" in text.lower()
    assert "provider" in text.lower() and "query" in text.lower()
    assert "local" in text.lower()
    assert "don't browse the web" not in text.lower()
    assert "offline by design" not in text.lower()
