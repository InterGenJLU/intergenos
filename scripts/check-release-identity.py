#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""check-release-identity.py — every identity source states the ONE declared release.

WHY. An installed system read R001.1 at the top of `cat /etc/*release` and
rc001.2 at the bottom: the identity files that intergenos-base-files ships
said one release while IMAGE_VERSION, copied from the ISO file name, said a
candidate of another. Two causes, both silent before this gate: the base-files
recipe had not been rebuilt into the chroot after its identity moved, and the
ISO name carried an `rc` prefix left over from the pre-release era, when every
build was a candidate for promotion. Since the first release there are no
candidates; a release is spelled one way, everywhere.

THE DECLARATION. The release identity is declared ONCE, in
packages/core/intergenos-base-files/files/etc/igos-release — a single line such
as `R001.3`. Every other surface either derives from that line or is checked
against it here:

  tree     /etc/os-release VERSION, VERSION_ID, VERSION_CODENAME, PRETTY_NAME;
           /etc/lsb-release DISTRIB_RELEASE, DISTRIB_CODENAME, DISTRIB_DESCRIPTION;
           /etc/issue — all inside the base-files recipe (the source of the shipped bytes)
  chroot   the same four files as BUILT into the chroot (--chroot): a chroot still
           carrying the previous base-files is refused before squashfs
  iso name the --iso-name of the launch chain (--iso-name / --iso-name-file):
           intergenos-<release>.iso, lower-case, an optional re-mint ordinal
           (-02, -03 …, framework §6.4 rotation rule), no `rc`, no other prefix
  stamp    BUILD_ID and IMAGE_VERSION in a stamped os-release (--os-release):
           both equal the ISO-derived tag, in the os-release character set
  record   build/.image-version written by build-squashfs at the stamp
           (--image-version-file): the ISO minted later must carry the tag the
           squashfs was stamped with

MODES. `release` (default) fails closed on every disagreement. `test`
(UNSIGNED_TEST=1 media, which already carry the dev marker and can never be a
release) keeps the four-file agreement fatal but reports the ISO-name and stamp
disagreements as warnings, so a dev candidate can still be named freely.

Exit codes:
  0  every checked source states the declared release (warnings possible in test mode)
  1  a disagreement (each named: source, field, actual, expected)
  2  usage error, or a required input is absent or unreadable
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
PREFIX = "[release-identity]"

# The release grammar: R + three digits, an optional point number. No prefix,
# no suffix — the re-mint ordinal belongs to the ISO name, not to the release.
RELEASE_RE = re.compile(r"^R\d{3}(?:\.\d+)?$")
# A re-mint rotates an ordinal onto the ISO name; the first mint carries none.
ORDINAL_RE = r"(?:-(?:0[2-9]|[1-9]\d))?"
# The os-release character set for VERSION_ID / IMAGE_VERSION / BUILD_ID.
OS_RELEASE_CHARSET_RE = re.compile(r"^[a-z0-9._-]+$")

IDENTITY_FILES = ("igos-release", "os-release", "lsb-release", "issue")


class Disagreement:
    __slots__ = ("source", "field", "actual", "expected")

    def __init__(self, source, field, actual, expected):
        self.source, self.field, self.actual, self.expected = source, field, actual, expected

    def __str__(self):
        return f"  - {self.source}: {self.field} = {self.actual!r} (expected {self.expected!r})"


# ---------------------------------------------------------------------------
# readers
# ---------------------------------------------------------------------------
def read_kv(path: Path) -> dict[str, str]:
    """KEY=VALUE lines (os-release / lsb-release shape); quotes stripped."""
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def read_declared(etc: Path) -> tuple[str | None, list[Disagreement]]:
    """The declared release from <etc>/igos-release: exactly one line of the grammar."""
    path = etc / "igos-release"
    lines = [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if len(lines) != 1:
        return None, [Disagreement(str(path), "line count", len(lines), "exactly one line")]
    declared = lines[0]
    if not RELEASE_RE.match(declared):
        return None, [Disagreement(str(path), "release", declared,
                                   "the grammar R<three digits>[.<point>] — no rc/candidate prefix, no suffix")]
    return declared, []


def expected_forms(declared: str, codename: str) -> dict[str, str]:
    display = codename[:1].upper() + codename[1:]
    pretty = f"InterGenOS {declared} ({display})"
    return {
        "VERSION": f"{declared} ({display})",
        "VERSION_ID": declared.lower(),
        "VERSION_CODENAME": codename,
        "PRETTY_NAME": pretty,
        "DISTRIB_RELEASE": declared,
        "DISTRIB_CODENAME": codename,
        "DISTRIB_DESCRIPTION": pretty,
        "issue": pretty,
    }


def check_etc(etc: Path, source: str, declared: str | None = None) -> tuple[str | None, str | None, list[Disagreement]]:
    """Check the four identity files under <etc>.

    Returns (declared, codename, disagreements). When `declared` is given (the
    tree's declaration) the directory's own igos-release must state it too.
    """
    found: list[Disagreement] = []
    for name in IDENTITY_FILES:
        if not (etc / name).is_file():
            found.append(Disagreement(source, name, "absent", "present"))
    if found:
        return None, None, found

    own, bad = read_declared(etc)
    found.extend(bad)
    if own is None:
        return None, None, found
    if declared is not None and own != declared:
        found.append(Disagreement(f"{source}/igos-release", "release", own, declared))
    release = declared or own

    osr = read_kv(etc / "os-release")
    codename = osr.get("VERSION_CODENAME", "")
    if not codename or not OS_RELEASE_CHARSET_RE.match(codename):
        found.append(Disagreement(f"{source}/os-release", "VERSION_CODENAME", codename or "absent",
                                  "a lower-case codename in the os-release character set"))
        return release, None, found
    want = expected_forms(release, codename)

    for field in ("VERSION", "VERSION_ID", "VERSION_CODENAME", "PRETTY_NAME"):
        actual = osr.get(field, "absent")
        if actual != want[field]:
            found.append(Disagreement(f"{source}/os-release", field, actual, want[field]))
    if osr.get("NAME") != "InterGenOS":
        found.append(Disagreement(f"{source}/os-release", "NAME", osr.get("NAME", "absent"), "InterGenOS"))
    if osr.get("ID") != "intergenos":
        found.append(Disagreement(f"{source}/os-release", "ID", osr.get("ID", "absent"), "intergenos"))

    lsb = read_kv(etc / "lsb-release")
    for field in ("DISTRIB_RELEASE", "DISTRIB_CODENAME", "DISTRIB_DESCRIPTION"):
        actual = lsb.get(field, "absent")
        if actual != want[field]:
            found.append(Disagreement(f"{source}/lsb-release", field, actual, want[field]))
    if lsb.get("DISTRIB_ID") != "InterGenOS":
        found.append(Disagreement(f"{source}/lsb-release", "DISTRIB_ID", lsb.get("DISTRIB_ID", "absent"), "InterGenOS"))

    issue = (etc / "issue").read_text(encoding="utf-8")
    if want["issue"] not in issue:
        stated = re.findall(r"InterGenOS [^\n]*", issue)
        found.append(Disagreement(f"{source}/issue", "banner", stated[0] if stated else "no InterGenOS line",
                                  want["issue"]))
    return release, codename, found


def iso_tag(iso_name: str) -> str:
    """The tag build-squashfs derives from the ISO name (intergenos-<tag>.iso)."""
    base = iso_name.rsplit("/", 1)[-1]
    tag = base[len("intergenos-"):] if base.startswith("intergenos-") else base
    return tag[:-len(".iso")] if tag.endswith(".iso") else tag


def check_iso_name(iso_name: str, declared: str) -> list[Disagreement]:
    expected = f"intergenos-{declared.lower()}{ORDINAL_RE}\\.iso"
    if re.fullmatch(expected, iso_name):
        return []
    return [Disagreement("iso name", "file name", iso_name,
                         f"intergenos-{declared.lower()}.iso (a re-mint adds -02, -03 …; no rc prefix)")]


def check_stamp(os_release: Path, declared: str, iso_name: str | None) -> list[Disagreement]:
    found: list[Disagreement] = []
    kv = read_kv(os_release)
    build_id = kv.get("BUILD_ID")
    image_version = kv.get("IMAGE_VERSION")
    if iso_name is not None:
        expected = iso_tag(iso_name)
    else:
        expected = None
    for field, actual in (("BUILD_ID", build_id), ("IMAGE_VERSION", image_version)):
        if actual is None:
            found.append(Disagreement(str(os_release), field, "absent", expected or "the ISO-derived tag"))
            continue
        if not OS_RELEASE_CHARSET_RE.match(actual):
            found.append(Disagreement(str(os_release), field, actual, "lower-case a-z 0-9 . _ - only"))
            continue
        if not re.fullmatch(f"{declared.lower()}{ORDINAL_RE}", actual):
            found.append(Disagreement(str(os_release), field, actual,
                                      f"{declared.lower()} (a re-mint adds -02, -03 …; no rc prefix)"))
        elif expected is not None and actual != expected:
            found.append(Disagreement(str(os_release), field, actual, expected))
    if build_id is not None and image_version is not None and build_id != image_version:
        found.append(Disagreement(str(os_release), "BUILD_ID vs IMAGE_VERSION", f"{build_id} vs {image_version}",
                                  "equal"))
    return found


def check_image_version_file(path: Path, iso_name: str) -> list[Disagreement]:
    stamped = path.read_text(encoding="utf-8").strip()
    expected = iso_tag(iso_name)
    if stamped != expected:
        return [Disagreement(str(path), "stamped tag", stamped,
                             f"{expected} (the ISO name and the squashfs stamp must agree — rebuild squashfs or keep the launch name)")]
    return []


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------
def run(args) -> int:
    packages = Path(args.packages)
    tree_etc = packages / "core" / "intergenos-base-files" / "files" / "etc"
    if not tree_etc.is_dir():
        print(f"{PREFIX} error: {tree_etc} is not a directory (--packages)", file=sys.stderr)
        return 2

    fatal: list[Disagreement] = []
    soft: list[Disagreement] = []

    declared, _codename, found = check_etc(tree_etc, "tree base-files")
    fatal.extend(found)
    if declared is None:
        _report(None, fatal, soft, args.mode)
        return 1

    if args.chroot:
        chroot_etc = Path(args.chroot) / "etc"
        if not chroot_etc.is_dir():
            print(f"{PREFIX} error: {chroot_etc} is not a directory (--chroot)", file=sys.stderr)
            return 2
        _r, _c, found = check_etc(chroot_etc, "chroot", declared)
        fatal.extend(found)

    iso_name = args.iso_name
    if iso_name is None and args.iso_name_file:
        p = Path(args.iso_name_file)
        if p.is_file():
            first = p.read_text(encoding="utf-8").splitlines()
            iso_name = first[0].strip() if first else ""
    if iso_name is not None and iso_name != "":
        soft.extend(check_iso_name(iso_name, declared))
    elif args.require_iso_name:
        soft.append(Disagreement("iso name", "launch", "none (no --iso-name this launch chain)",
                                 f"intergenos-{declared.lower()}.iso given at launch"))
        iso_name = None
    else:
        iso_name = None

    if args.os_release:
        p = Path(args.os_release)
        if not p.is_file():
            print(f"{PREFIX} error: {p} is not a file (--os-release)", file=sys.stderr)
            return 2
        soft.extend(check_stamp(p, declared, iso_name))

    if args.image_version_file:
        p = Path(args.image_version_file)
        if not p.is_file():
            print(f"{PREFIX} error: {p} is not a file (--image-version-file)", file=sys.stderr)
            return 2
        if iso_name is None:
            print(f"{PREFIX} error: --image-version-file needs the ISO name (--iso-name / --iso-name-file)",
                  file=sys.stderr)
            return 2
        soft.extend(check_image_version_file(p, iso_name))

    if args.mode == "release":
        fatal.extend(soft)
        soft = []
    _report(declared, fatal, soft, args.mode)
    return 1 if fatal else 0


def _report(declared, fatal, soft, mode):
    if fatal:
        print(f"{PREFIX} FAIL: {len(fatal)} identity disagreement(s)"
              + (f" against the declared release {declared}" if declared else ""))
        for d in fatal:
            print(str(d))
        print(f"{PREFIX} the release is declared ONCE in packages/core/intergenos-base-files/files/etc/igos-release;"
              " scripts/set-release-identity.py <release> rewrites the four files consistently; a chroot that"
              " disagrees needs intergenos-base-files rebuilt; an ISO is named intergenos-<release>.iso at launch.")
    for d in soft:
        print(f"{PREFIX} warning ({mode} mode, not a release): {str(d).strip()}")
    if not fatal:
        print(f"{PREFIX} PASS: every checked source states the declared release {declared}"
              + (f" ({len(soft)} warning(s), test mode)" if soft else ""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--packages", default=str(_REPO_ROOT / "packages"),
                    help="the packages tree holding core/intergenos-base-files (default: this checkout's)")
    ap.add_argument("--chroot", help="a built chroot whose /etc identity files must state the declared release")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--iso-name", help="the ISO file name of this launch chain")
    g.add_argument("--iso-name-file", help="the persisted launch name (build/.iso-name); absent file = no name")
    ap.add_argument("--require-iso-name", action="store_true",
                    help="a launch chain without an ISO name is a disagreement (media about to be minted)")
    ap.add_argument("--os-release", help="a stamped os-release whose BUILD_ID/IMAGE_VERSION must carry the tag")
    ap.add_argument("--image-version-file",
                    help="build/.image-version written at the squashfs stamp; must equal the ISO name's tag")
    ap.add_argument("--mode", choices=("release", "test"), default="release",
                    help="release (default) fails on every disagreement; test keeps the four-file agreement "
                         "fatal and reports name/stamp disagreements as warnings (UNSIGNED_TEST media)")
    args = ap.parse_args(argv)
    try:
        return run(args)
    except OSError as exc:
        print(f"{PREFIX} error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
