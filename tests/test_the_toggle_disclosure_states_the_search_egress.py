#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The InterGen toggle's disclosure says what leaves the machine.

The toggle app's disclosure label said "The AI runs entirely on your machine —
no conversation data leaves your computer." A web search sends its search words
to the search service and then fetches the first result's page, and a question
goes to a cloud provider once the person sets one up, so the sentence denied an
egress the code performs. The label now says the AI runs on the machine and
sends nothing in the background, names the search and its page fetch as
happening on the person's word (asked for, or accepted when offered), and names
the cloud provider's condition; the opt-in sentence and the two terminal
commands stay.

The label is read from the app's source as Python reads it (adjacent string
literals joined), without importing GTK: the window is not opened here.
"""
import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TOGGLE = REPO_ROOT / "packages/desktop/intergen-toggle/assets/intergen-toggle.py"


def _disclosure() -> str:
    """The text of the one label assigned to ``disclosure`` in the app."""
    tree = ast.parse(TOGGLE.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "disclosure"
                and isinstance(node.value, ast.Call)):
            for keyword in node.value.keywords:
                if (keyword.arg == "label"
                        and isinstance(keyword.value, ast.Constant)
                        and isinstance(keyword.value.value, str)):
                    found.append(keyword.value.value)
    assert len(found) == 1, f"expected one disclosure label, found {found}"
    return found[0]


def test_the_disclosure_no_longer_denies_the_egress():
    text = _disclosure().lower()
    assert "no conversation data leaves" not in text
    assert "runs entirely on" not in text


def test_the_disclosure_names_the_search_and_its_page_fetch():
    text = _disclosure()
    assert "A web search you ask for, or accept when it is offered" in text
    assert "sends its search words to the search service" in text
    assert "fetches the first result's page" in text


def test_the_disclosure_names_the_cloud_providers_condition():
    text = _disclosure()
    assert "a question goes to a cloud provider only if you set one up" in text
    assert "sends nothing in the background" in text


def test_the_opt_in_and_the_two_terminal_commands_stay():
    text = _disclosure()
    assert text.startswith("InterGen is opt-in by design.")
    assert "`intergen setup` (enable)" in text
    assert "`systemctl --user disable --now intergen.service` (disable)" in text
