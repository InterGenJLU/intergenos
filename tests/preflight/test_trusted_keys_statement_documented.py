# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The documents describe the first-login page as it actually behaves now.

The user documentation lists what the Welcomer's first page says about Secure
Boot keys. Before this change that list described one card, shown only when a
retirement somebody asked for did not happen. The page now states on EVERY
machine which machine owner certificates the firmware trusts, and the install
writes a world-readable record of what was decided about earlier keys, which is
where "you chose to keep this one" comes from.

A document that describes the old behaviour is how a person concludes the
silence on their own machine means there is nothing to see, so this gate reads
the two documents that make the claim and requires the new behaviour and the new
file to be in them.
"""

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

DOCUMENTS = [
    "docs/users/secure-boot-and-mok.md",
    "docs/mok-enrollment.md",
]


@pytest.mark.parametrize("relative", DOCUMENTS)
def test_the_document_says_the_page_always_states_what_is_trusted(relative):
    text = (REPO / relative).read_text(encoding="utf-8")
    assert "every login" in text or "at every login" in text, (
        f"{relative} does not say the first page states what the firmware "
        f"trusts on every machine")
    assert "mokutil --list-enrolled" in text or "certificates the firmware trusts" in text


@pytest.mark.parametrize("relative", DOCUMENTS)
def test_the_document_names_the_decision_record(relative):
    text = (REPO / relative).read_text(encoding="utf-8")
    assert "/etc/intergenos/mok-prior-keys" in text, (
        f"{relative} does not name the record the page reads to tell a key "
        f"somebody kept on purpose from one nobody was asked about")
