# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""GATE (root tier) — the INSTALLED system carries a real root password hash.

Every R001.2 install landed with root locked: the shadow package's post-install
hook replaced the hash the installer wrote (R001.3 row 5). The installer now
proves the hash survived its own hooks; this gate proves it on the machine that
came out of the install, from /etc/shadow itself. Reading that file needs root,
so an unprivileged run says NOT VERIFIED rather than pretending.

The installed system is not media: D-007's locked root applies to the ISO and
the image, never to a machine whose owner set a password in the installer.

THE FAILURE NAMES THE FIELD'S CLASS, NEVER ITS BYTES. The message lands in the
run's capture, its report and its sealed record, and this gate runs as root on
every validation machine. A field that is locked but still carries its hash
(passwd -l puts '!' in front of it) fails the gate, so printing the field would
put root's hash into all three. The message says what the field is — the locked
marker, or the hash scheme and the hash's length — and the gate fails without a
traceback, which would otherwise print the comparison's operands.
"""

from pathlib import Path

import pytest

SHADOW = Path("/etc/shadow")
LOCKED = {"*", "!", "!*", "!!", "x", ""}
# The crypt schemes libxcrypt documents, by the identifier between the first two
# dollar signs. Only these names are reported; anything else in that position
# could be the field's own bytes.
SCHEMES = {"y", "gy", "7", "2b", "2a", "2x", "2y", "6", "5", "sha1", "md5", "1",
           "3", "sm3", "sm3y"}


def _root_field(text: str):
    for line in text.splitlines():
        parts = line.split(":")
        if parts and parts[0] == "root":
            return parts[1] if len(parts) > 1 else ""
    return None


def _field_class(field: str) -> str:
    """What root's password field is, in words that carry none of its bytes."""
    if field == "":
        return "empty"
    if field in LOCKED:
        return f"the locked marker {field!r}"
    rest = field.lstrip("!*")
    marker = field[:len(field) - len(rest)]
    scheme = rest[1:].split("$", 1)[0] if rest.startswith("$") else None
    if scheme in SCHEMES and rest.startswith(f"${scheme}$"):
        what = f"a ${scheme}$ hash of {len(rest)} characters"
    else:
        what = (f"a value of {len(rest)} characters that is not a hash of a "
                f"known crypt scheme")
    return f"locked ({marker!r}) over {what}" if marker else what


@pytest.mark.usefixtures("require_installed_intergenos")
class TestRootPasswordKept:
    def test_control_reader_sees_a_locked_field(self):
        assert _root_field("root:!:1:0:99999:7:::\n") in LOCKED

    @pytest.mark.root_tier
    def test_installed_root_has_a_real_hash(self):
        try:
            text = SHADOW.read_text()
        except PermissionError:
            pytest.skip("NOT VERIFIED: /etc/shadow is readable by root only; "
                        "run this gate as root (the root tier)")
        field = _root_field(text)
        assert field is not None, "no root entry in /etc/shadow"
        if field in LOCKED or not field.startswith("$"):
            pytest.fail(f"root's password field is {_field_class(field)}: the "
                        f"install left root with no usable credential (R001.3 "
                        f"row 5)", pytrace=False)
