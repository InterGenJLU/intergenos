"""GATE 13 — secret and redaction fixtures (section 9 line 12; ADVISORY).

ADVISORY IN R001.2 by the master plan's own wording — it becomes blocking once the
redaction contract is decided. It is authored now, with the fixtures that will decide
it, so the contract is chosen against real cases rather than in the abstract.

WHAT COMPOSITION PROPERTY THIS CATCHES. The shipped redaction decides what to hide by
looking at the NAME of a field. A field called ``api_key`` has its value replaced; a
field called ``command`` whose value happens to contain an interface token does not.
The persisted record therefore protects a credential that arrives in a
credential-shaped field and keeps one that arrives inside ordinary text — which is how
a person actually types them.

THE FIXTURES BELOW ARE THE CONTRACT QUESTION, NOT THE ANSWER. Each is a shape a
credential really takes in a shell command or a pasted block. Whoever decides the
contract decides which of them the record must hide; this gate then encodes that
decision. Reported, not softened.

EXPECTED TO FAIL ON R001.1 AS SHIPPED.
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
        f"\nADVISORY — a credential survives into the persisted record: {label}\n"
        f"  field name : {key!r}\n"
        f"  value      : {value!r}\n"
        f"  persisted  : {rendered}\n"
        "The redaction matches on the NAME of the field. This value arrived under a "
        "name that is not credential-shaped, so it is written out in full and stays in "
        "the record for as long as the record is kept.\n"
        "This gate is advisory until the redaction contract is decided. The decision "
        "this fixture asks for is whether the record must also inspect VALUES — and if "
        "so, at what cost in false redactions of ordinary text."
    )
