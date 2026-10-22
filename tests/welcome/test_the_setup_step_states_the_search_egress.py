#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The Welcomer's InterGen setup step says what leaves the machine.

The step that offers to set InterGen up said "The model runs entirely on your
machine; no conversation data ever leaves your computer." A web search sends
its search words to the search service and then fetches the first result's
page, and a question goes to a cloud provider once the person sets one up, so
the sentence denied an egress the code performs. It now says the model runs on
the machine and sends nothing in the background, names the search and its page
fetch as happening on the person's word, the network check and the IP answer's
resolver query with the conditions they run under, and the cloud provider's
condition, as the InterGen toggle's disclosure does.

The label is read from the Welcomer's source as Python reads it, its literal
parts joined and the size it formats in from the offer marked "{}", without
importing GTK.
"""
import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WELCOME = REPO_ROOT / "assets/intergen-welcome/intergen-welcome.py"


def _setup_description() -> str:
    """The literal text of the one label assigned to ``setup_desc``."""
    tree = ast.parse(WELCOME.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "setup_desc"
                and isinstance(node.value, ast.Call)):
            for keyword in node.value.keywords:
                if keyword.arg != "label":
                    continue
                value = keyword.value
                if isinstance(value, ast.JoinedStr):
                    found.append("".join(
                        part.value if isinstance(part, ast.Constant) else "{}"
                        for part in value.values))
                elif isinstance(value, ast.Constant) and isinstance(value.value, str):
                    found.append(value.value)
    assert len(found) == 1, f"expected one setup description, found {found}"
    return found[0]


def test_the_step_no_longer_denies_the_egress():
    text = _setup_description().lower()
    assert "no conversation data ever leaves" not in text
    assert "runs entirely on" not in text


def test_the_step_names_the_search_its_page_fetch_and_the_cloud_condition():
    text = _setup_description()
    assert "The model runs on your machine and sends nothing in the background" in text
    assert "a web search you ask for, or accept when it is offered" in text
    assert "sends its search words to the search service" in text
    assert "fetches the first result's page" in text
    assert "a question goes to a cloud provider only if you set one up" in text


def test_the_step_names_the_network_check_and_the_ip_lookup():
    text = _setup_description()
    assert "A network check such as ping or dig reaches the host it names" in text
    assert ("runs without asking only when you ask for it directly and nothing "
            "from outside has been read in the conversation; otherwise the "
            "assistant asks you first") in text
    assert ("Your IP address is looked up at a public DNS resolver only when "
            "you ask for it") in text


def test_the_rest_of_the_step_stays():
    text = _setup_description()
    assert text.startswith("One click downloads the local AI model that fits your hardware ({}")
    assert "no terminal needed" in text
    assert text.endswith("the model's license is shown as it installs.")
