#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The installer's opt-in for the assistant says what leaves the machine.

Both installers, the graphical Packages screen and the text walk-through,
ended their description of the InterGen opt-in with "No data leaves your
machine." A web search sends its search words to the search service and then
fetches the first result's page, and a question goes to a cloud provider once
the person sets one up, so the sentence denied an egress the code performs.
Each now says the assistant runs on the machine and sends nothing in the
background, names the search and its page fetch as happening on the person's
word, and names the cloud provider's condition, as the InterGen toggle's
disclosure does.

The texts are read from the installers' sources as Python reads them (adjacent
string literals joined), without importing GTK or running a walk-through.
"""
import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUI = REPO_ROOT / "installer/frontend/gui/screens/packages.py"
TUI = REPO_ROOT / "installer/frontend/tui.py"

_SAYS = (
    "sends nothing in the background",
    "a web search you ask for, or accept when it is offered",
    "sends its search words to the search service",
    "fetches the first result's page",
    "a question goes to a cloud provider only if you set one up",
)


def _strings(path: Path) -> list[ast.AST]:
    return list(ast.walk(ast.parse(path.read_text(encoding="utf-8"))))


def _gui_subtitle() -> str:
    """The subtitle set on the InterGen switch row of the Packages screen."""
    found = []
    for node in _strings(GUI):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "set_subtitle"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "_intergen_switch"
                and node.args and isinstance(node.args[0], ast.Constant)):
            found.append(node.args[0].value)
    assert len(found) == 1, f"expected one InterGen subtitle, found {found}"
    return found[0]


def _tui_prompt() -> str:
    """The text walk-through's question for the InterGen opt-in."""
    found = [node.value for node in _strings(TUI)
             if isinstance(node, ast.Constant) and isinstance(node.value, str)
             and node.value.startswith("Enable the InterGen AI assistant? (default: NO)")]
    assert len(found) == 1, f"expected one InterGen prompt, found {found}"
    return found[0]


def test_neither_installer_denies_the_egress():
    for text in (_gui_subtitle(), _tui_prompt()):
        assert "No data leaves your machine" not in text


def test_each_installer_names_the_search_its_page_fetch_and_the_cloud_condition():
    for text in (_gui_subtitle(), _tui_prompt()):
        for words in _SAYS:
            assert words in text, (words, text)


def test_the_rest_of_each_description_stays():
    gui = _gui_subtitle()
    assert gui.startswith("Starts the InterGen AI assistant at first login.")
    assert gui.endswith("auto-enables the Local AI runtime below (the service "
                        "needs the binaries to run).")
    tui = _tui_prompt()
    assert "`intergen setup`" in tui
    assert "default: NO" in tui
