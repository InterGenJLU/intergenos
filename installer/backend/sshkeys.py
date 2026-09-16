# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Parse pasted SSH public-key text into individual, validated keys.

Decided 2026-09-16: the installer's key input and the file it writes must
agree on what one unit of work is. Before this module, the GUI validated
`text.strip().split("\\n", 1)[0]` — the FIRST LINE ONLY — and then stored
and wrote the ENTIRE contents of the box. A person who pasted an existing
`authorized_keys` file had line 1 checked and every line installed, with
no surface that named what had been accepted. Seven keys reached a fresh
machine that way.

This module is the single source of truth for that unit. It parses a
block into one entry per key line, validates EVERY line, and reports a
line it cannot validate as a REJECTION with a reason — never by dropping
it and never by widening what is accepted. The caller decides what to do
with the two lists; nothing here writes a file or trusts a key.

What "validated" means here, in order:

  * the key type is one the shipped OpenSSH accepts (`KEY_TYPES`);
  * the key material decodes as strict base64;
  * the decoded blob's OWN embedded algorithm name (SSH wire format:
    a 4-byte big-endian length, then the name) equals the declared key
    type. This is the check that catches two keys run together and a
    type/material mismatch — a prefix test cannot;
  * the blob carries exactly the field count its algorithm defines, and
    nothing after them. The name check alone is NOT enough: material cut
    off immediately after the algorithm name still decodes and still
    names its algorithm, so a truncated paste passed a name-only check
    (caught by this file's own test before the code shipped).

Anything else — an options prefix (`command=`, `from=`, `restrict`), a
private key, a certificate, a bare comment — is rejected by name. An
options prefix in particular changes what the key is allowed to do, so
accepting one silently would install a permission nobody was shown.

Fingerprints are computed here (SHA-256 over the decoded blob, base64
without padding) rather than shelled out to `ssh-keygen`, so the surface
that shows a person their key needs no external binary and works in the
installer environment. The value is byte-identical to what
`ssh-keygen -lf` prints; `tests/installer/test_row32_authorized_keys.py`
proves that against the real tool rather than asserting it here.
"""

import base64
import hashlib
import struct
from dataclasses import dataclass

__all__ = [
    "KEY_TYPES",
    "ParsedKey",
    "RejectedLine",
    "ParseResult",
    "parse",
    "render_authorized_keys",
]


#: Public-key algorithm names the shipped OpenSSH accepts in an
#: authorized_keys line. Certificate types (`*-cert-v01@openssh.com`) are
#: deliberately absent: a certificate is trusted through a CA declared
#: with TrustedUserCAKeys, not by being listed here, and accepting one as
#: an ordinary key would install something that does not work.
KEY_TYPES = frozenset({
    "ssh-rsa",
    "ssh-ed25519",
    "ssh-dss",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@openssh.com",
    "sk-ecdsa-sha2-nistp256@openssh.com",
})


#: How many length-prefixed fields each algorithm's blob carries, counting
#: the algorithm name itself. From RFC 4253 §6.6 (ssh-rsa, ssh-dss),
#: RFC 5656 §3.1 (ecdsa), RFC 8709 §4 (ed25519) and PROTOCOL.u2f (the
#: sk-* variants, which append the FIDO2 application string).
_FIELD_COUNTS = {
    "ssh-rsa": 3,                                # name, e, n
    "ssh-dss": 5,                                # name, p, q, g, y
    "ssh-ed25519": 2,                            # name, key
    "ecdsa-sha2-nistp256": 3,                    # name, curve, point
    "ecdsa-sha2-nistp384": 3,
    "ecdsa-sha2-nistp521": 3,
    "sk-ssh-ed25519@openssh.com": 3,             # + application
    "sk-ecdsa-sha2-nistp256@openssh.com": 4,     # + curve, point, application
}


@dataclass(frozen=True)
class ParsedKey:
    """One key line that passed every check in this module.

    `lineno` is 1-based and counts every line of the input as pasted,
    including blanks and comments, so a message about line 4 points at
    the line the person can see.
    """

    lineno: int
    key_type: str
    material: str
    comment: str
    fingerprint: str

    @property
    def line(self):
        """The single line to write into authorized_keys."""
        if self.comment:
            return f"{self.key_type} {self.material} {self.comment}"
        return f"{self.key_type} {self.material}"

    @property
    def bits(self):
        """Key size in bits where it is derivable, else None.

        Ed25519 and the FIDO2 variants are fixed-size; ECDSA carries its
        size in the curve name; RSA and DSA carry it in the modulus. A
        value that cannot be derived honestly is None, not a guess.
        """
        if self.key_type in ("ssh-ed25519", "sk-ssh-ed25519@openssh.com"):
            return 256
        if "nistp" in self.key_type:
            return int(self.key_type.split("nistp")[1].split("@")[0])
        if self.key_type in ("ssh-rsa", "ssh-dss"):
            try:
                fields = _wire_fields(base64.b64decode(self.material,
                                                      validate=True))
            except (ValueError, struct.error):
                return None
            # ssh-rsa: name, e, n — the modulus is the last field.
            # ssh-dss: name, p, q, g, y — p is the modulus.
            modulus = fields[-1] if self.key_type == "ssh-rsa" else fields[1]
            modulus = modulus.lstrip(b"\x00")
            return len(modulus) * 8 if modulus else None
        return None


@dataclass(frozen=True)
class RejectedLine:
    """One line that could not be validated, and why.

    A rejection is a thing to SHOW, not a thing to swallow: every caller
    in the tree puts the reason in front of the person rather than
    continuing with a quietly smaller key set.
    """

    lineno: int
    text: str
    reason: str


@dataclass(frozen=True)
class ParseResult:
    accepted: tuple
    rejected: tuple

    def __bool__(self):
        return bool(self.accepted)


def _wire_fields(blob):
    """Split an SSH wire-format blob into its length-prefixed fields."""
    fields = []
    offset = 0
    while offset < len(blob):
        if offset + 4 > len(blob):
            raise ValueError("truncated length prefix")
        (size,) = struct.unpack(">I", blob[offset:offset + 4])
        offset += 4
        if offset + size > len(blob):
            raise ValueError("field runs past the end of the key material")
        fields.append(blob[offset:offset + size])
        offset += size
    if not fields:
        raise ValueError("no fields")
    return fields


def _describe_unrecognised(first_field, line):
    """A reason a person can act on, for a line we will not accept."""
    if line.startswith("-----BEGIN"):
        return ("this is a PRIVATE key, not a public key — never paste a "
                "private key anywhere; the public half usually sits beside "
                "it with a .pub suffix")
    if first_field.endswith("-cert-v01@openssh.com"):
        return ("this is a signed certificate, not a plain public key; a "
                "certificate is trusted through its authority, so it cannot "
                "be installed as an ordinary key here")
    if "=" in first_field or first_field in ("restrict", "no-pty",
                                             "no-agent-forwarding",
                                             "no-port-forwarding",
                                             "no-X11-forwarding",
                                             "cert-authority"):
        return (f"this line starts with the option {first_field!r}. Options "
                "change what a key is allowed to do, so they are not "
                "accepted here — paste the key by itself and add options "
                "afterwards by editing ~/.ssh/authorized_keys")
    return (f"unrecognised key type {first_field!r} — expected one of: "
            + ", ".join(sorted(KEY_TYPES)))


def parse(text):
    """Parse pasted text into accepted keys and rejected lines.

    Blank lines and `#` comment lines (the authorized_keys comment
    syntax) are neither accepted nor rejected — they carry no key and
    losing them loses nothing. Every other line produces exactly one
    entry in one of the two lists, so nothing the person pasted can
    disappear without being named.

    A key that repeats is accepted once; the repeat is reported as a
    rejection naming the line it duplicates, because a file with the
    same key twice is a file nobody meant to write.
    """
    accepted = []
    rejected = []
    seen = {}

    for lineno, raw in enumerate((text or "").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        fields = line.split(None, 2)
        if len(fields) < 2:
            rejected.append(RejectedLine(
                lineno, line,
                "no key material after the key type — an SSH public key is "
                "a type, then the key material, then an optional comment"))
            continue

        key_type, material = fields[0], fields[1]
        comment = fields[2].strip() if len(fields) > 2 else ""

        if key_type not in KEY_TYPES:
            rejected.append(RejectedLine(
                lineno, line, _describe_unrecognised(key_type, line)))
            continue

        try:
            blob = base64.b64decode(material, validate=True)
        except (ValueError, base64.binascii.Error):
            rejected.append(RejectedLine(
                lineno, line,
                "the key material is not valid base64 — the paste is most "
                "likely truncated or has had a line break inserted into it"))
            continue

        try:
            wire = _wire_fields(blob)
            embedded = wire[0].decode("ascii")
        except (ValueError, struct.error, UnicodeDecodeError) as exc:
            rejected.append(RejectedLine(
                lineno, line,
                f"the key material is not a well-formed SSH key ({exc})"))
            continue

        if embedded != key_type:
            rejected.append(RejectedLine(
                lineno, line,
                f"the line says {key_type!r} but the key material itself "
                f"says {embedded!r} — these must agree, so this paste is "
                "damaged or two keys have run together"))
            continue

        expected = _FIELD_COUNTS[key_type]
        if len(wire) != expected:
            rejected.append(RejectedLine(
                lineno, line,
                f"a {key_type} key is made of {expected} parts and this one "
                f"has {len(wire)} — the paste is incomplete or has extra "
                "text on the end"))
            continue

        fingerprint = "SHA256:" + base64.b64encode(
            hashlib.sha256(blob).digest()).decode("ascii").rstrip("=")

        if fingerprint in seen:
            rejected.append(RejectedLine(
                lineno, line,
                f"the same key is already on line {seen[fingerprint]} — it "
                "is installed once, not twice"))
            continue

        seen[fingerprint] = lineno
        accepted.append(ParsedKey(lineno, key_type, material, comment,
                                  fingerprint))

    return ParseResult(tuple(accepted), tuple(rejected))


def render_authorized_keys(keys):
    """The exact bytes to write into authorized_keys for these keys."""
    return "".join(f"{key.line}\n" for key in keys)
