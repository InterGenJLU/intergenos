# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Regression tests for pkm.version — the dpkg comparator (PKM-A01..A06).

Gates the trust-first fixes:
  PKM-A01  real upstream versions order correctly and NEVER raise 'corruption'
  PKM-A03  an upstream -N suffix does NOT collide with the separate release int
  PKM-A04  1.0 and 1.0.0 are NOT treated as equal (no masked upgrade)
  PKM-A05  a malformed release surfaces (raise), an absent one defaults to 1
  PKM-A06  (release tie-break is exercised; display is tested in cli tests)
"""
import pytest

from pkm.version import compare, is_upgradable, VersionParseError

# The exact installed version strings from the development machine audit that the old PEP-440
# comparator declared "build-system or repo-index corruption". Every one is a
# valid upstream version and must compare cleanly (==self -> 0), never raise.
REAL_WORLD_VERSIONS = [
    "140.9.0esr", "b8796", "10.2p1", "1.9.17p2", "2026_01_20.386b5f5",
    "2025-04-17", "2025-08-21", "2025-07-06", "2025-04-25", "2025-02-10",
    "140.8.0esr", "2025-07-24", "2025-12-27", "3510200",
]


@pytest.mark.parametrize("v", REAL_WORLD_VERSIONS)
def test_real_upstream_versions_never_raise_and_self_equal(v):
    # PKM-A01: these used to raise VersionParseError("...corruption").
    assert compare({"version": v, "release": 1}, {"version": v, "release": 1}) == 0


@pytest.mark.parametrize("older,newer", [
    ("1.0", "1.1"),
    ("1.9", "1.10"),          # numeric, not lexical
    ("1.0", "1.0.1"),
    ("1.0", "1.0.0"),         # PKM-A04: shorter sorts BEFORE longer (not equal)
    ("1.0~rc1", "1.0"),       # tilde pre-release sorts before the release
    ("1.0~~", "1.0~"),
    ("10.2p1", "10.2p2"),     # patch-letter suffix orders numerically
    ("b8796", "b8797"),       # build-counter prefix
    ("140.8.0esr", "140.9.0esr"),
    ("2025-04-17", "2025-08-21"),
    ("1.0.0", "1.0.0esr"),    # trailing letters sort AFTER the bare number
])
def test_ordering(older, newer):
    assert compare({"version": older}, {"version": newer}) == -1
    assert compare({"version": newer}, {"version": older}) == 1
    assert compare({"version": older}, {"version": older}) == 0


def test_release_is_the_tiebreak_when_versions_equal():
    # PKM-A06 / the phantom-23 case: same version, release advances.
    assert compare({"version": "0.1.0", "release": 4},
                   {"version": "0.1.0", "release": 5}) == -1
    assert compare({"version": "3.1.7", "release": 1},
                   {"version": "3.1.7", "release": 1}) == 0


def test_version_outranks_release():
    # PKM-A03: a higher version wins regardless of release; the -N-style suffix
    # is NOT folded into the version compare.
    assert compare({"version": "1.0.0", "release": 9},
                   {"version": "1.0.1", "release": 1}) == -1
    # an upstream version string that itself contains -N stays opaque
    assert compare({"version": "1.0.0-5", "release": 1},
                   {"version": "1.0.0-6", "release": 1}) == -1


def test_is_upgradable_release_only_bump():
    installed = {"version": "0.1.0", "release": 4}
    remote = {"version": "0.1.0", "release": 5}
    assert is_upgradable(installed, remote) is True
    # same (version, release) -> NOT upgradable (the apparmor-once-captured case)
    assert is_upgradable({"version": "3.1.7", "release": 1},
                         {"version": "3.1.7", "release": 1}) is False
    # remote OLDER -> not upgradable unless --allow-downgrade
    assert is_upgradable({"version": "2.0"}, {"version": "1.0"}) is False
    assert is_upgradable({"version": "2.0"}, {"version": "1.0"},
                         allow_downgrade=True) is True


def test_malformed_release_raises_not_silently_coerced():
    # PKM-A05: a present-but-garbage release must surface, not become 1.
    with pytest.raises(VersionParseError):
        compare({"version": "1.0", "release": "5a"},
                {"version": "1.0", "release": 1})


def test_absent_release_defaults_to_one():
    # absent release is legitimate (DB schema default) -> 1, no raise.
    assert compare({"version": "1.0"}, {"version": "1.0", "release": 1}) == 0


def test_empty_version_raises():
    with pytest.raises(VersionParseError):
        compare({"version": ""}, {"version": "1.0"})


def test_tuple_operands_supported():
    # publish-repo.sh and others pass (ver, rel) tuples.
    assert compare(("1.0", 1), ("1.0", 2)) == -1
    assert compare(("1.10", 1), ("1.2", 1)) == 1  # numeric ordering: 1.10 > 1.2
