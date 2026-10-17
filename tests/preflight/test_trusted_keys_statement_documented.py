# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The documents do not describe a first-page statement that no longer exists.

The Welcomer's first page used to end with a statement, at every login, of which
machine owner certificates the firmware trusts. That statement is removed, and a
document that still promised it would send a person looking for a page that is
not there, or teach them that its absence on their own machine means something.
This gate reads the two documents that described it and requires that no line
about the first page says it states what the firmware trusts.

What stays documented: the world-readable record the install writes of what was
decided about earlier keys, which the first page still reads for its retirement
advisory, and that advisory itself. The retirement test below also shows the
first check is reading lines about the first page at all, rather than passing
over a document it never matched.
"""

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

DOCUMENTS = [
    "docs/users/secure-boot-and-mok.md",
    "docs/mok-enrollment.md",
]

# How the removed statement was described, matched without regard to case.
CLAIMS = ("every login", "what the firmware trusts", "what the machine trusts",
          "how many certificates")


def _first_page_lines(text):
    """Each line of a document that speaks about the Welcomer's first page."""
    return [line for line in text.splitlines() if "first page" in line.lower()]


def _claims_in(lines):
    return [(claim, line[:120]) for line in lines for claim in CLAIMS
            if claim in line.lower()]


@pytest.mark.parametrize("relative", DOCUMENTS)
def test_no_line_says_the_first_page_states_what_is_trusted(relative):
    text = (REPO / relative).read_text(encoding="utf-8")
    found = _claims_in(_first_page_lines(text))
    assert found == [], (
        f"{relative} still describes the removed first-page statement: {found}")


@pytest.mark.parametrize("relative", DOCUMENTS)
def test_the_document_names_the_decision_record(relative):
    text = (REPO / relative).read_text(encoding="utf-8")
    assert "/etc/intergenos/mok-prior-keys" in text, (
        f"{relative} does not name the record the page reads to tell a key "
        f"somebody kept on purpose from one nobody was asked about")


@pytest.mark.parametrize("relative", DOCUMENTS)
def test_the_retirement_advisory_is_still_described(relative):
    text = (REPO / relative).read_text(encoding="utf-8")
    lines = [line for line in _first_page_lines(text) if "retire" in line.lower()]
    assert lines, (f"{relative} no longer describes the first page's retirement "
                   f"advisory, or the first check is reading nothing")


def test_the_check_finds_a_planted_claim():
    planted = ("- **On every machine, at every login**, the Welcomer's first page "
               "states what the firmware trusts: how many certificates ...")
    found = _claims_in(_first_page_lines("intro\n" + planted + "\nend\n"))
    assert sorted(claim for claim, _ in found) == sorted(
        ["every login", "what the firmware trusts", "how many certificates"])
