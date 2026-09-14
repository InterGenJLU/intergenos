#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""set-release-identity.py — declare the cycle's release identity ONCE.

The release a cycle ships is spelled in one place, the igos-release file of
the intergenos-base-files recipe, and the three files beside it (os-release,
lsb-release, issue) derive from it. This command writes all four consistently
and then runs scripts/check-release-identity.py over the result, so a cycle's
identity step is one command and one gate rather than four hand edits.

It changes the recipe's shipped files only; the recipe's release number moves
by the ordinary machine bump (scripts/bump-changed-releases.py) at commit time,
and the chroot must then rebuild intergenos-base-files before squashfs — the
gate refuses a chroot that still carries the previous identity.

Usage:
  python3 scripts/set-release-identity.py R001.3 [--codename revival] [--packages <dir>]

Exit codes: 0 written (or already consistent) and the gate passes; 1 the
release is not in the grammar or the gate still fails; 2 a file is missing.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent


def _gate():
    spec = importlib.util.spec_from_file_location("check_release_identity", _HERE / "check-release-identity.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _replace_kv(text: str, field: str, value: str, quoted: bool) -> tuple[str, bool]:
    """Replace the value of one KEY= line in place; append if the key is absent."""
    rendered = f'{field}="{value}"' if quoted else f"{field}={value}"
    pattern = re.compile(rf"^{re.escape(field)}=.*$", re.M)
    if pattern.search(text):
        new = pattern.sub(rendered, text, count=1)
    else:
        new = text + ("" if text.endswith("\n") or not text else "\n") + rendered + "\n"
    return new, new != text


def apply(packages: Path, release: str, codename: str | None) -> list[str]:
    gate = _gate()
    if not gate.RELEASE_RE.match(release):
        raise ValueError(f"{release!r} is not a release: the grammar is R<three digits>[.<point>] "
                         "(no rc/candidate prefix, no suffix)")
    etc = packages / "core" / "intergenos-base-files" / "files" / "etc"
    for name in gate.IDENTITY_FILES:
        if not (etc / name).is_file():
            raise FileNotFoundError(etc / name)

    osr = gate.read_kv(etc / "os-release")
    codename = (codename or osr.get("VERSION_CODENAME") or "").strip().lower()
    if not codename or not gate.OS_RELEASE_CHARSET_RE.match(codename):
        raise ValueError("a codename is needed (--codename) and it must be lower-case a-z 0-9 . _ -")
    want = gate.expected_forms(release, codename)
    changed: list[str] = []

    text = (etc / "igos-release").read_text(encoding="utf-8")
    new = release + "\n"
    if new != text:
        (etc / "igos-release").write_text(new, encoding="utf-8")
        changed.append("igos-release")

    text = (etc / "os-release").read_text(encoding="utf-8")
    moved = False
    for field, quoted in (("VERSION", True), ("VERSION_ID", False), ("VERSION_CODENAME", False), ("PRETTY_NAME", True)):
        text, m = _replace_kv(text, field, want[field], quoted)
        moved = moved or m
    if moved:
        (etc / "os-release").write_text(text, encoding="utf-8")
        changed.append("os-release")

    text = (etc / "lsb-release").read_text(encoding="utf-8")
    moved = False
    for field in ("DISTRIB_RELEASE", "DISTRIB_CODENAME", "DISTRIB_DESCRIPTION"):
        text, m = _replace_kv(text, field, want[field], True)
        moved = moved or m
    if moved:
        (etc / "lsb-release").write_text(text, encoding="utf-8")
        changed.append("lsb-release")

    text = (etc / "issue").read_text(encoding="utf-8")
    new = re.sub(r"^(\s*)InterGenOS [^\n]*$", lambda m: m.group(1) + want["issue"], text, count=1, flags=re.M)
    if new == text and want["issue"] not in text:
        new = f"\n  {want['issue']}\n" + text
    if new != text:
        (etc / "issue").write_text(new, encoding="utf-8")
        changed.append("issue")
    return changed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("release", help="the release to declare, e.g. R001.3")
    ap.add_argument("--codename", help="the codename (default: the one os-release already carries)")
    ap.add_argument("--packages", default=str(_REPO_ROOT / "packages"))
    args = ap.parse_args(argv)
    packages = Path(args.packages)
    try:
        changed = apply(packages, args.release, args.codename)
    except FileNotFoundError as exc:
        print(f"[set-release-identity] error: {exc} is missing", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"[set-release-identity] error: {exc}", file=sys.stderr)
        return 1
    if changed:
        print(f"[set-release-identity] {args.release}: rewrote " + ", ".join(changed)
              + " under packages/core/intergenos-base-files/files/etc")
    else:
        print(f"[set-release-identity] {args.release}: the four identity files already state it (no change)")
    return _gate().main(["--packages", str(packages)])


if __name__ == "__main__":
    sys.exit(main())
