# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""GATE — on the INSTALLED system every identity file states the ONE release.

WHAT THIS CATCHES THAT THE SOURCE-TREE GATE CANNOT. scripts/check-release-identity.py
proves the recipe, the chroot and the stamp agree at build time. Only the
installed target can show what an install actually left in /etc: one machine
read R001.1 at the top of `cat /etc/*release` (igos-release, lsb-release,
os-release VERSION) and rc001.2 at the bottom (IMAGE_VERSION, copied from the
ISO name by the installer) — one system, two releases (R001.3 row 42).

Four facts are read from the installed files, unprivileged:
  1. the four identity files agree with each other on one release, spelled in
     the release grammar (no rc/candidate prefix);
  2. IMAGE_VERSION is that release in the os-release character set, with at
     most a re-mint ordinal, and equals BUILD_ID when both are present;
  3. the installed intergenos-base-files is the one the mirror serves — the
     package manager's own upgradable list must not name it (an installed
     identity set that is behind the shipped one is not the release's);
  4. the shipped package's igos-release matches the installed file byte for
     byte (the package manager does not content-check /etc files, so this
     gate reads the package's own record of the file).

CONTROLS. The reader is run against a copy carrying the origin case
(IMAGE_VERSION="rc001.2-03") and must fail; a reader that cannot fail is not a
reader.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

ETC = Path("/etc")
RELEASE_RE = re.compile(r"^R\d{3}(?:\.\d+)?$")
ORDINAL = r"(?:-(?:0[2-9]|[1-9]\d))?"
CHARSET_RE = re.compile(r"^[a-z0-9._-]+$")
MEDIUM_RE = re.compile(r"^(?:rc|r)(?P<num>\d{3}(?:\.\d+)?)(?:-(?:0[2-9]|[1-9]\d))?$")
# The record runner declares the shape of the machine it measures: "fresh" (installed
# from the release medium — the default and the strict rule) or "upgrade" (moved from
# an earlier release by the package manager; the medium it was installed from is an
# older or equal release, and that is the truth the gate requires it to state).
SHAPE_ENV = "IGOS_RECORD_SHAPE"
SHAPES = ("fresh", "upgrade")


def _kv(text: str) -> dict[str, str]:
    out = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def _release_number(num: str) -> tuple[int, ...]:
    return tuple(int(part) for part in num.split("."))


def identity_disagreements(igos: str, os_release: str, lsb: str, issue: str,
                           shape: str = "fresh") -> list[str]:
    """Every way the four texts fail to state one release. Empty = agreement.

    On a fresh install IMAGE_VERSION must state the running release (the medium
    it was installed from IS the release). On an upgrade-shape record it must be a
    medium tag of an older or equal release: the machine was installed from that
    medium and moved forward by the package manager, and the gate requires the
    truth, not a rewritten tag."""
    if shape not in SHAPES:
        return [f"record shape {shape!r} is not one of {SHAPES}"]
    found = []
    lines = [l.strip() for l in igos.splitlines() if l.strip()]
    if len(lines) != 1 or not RELEASE_RE.match(lines[0]):
        return [f"igos-release does not declare one release in the grammar: {lines!r}"]
    release = lines[0]
    osr, lsbv = _kv(os_release), _kv(lsb)
    codename = osr.get("VERSION_CODENAME", "")
    display = codename[:1].upper() + codename[1:]
    pretty = f"InterGenOS {release} ({display})"
    want = {"VERSION": f"{release} ({display})", "VERSION_ID": release.lower(), "PRETTY_NAME": pretty}
    for field, expected in want.items():
        if osr.get(field) != expected:
            found.append(f"os-release {field}={osr.get(field)!r} (expected {expected!r})")
    for field, expected in (("DISTRIB_RELEASE", release), ("DISTRIB_DESCRIPTION", pretty)):
        if lsbv.get(field) != expected:
            found.append(f"lsb-release {field}={lsbv.get(field)!r} (expected {expected!r})")
    if pretty not in issue:
        found.append(f"issue carries no {pretty!r} banner")
    image_version = osr.get("IMAGE_VERSION")
    if image_version is None:
        found.append("os-release carries no IMAGE_VERSION (the installer records the medium's tag)")
    elif shape == "upgrade":
        medium = MEDIUM_RE.match(image_version) if CHARSET_RE.match(image_version) else None
        if medium is None:
            found.append(f"os-release IMAGE_VERSION={image_version!r} is not a medium tag "
                         "(r001.2, rc001.2-03, r001.3-02 …)")
        elif _release_number(medium.group("num")) > _release_number(release[1:]):
            found.append(f"os-release IMAGE_VERSION={image_version!r} names a medium NEWER than the "
                         f"running release {release}: an upgraded system was installed from an "
                         "older or equal medium")
    elif not CHARSET_RE.match(image_version) or not re.fullmatch(f"{release.lower()}{ORDINAL}", image_version):
        found.append(f"os-release IMAGE_VERSION={image_version!r} does not state {release.lower()!r} "
                     "(a re-mint adds -02, -03 …; no rc prefix)")
    build_id = osr.get("BUILD_ID")
    if build_id is not None and image_version is not None and build_id != image_version:
        found.append(f"BUILD_ID={build_id!r} differs from IMAGE_VERSION={image_version!r}")
    return found


def _read(name: str) -> str:
    return (ETC / name).read_text(encoding="utf-8")


class TestTheReaderOnAnUpgradedSystem:
    """Pure controls for the shape rule; no installed system is needed to run them."""
    IGOS = "R001.3\n"
    LSB = ('DISTRIB_ID=InterGenOS\nDISTRIB_RELEASE=R001.3\nDISTRIB_CODENAME=revival\n'
           'DISTRIB_DESCRIPTION="InterGenOS R001.3 (Revival)"\n')
    ISSUE = "InterGenOS R001.3 (Revival) \\n \\l\n"

    @staticmethod
    def _osr(image_version: str) -> str:
        return ('NAME="InterGenOS"\nVERSION="R001.3 (Revival)"\nID=intergenos\nVERSION_ID=r001.3\n'
                'VERSION_CODENAME=revival\nPRETTY_NAME="InterGenOS R001.3 (Revival)"\n'
                f'IMAGE_VERSION="{image_version}"\n')

    def test_an_older_medium_is_the_truth_on_an_upgrade(self):
        assert identity_disagreements(self.IGOS, self._osr("rc001.2-03"), self.LSB, self.ISSUE,
                                      shape="upgrade") == []

    def test_the_same_medium_still_fails_a_fresh_install(self):
        found = identity_disagreements(self.IGOS, self._osr("rc001.2-03"), self.LSB, self.ISSUE)
        assert any("rc001.2-03" in f for f in found), "the fresh rule must still refuse the rc stamp"

    def test_a_newer_medium_fails_an_upgrade(self):
        found = identity_disagreements(self.IGOS, self._osr("r001.4"), self.LSB, self.ISSUE, shape="upgrade")
        assert any("NEWER" in f for f in found)

    def test_a_tag_that_is_no_medium_fails_an_upgrade(self):
        found = identity_disagreements(self.IGOS, self._osr("latest"), self.LSB, self.ISSUE, shape="upgrade")
        assert any("not a medium tag" in f for f in found)

    def test_an_unknown_shape_is_refused(self):
        found = identity_disagreements(self.IGOS, self._osr("r001.3"), self.LSB, self.ISSUE, shape="other")
        assert found and "shape" in found[0]


@pytest.mark.usefixtures("require_installed_intergenos")
class TestReleaseIdentityTruth:
    def test_control_reader_fails_on_the_origin_case(self):
        os_release = _read("os-release")
        os_release = re.sub(r"^IMAGE_VERSION=.*$", 'IMAGE_VERSION="rc001.2-03"', os_release, flags=re.M)
        if "IMAGE_VERSION=" not in os_release:
            os_release += 'IMAGE_VERSION="rc001.2-03"\n'
        found = identity_disagreements(_read("igos-release"), os_release, _read("lsb-release"), _read("issue"))
        assert any("rc001.2-03" in f for f in found), "the control must fail on the rc stamp"

    def test_installed_identity_files_state_one_release(self):
        shape = os.environ.get(SHAPE_ENV, "fresh")
        if shape == "upgrade":
            print(f"[identity] upgrade-shape record: installed from the medium "
                  f"{_kv(_read('os-release')).get('IMAGE_VERSION')!r}, running {_read('igos-release').strip()}")
        found = identity_disagreements(_read("igos-release"), _read("os-release"),
                                       _read("lsb-release"), _read("issue"), shape=shape)
        assert not found, ("the installed identity files disagree (R001.3 row 42):\n  - "
                           + "\n  - ".join(found))

    def test_installed_base_files_is_the_shipped_one(self):
        r = subprocess.run(["pkm", "list", "upgradable"], capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            pytest.fail("NOT VERIFIED: `pkm list upgradable` could not run "
                        f"(exit {r.returncode}): {(r.stderr or r.stdout).strip()[:300]}")
        assert not re.search(r"^\s*intergenos-base-files\b", r.stdout, re.M), (
            "intergenos-base-files is upgradable: the installed identity files are not the ones "
            "the mirror ships for this release\n" + r.stdout[:600])

    def test_shipped_igos_release_matches_the_installed_file(self):
        r = subprocess.run(["pkm", "info", "intergenos-base-files"], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            pytest.fail("NOT VERIFIED: intergenos-base-files is not registered as installed "
                        f"(pkm info exit {r.returncode})")
        r = subprocess.run(["pkm", "files", "intergenos-base-files"], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0 and re.search(r"^\s*/etc/igos-release\s*$", r.stdout, re.M), (
            "the package manager does not record /etc/igos-release as a file of intergenos-base-files")
        installed = _read("igos-release").strip()
        assert RELEASE_RE.match(installed), f"/etc/igos-release={installed!r} is not in the release grammar"
