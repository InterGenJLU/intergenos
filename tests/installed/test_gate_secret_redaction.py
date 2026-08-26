"""GATE 13 — secret and redaction fixtures (section 9 line 12).

THE CONTRACT IS DECIDED, AND THIS GATE NOW ENCODES IT. The fixtures below were
authored as the contract QUESTION: five shapes a credential really takes when a
person hands it to a machine, with the decision about which of them a persisted
record must hide left open. Decided 2026-08-25: the record must hide all five. A
credential is hidden by the SHAPE of the value as well as by the name of the
field it arrived under, because a person types a credential into a command far
more often than they place one in a field called ``api_key``. The redaction is
applied to the whole row at the moment of writing, so a field added later is
covered without its author knowing this gate exists.

WHAT COMPOSITION PROPERTY THIS CATCHES. The redaction can only be measured on the
package that is actually installed. A source-tree test would report on the tree
the author is editing; this tier imports the shipped module, so a release that
carries an older redaction than its own tests describe is caught here and nowhere
else.

EXPECTED TO FAIL ON R001.1 AS SHIPPED (release 183), which has no shape predicate
at all: all four non-control fixtures reach the record byte-identical.
"""

from __future__ import annotations

import json

import pytest

# (label, the field name it arrives under, the value, the fragment that must not persist)
FIXTURES = [
    ("a token in a credential-shaped field",
     "api_key", "sk-live-51H8ZqRExampleOnlyNotARealKey", "sk-live-51H8Zq"),
    ("a bearer token inside a shell command",
     "command", "curl -H 'Authorization: Bearer abc123secretvalue' https://example.invalid",
     "abc123secretvalue"),
    ("a password inside a shell command",
     "command", "mysql -u admin -pHunter2Example --host db.example.invalid",
     "Hunter2Example"),
    ("a private key block pasted as content",
     "content",
     "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaEV4YW1wbGVPbmx5\n-----END OPENSSH PRIVATE KEY-----",
     "b3BlbnNzaEV4YW1wbGVPbmx5"),
    ("a cloud access key in a free-text field",
     "user_message", "my key is AKIAIOSFODNN7EXAMPLE, can you set it up",
     "AKIAIOSFODNN7EXAMPLE"),
]


# Command text a person really types that carries NO credential. A redaction
# that cannot be shown to leave these alone is not a redaction, it is a
# shredder, and an unexplained hole in a full-fidelity record is its own
# failure. Each is a near-miss for one of the shapes above.
INNOCENT = [
    ("an ordinary package command",
     "command", "pkm install firefox && systemctl --user restart intergen"),
    ("the -p option with its value in the next argument",
     "command", "mkdir -p /srv/intergen && cp -pr assets backup"),
    ("a published port with no letter in it",
     "command", "docker run -p8080:8080 --name intergen-web image:latest"),
    ("the scheme word followed by an ordinary word",
     "user_message", "bearer authentication is the scheme name, not a secret"),
    ("prose about credentials",
     "user_message", "How do I store a password safely? I keep forgetting mine."),
    ("a plain URL",
     "url", "https://github.com/InterGenJLU/intergenos"),
]


@pytest.fixture(scope="module")
def redact(installed_intergen_dir):
    from intergen.glass import _redact
    return _redact


def test_a_credential_shaped_field_is_redacted(redact):
    """The control: the mechanism works for the case it was written for."""
    label, key, value, fragment = FIXTURES[0]
    out = redact({key: value})
    assert fragment not in json.dumps(out), (
        f"The shipped redaction did not hide {label}; the mechanism this gate is "
        "measuring the REACH of does not work at all, which is a larger finding than "
        f"the one below.\n  produced: {out!r}")


@pytest.mark.parametrize(
    "label,key,value,fragment",
    [f for f in FIXTURES[1:]],
    ids=[f[0].replace(" ", "-") for f in FIXTURES[1:]],
)
def test_a_credential_carried_inside_ordinary_text_is_redacted(
        redact, label, key, value, fragment):
    out = redact({key: value})
    rendered = json.dumps(out)
    assert fragment not in rendered, (
        f"\na credential survives into the persisted record: {label}\n"
        f"  field name : {key!r}\n"
        f"  value      : {value!r}\n"
        f"  persisted  : {rendered}\n"
        "This value arrived under a field name that is not credential-shaped, so "
        "only a predicate that reads the VALUE can hide it. On this machine that "
        "predicate is missing or does not cover this shape, and the credential "
        "stays in the record for as long as the record is kept.\n"
        "Decided 2026-08-25: the record must hide every shape in this file. A "
        "release whose installed package fails here is carrying an older "
        "redaction than its own tests describe."
    )


@pytest.mark.parametrize(
    "label,key,value",
    INNOCENT,
    ids=[f[0].replace(" ", "-") for f in INNOCENT],
)
def test_ordinary_command_text_survives_byte_identical(redact, label, key, value):
    """The other direction, and the reason there is no entropy heuristic.

    This is the control on the four assertions above. Without it, a redaction
    that replaced everything would pass every one of them.
    """
    out = redact({key: value})
    assert out[key] == value, (
        f"\nordinary content was altered: {label}\n"
        f"  field name : {key!r}\n"
        f"  written    : {value!r}\n"
        f"  persisted  : {out[key]!r}\n"
        "A hole in a full-fidelity record with nothing behind it is worse than "
        "the content it removed. Either a shape matches more than it should, or "
        "an entropy threshold has been introduced — this module's own "
        "documentation says that is a decision to be argued, not slipped in."
    )
