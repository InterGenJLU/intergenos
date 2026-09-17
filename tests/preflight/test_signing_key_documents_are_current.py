# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""No document may still say the machine owner signing key has no passphrase.

Until 2026-09-17 three documents described the no-passphrase key as a considered
trade, with the reasoning for it. That reasoning was reversed on measurement:
unattended kernel updates and a boot chain that resists root cannot both hold,
and the signing authority is the machine owner's.

A document that still states the retired trade is worse than one that says
nothing, because a reader checks their own machine against it and concludes the
unprotected key they find is the intended design. This gate is a vocabulary
sweep, which is a blunt instrument by nature: it can see a sentence that says
the old thing, and it cannot see a sentence that quietly implies it. The
per-document reading that catches the second kind is in the delivery, not here.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# The documents that described the trade, plus the two the change touches.
DOCUMENTS = [
    "docs/users/secure-boot-and-mok.md",
    "docs/users/security-defaults.md",
    "docs/mok-enrollment.md",
    "docs/release-policy.md",
    "SECURITY.md",
]

# Phrasings that assert the key is unprotected. Each is a sentence shape rather
# than a single word, because "passphrase" appears legitimately all over these
# documents — the disk passphrase is a different thing and is discussed beside
# this one.
RETIRED_CLAIMS = [
    re.compile(r"without a passphrase", re.I),
    re.compile(r"no passphrase on (?:your|the) (?:machine|signing|machine owner)", re.I),
    re.compile(r"stored \*\*without a passphrase\*\*", re.I),
    re.compile(r"why it has no passphrase", re.I),
    re.compile(r"No Passphrase On Your Machine's Signing Key", re.I),
    re.compile(r"a passphrase would mean a person typing it", re.I),
]

# Where the retired wording may still appear: a sentence that says the old state
# is what a machine installed BEFORE the change has, or what the change replaced.
# Recognised by the presence of a marker in the same paragraph, never by a
# hand-listed line number, which would go stale at the next edit.
HISTORY_MARKERS = (
    "before 2026-09",
    "until 2026-09",
    "previously",
    "earlier releases",
    "installed before",
    "this replaced",
    "what changed",
)


def paragraphs(text):
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


@pytest.mark.parametrize("relative", DOCUMENTS)
def test_no_document_asserts_the_key_is_unprotected(relative):
    path = REPO / relative
    if not path.is_file():
        pytest.skip(f"{relative} is not in this tree")
    offenders = []
    for para in paragraphs(path.read_text(encoding="utf-8")):
        lowered = para.lower()
        if any(marker in lowered for marker in HISTORY_MARKERS):
            continue        # describing the previous state as previous
        for pattern in RETIRED_CLAIMS:
            if pattern.search(para):
                offenders.append((pattern.pattern, para[:160]))
    assert not offenders, (
        f"{relative} still states the retired no-passphrase trade as current:\n"
        + "\n".join(f"  [{pat}] {snippet}" for pat, snippet in offenders))


def test_the_sweep_can_actually_detect_the_claim():
    """Positive control. A sweep never shown to fire cannot certify a zero."""
    sample = ("The private half is mok.key: an RSA-2048 key, readable only by "
              "the administrator, and stored without a passphrase.")
    assert any(p.search(sample) for p in RETIRED_CLAIMS)
    assert not any(marker in sample.lower() for marker in HISTORY_MARKERS)


def test_the_user_documents_say_the_passphrase_is_asked_for():
    """The replacement claim has to be present, not merely the old one absent."""
    for relative in ("docs/users/secure-boot-and-mok.md",
                     "docs/users/security-defaults.md",
                     "docs/mok-enrollment.md"):
        text = (REPO / relative).read_text(encoding="utf-8").lower()
        assert "passphrase" in text, relative
        assert ("asks" in text or "asked" in text), (
            f"{relative} does not say the passphrase is asked for")


def test_the_documents_describe_the_migration():
    """A reader on an older machine needs to know what will happen to them."""
    for relative in ("docs/users/secure-boot-and-mok.md", "docs/mok-enrollment.md"):
        text = (REPO / relative).read_text(encoding="utf-8").lower()
        assert "installed before" in text, (
            f"{relative} does not tell a reader with an older machine what "
            f"happens to the key they already have")


def test_the_documents_say_what_happens_when_it_is_refused():
    for relative in ("docs/users/secure-boot-and-mok.md", "docs/mok-enrollment.md"):
        text = (REPO / relative).read_text(encoding="utf-8").lower()
        # The exact words the hook itself prints, so a person who sees the
        # message can search the documents for it and find this paragraph.
        # "refus" alone was too weak: it already appeared in both documents
        # for unrelated reasons and the test passed before anything was
        # written.
        assert "not bootable until signed" in text, (
            f"{relative} does not say, in the words the machine itself uses, "
            f"what happens when the passphrase is not given")
