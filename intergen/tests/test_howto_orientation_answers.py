# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Orientation questions retrieve useful answers from the shipped corpus."""

import pytest

from intergen.howto import HowtoCorpus
from intergen.tests.howto_content_helpers import DATA, commands, entry
from intergen.tests.test_router_explain import _repo_explain_router


@pytest.fixture(scope="module")
def corpus():
    return HowtoCorpus(data_dir=DATA, embedder=None)


@pytest.mark.parametrize(("query", "identifier"), [
    ("what is InterGenOS?", "igos-system-overview"),
    ("what version am I on?", "system-what-os-version"),
    ("what's different about this distro?", "igos-system-overview"),
    ("how do I update?", "pkm-update-system"),
    ("where are the logs?", "system-read-logs"),
    ("what was that blue screen at boot?", "igos-mokmanager-screen"),
    ("how do backups work here?", "system-backups"),
    ("how do I open a terminal?", "terminal-run-command"),
    ("put my documents back the way they were yesterday", "system-restore-documents"),
])
def test_orientation_question_retrieves_its_own_subject(corpus, query, identifier):
    result, _ = corpus.retrieve(query)
    assert result is not None, query
    assert result.id == identifier
    assert result.doc_source


def test_boot_screen_names_the_enrollment_fallback_and_black_screen_key():
    item = entry("igos-mokmanager-screen")
    assert "MokManager" in item.answer
    assert "Enroll key from disk" in item.answer
    assert "mok.der" in item.answer
    assert "ESP" in item.answer
    assert "Esc" in item.answer
    assert "black screen" in item.answer.lower()
    assert item.action is None


def test_backup_guidance_names_real_layers_and_checks_capture_health():
    text = entry("system-backups").answer
    assert ["chronicle", "status"] in commands(text)
    assert ["chronicle", "list", "user-data"] in commands(text)
    for layer in ("config-state", "user-data", "restore-point"):
        assert layer in text
    assert "external" in text.lower()
    assert "available" in text.lower()


def test_restore_guidance_selects_a_real_version_and_previews_the_paths():
    item = entry("system-restore-documents")
    examples = commands(item.answer)
    assert ["chronicle", "list", "user-data"] in examples
    assert ["sudo", "chronicle", "contents", "user-data", "<version>"] in examples
    assert [
        "sudo", "chronicle", "restore", "user-data", "<version>",
        "<saved-path>", "--dry-run",
    ] in examples
    assert "yesterday" in item.answer.lower()
    assert "available" in item.answer.lower()
    assert item.action.command == "chronicle list user-data"
    assert item.action.tool == "run_command"
    assert not any("-y" in command or "--yes" in command for command in examples)


def test_terminal_guidance_reaches_the_terminal_without_using_a_shell():
    text = entry("terminal-run-command").answer
    assert "Ctrl+Alt+T" in text
    assert "Super" in text
    assert "search" in text.lower()


def test_version_guidance_keeps_the_release_a_machine_read():
    item = entry("system-what-os-version")
    assert "/etc/os-release" in item.answer
    assert item.action.command == "cat /etc/os-release"


def test_log_guidance_uses_the_system_journal():
    assert ["journalctl", "-b"] in commands(entry("system-read-logs").answer)


def test_restore_request_offers_version_selection_without_restoring_files():
    router = _repo_explain_router()
    result, _ = router._try_explain(
        "put my documents back the way they were yesterday")
    assert result is not None
    assert result.source == "explain"
    assert result.tool_calls == []
    assert router._pending_action_offer[:2] == (
        "chronicle list user-data", "run_command")
    assert "Want me to run" in result.text
