# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Regression test for PKM-A06 — comparison displays show version-release.

The plan summary, `list upgradable`, and `check-updates` stdout all render
package identity via cli._vr_str so a release-only bump reads as
'0.1.0-4 -> 0.1.0-5' instead of the no-op-looking '0.1.0 -> 0.1.0'.
"""
from pkm.cli import _vr_str


def test_vr_str_renders_version_release():
    assert _vr_str("0.1.0", 5) == "0.1.0-5"
    assert _vr_str("3.1.7", 2) == "3.1.7-2"


def test_vr_str_absent_release_defaults_to_one():
    assert _vr_str("1.0", None) == "1.0-1"
    assert _vr_str("1.0") == "1.0-1"


def test_vr_str_malformed_release_shown_verbatim_not_masked():
    # A malformed release is surfaced, not silently coerced to 1.
    assert _vr_str("1.0", "5a") == "1.0-5a"
