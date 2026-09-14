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
"""

from pathlib import Path

import pytest

SHADOW = Path("/etc/shadow")
LOCKED = {"*", "!", "!*", "!!", "x", ""}


def _root_field(text: str):
    for line in text.splitlines():
        parts = line.split(":")
        if parts and parts[0] == "root":
            return parts[1] if len(parts) > 1 else ""
    return None


@pytest.mark.usefixtures("require_installed_intergenos")
class TestRootPasswordKept:
    def test_control_reader_sees_a_locked_field(self):
        assert _root_field("root:!:1:0:99999:7:::\n") in LOCKED

    def test_installed_root_has_a_real_hash(self):
        try:
            text = SHADOW.read_text()
        except PermissionError:
            pytest.skip("NOT VERIFIED: /etc/shadow is readable by root only; "
                        "run this gate as root (the root tier)")
        field = _root_field(text)
        assert field is not None, "no root entry in /etc/shadow"
        assert field not in LOCKED and field.startswith("$"), (
            f"root's password field is {field!r}: the install left root with no "
            f"usable credential (R001.3 row 5)")
