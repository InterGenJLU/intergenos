#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Fail-closed preflight gate: every recipe's `license:` must be a real SPDX
licence expression, every identifier in it on the SPDX licence list.

WHY A SEPARATE GATE, AND WHY HERE
---------------------------------
`license:` is the field every downstream licence consumer reads: the SBOM
generator copies it into `licenseDeclared`, the mirror index carries it, and a
licence audit of a shipped image begins and ends with it. Nothing checked it.
A recipe could declare `Public-Domain`, `MIT-style` or `Various
(redistributable)` and every tool downstream would faithfully propagate a
string no SPDX consumer can resolve.

scripts/iso-sbom-gen.py validates the SHAPE of an expression — identifiers
joined by AND/OR/WITH — and says in its own docstring that list membership
belongs to "the package-metadata lint that owns license: as a field". This is
that lint. Shape validation alone passes a misspelling: `Zope-2.0` and
`Public-Domain` are both well-formed tokens and neither is a licence anyone
can look up.

The check is static and costs milliseconds — it reads recipes and one bundled
data file, never the chroot or the network — so it runs at preflight and
refuses the build before it starts, rather than shipping unresolvable licence
metadata into an image and a mirror index.

FAIL-CLOSED
-----------
A recipe whose licence cannot be READ is a failure, never a skip. Unparseable
YAML, a missing `license:` key, a non-string value, an empty string: each is a
finding naming the file. A gate that skips what it cannot read reports a zero
it never earned.

Every recipe is scanned and every finding reported, so one run gives the whole
fix list instead of stopping at the first bad file.

WHAT COUNTS AS VALID
--------------------
An SPDX licence expression:

  * an identifier on the SPDX licence list (`MIT`, `GPL-3.0-or-later`), with
    an optional trailing `+`;
  * `LicenseRef-<idstring>`, optionally `DocumentRef-<id>:`-qualified — SPDX's
    own mechanism for a licence that is not on the list. A package whose
    licence genuinely has no list identifier declares it this way; that is the
    answer, not an exception-list entry;
  * those joined by `AND` / `OR`, with parentheses;
  * `<licence> WITH <exception>`, where the right operand must be on the SPDX
    licence-EXCEPTION list — a distinct list, and the reason a bare
    "is it on the licence list" check would wave `MIT WITH MIT` through.

Deprecated identifiers (`GPL-2.0`, `LGPL-2.1`) ARE on the list and therefore
PASS. They are reported separately as warnings, because replacing `GPL-2.0`
with `GPL-2.0-only` or `GPL-2.0-or-later` resolves an ambiguity the recipe
never resolved — a licensing determination that belongs to whoever reads the
package's own licence text, not to a lint. Silence about them would be the
worse error; so would a gate quietly picking one.

THE EXCEPTION LIST
------------------
`EXEMPT_PACKAGES` is empty, and the intent is that it stays that way.
`LicenseRef-` already covers every licence SPDX does not carry, so an entry
here means something stranger. Any entry must name the package and say in a
comment why that licence is genuinely absent from SPDX and why a LicenseRef is
not the right shape for it.

THE BUNDLED LIST, AND ITS STALENESS
-----------------------------------
The identifier sets live in config/spdx-license-list.json, checked in with the
upstream tag, the licence-list version, and the sha256 of each upstream file
it was built from. A build gate must not fetch: that would make the verdict
depend on a third party being reachable and honest at that moment.

The honest cost is that the bundled list ages. This gate answers that by
printing the licence-list version and release date on EVERY run, pass or fail,
so a stale list is stated rather than assumed, and by carrying the refresh
procedure in the data file itself. A newly-published identifier a recipe wants
is a list refresh, not a gate bypass.

Usage:
    scripts/preflight-license-identifiers.py
    scripts/preflight-license-identifiers.py --root /path/to/checkout
    scripts/preflight-license-identifiers.py --shipped-only
    scripts/preflight-license-identifiers.py --quiet

Exit 0 clean, 1 on findings, 2 on usage/setup errors.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_RELPATH = "config/spdx-license-list.json"

# Packages whose licence is genuinely absent from SPDX in a way LicenseRef-
# does not express. Empty by design — see the module docstring. Each entry is
# "<tier>/<name>": "why SPDX has no identifier for this and why LicenseRef- is
# the wrong shape here".
EXEMPT_PACKAGES: dict[str, str] = {}

# One expression token. SPDX identifiers are letters, digits, '.' and '-'; the
# trailing '+' is the older "or later" shorthand, still accepted alongside the
# -or-later suffix form.
_ID_RE = re.compile(r"^[A-Za-z0-9.\-]+\+?$")
_REF_RE = re.compile(r"^(?:DocumentRef-[A-Za-z0-9.\-]+:)?LicenseRef-[A-Za-z0-9.\-]+$")
_OPERATORS = {"AND", "OR", "WITH"}


class SetupError(Exception):
    """The gate cannot run — never confused with a clean result."""


def load_spdx(root: Path) -> dict:
    """Read the bundled identifier sets, or refuse to run.

    A missing or malformed data file makes every recipe unverifiable. Exiting 2
    keeps that distinct from exit 0, which would claim a corpus was checked.
    """
    path = root / DATA_RELPATH
    if not path.is_file():
        raise SetupError(
            f"{DATA_RELPATH} is missing — the SPDX identifier sets this gate "
            f"validates against are not present, so nothing can be checked.")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise SetupError(f"{DATA_RELPATH} could not be read: {e}") from e

    for key in ("licenses", "exceptions", "deprecated_licenses", "upstream"):
        if key not in doc:
            raise SetupError(
                f"{DATA_RELPATH} has no {key!r} — it is not the shape this gate "
                f"reads; regenerate it per the file's own _regenerate note.")
    if not doc["licenses"] or not doc["exceptions"]:
        raise SetupError(
            f"{DATA_RELPATH} carries an empty identifier set — every recipe "
            f"would fail for the wrong reason.")
    return {
        "licenses": set(doc["licenses"]),
        "exceptions": set(doc["exceptions"]),
        "deprecated": set(doc["deprecated_licenses"]),
        "version": doc["upstream"].get("license_list_version", "unknown"),
        "release_date": doc["upstream"].get("release_date", "unknown"),
        "tag": doc["upstream"].get("tag", "unknown"),
    }


def check_expression(expr: str, spdx: dict) -> tuple[str | None, list[str]]:
    """Validate one licence expression.

    Returns (error, deprecated_ids). `error` is None when the expression is a
    valid SPDX expression whose every identifier is on the relevant list;
    otherwise it is a sentence naming the exact token that failed, so the
    finding is actionable without re-deriving anything.
    """
    if not isinstance(expr, str) or not expr.strip():
        return "declares no licence text at all", []

    spaced = expr.replace("(", " ( ").replace(")", " ) ")
    tokens = spaced.split()

    depth = 0
    for tok in tokens:
        if tok == "(":
            depth += 1
        elif tok == ")":
            depth -= 1
            if depth < 0:
                return "has an unbalanced ')'", []
    if depth != 0:
        return "has an unbalanced '('", []

    meaningful = [t for t in tokens if t not in ("(", ")")]
    if not meaningful:
        return "is only parentheses", []

    deprecated: list[str] = []
    expect_operand = True
    after_with = False
    for tok in meaningful:
        if expect_operand:
            if tok.upper() in _OPERATORS:
                return (f"has the operator {tok!r} where a licence was expected"), []
            if after_with:
                # The right operand of WITH comes from the EXCEPTION list.
                if not (_REF_RE.match(tok) or tok in spdx["exceptions"]):
                    return (f"uses {tok!r} after WITH, which is not on the SPDX "
                            f"licence-exception list"), []
            elif _REF_RE.match(tok):
                pass
            elif _ID_RE.match(tok):
                base = tok[:-1] if tok.endswith("+") else tok
                if base not in spdx["licenses"]:
                    return (f"uses {tok!r}, which is not an identifier on the "
                            f"SPDX licence list"), []
                if base in spdx["deprecated"]:
                    deprecated.append(base)
            else:
                return (f"uses {tok!r}, which is not a licence identifier, a "
                        f"LicenseRef- or an operator"), []
            expect_operand = False
            after_with = False
        else:
            if tok.upper() not in _OPERATORS:
                return (f"puts {tok!r} where AND, OR or WITH was expected — two "
                        f"licences separated by a bare space is not an "
                        f"expression"), []
            after_with = tok.upper() == "WITH"
            expect_operand = True

    if expect_operand:
        return "ends with an operator and no licence after it", []
    return None, deprecated


def scan(root: Path, spdx: dict, shipped_only: bool) -> tuple[list[dict], list[dict], dict]:
    """Walk every recipe. Returns (findings, warnings, counts)."""
    sys.path.insert(0, str(root))
    try:
        parse_template = importlib.import_module("igos-build.parser").parse_template
    except Exception as e:  # the parser is the build's own; without it, refuse
        raise SetupError(
            f"could not import igos-build.parser from {root}: {e}") from e

    findings: list[dict] = []
    warnings: list[dict] = []
    counts = {"scanned": 0, "shipped": 0, "mirror_only": 0, "exempt": 0,
              "checked": 0}

    for recipe in sorted(root.glob("packages/*/*/package.yml")):
        rel = recipe.relative_to(root).as_posix()
        key = "/".join(recipe.parts[-3:-1])
        counts["scanned"] += 1

        if key in EXEMPT_PACKAGES:
            counts["exempt"] += 1
            continue

        try:
            pkg = parse_template(str(recipe))
        except Exception as e:
            # Unreadable is a finding. The build's own parser rejected it, so
            # the licence cannot be established at all. It counts as CHECKED:
            # the recipe was examined and produced a verdict, and without this
            # a tree of nothing but unparseable recipes would exit 2 for an
            # empty scope and bury every finding it had just collected.
            counts["checked"] += 1
            findings.append({
                "recipe": rel, "shipped": None, "license": None,
                "problem": (f"could not be parsed by the build's own recipe "
                            f"parser ({type(e).__name__}: {e})"),
            })
            continue

        shipped = bool(getattr(pkg, "iso_include", False))
        counts["shipped" if shipped else "mirror_only"] += 1
        if shipped_only and not shipped:
            continue

        declared = getattr(pkg, "license", None)
        counts["checked"] += 1
        error, deprecated = check_expression(declared, spdx)
        if error is not None:
            findings.append({"recipe": rel, "shipped": shipped,
                             "license": declared, "problem": error})
        elif deprecated:
            warnings.append({"recipe": rel, "shipped": shipped,
                             "license": declared,
                             "deprecated": sorted(set(deprecated))})

    # A gate certifies only what it positively scanned. Exit 0 on an empty
    # inventory would read as "the whole tree is clean" while nothing was
    # examined — the failure shape the tree's gate-inventory rule exists to
    # stop. Refuse instead, and say which of the two emptinesses it was.
    if counts["scanned"] == 0:
        raise SetupError(
            f"no packages/*/*/package.yml found under {root} — a clean verdict "
            f"here would certify a corpus that was never read.")
    if counts["checked"] == 0:
        raise SetupError(
            f"scanned {counts['scanned']} recipe(s) but checked none of them "
            f"— every package was filtered out by the selected scope or the "
            f"exemption list, so there is nothing to certify.")
    return findings, warnings, counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(REPO_ROOT),
                    help="repository root to scan (default: this checkout)")
    ap.add_argument("--shipped-only", action="store_true",
                    help="check only packages the ISO ships; mirror-only "
                         "packages are still counted and reported as skipped")
    ap.add_argument("--quiet", action="store_true",
                    help="suppress the deprecated-identifier warnings (they "
                         "are still counted in the summary)")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    try:
        spdx = load_spdx(root)
        findings, warnings, counts = scan(root, spdx, args.shipped_only)
    except SetupError as e:
        print(f"[license-identifiers] SETUP ERROR: {e}", file=sys.stderr)
        return 2

    scope = "shipped packages only" if args.shipped_only else "every package"
    print(f"[license-identifiers] SPDX licence list {spdx['version']} "
          f"({spdx['tag']}, released {spdx['release_date']}) — "
          f"{len(spdx['licenses'])} identifiers, {len(spdx['exceptions'])} exceptions")
    print(f"[license-identifiers] scanned {counts['scanned']} recipe(s): "
          f"{counts['shipped']} shipped, {counts['mirror_only']} mirror-only, "
          f"{counts['exempt']} exempt — checked {counts['checked']} ({scope})")

    if warnings and not args.quiet:
        print(f"[license-identifiers] WARNING: {len(warnings)} recipe(s) declare "
              f"a DEPRECATED SPDX identifier. These pass — the identifier is on "
              f"the list — and are reported because the replacement resolves an "
              f"ambiguity only the package's own licence text can settle:")
        for w in warnings:
            where = "shipped" if w["shipped"] else "mirror-only"
            print(f"  {w['recipe']}  [{where}]")
            print(f"    declares  : {w['license']}")
            print(f"    deprecated: {', '.join(w['deprecated'])}")

    if not findings:
        print(f"[license-identifiers] PASS: every checked recipe declares a "
              f"valid SPDX licence expression"
              f"{f' ({len(warnings)} deprecated-identifier warning(s))' if warnings else ''}")
        return 0

    print(f"[license-identifiers] HALT: {len(findings)} recipe(s) declare a "
          f"licence that is not a valid SPDX expression:", file=sys.stderr)
    for f in findings:
        if f["shipped"] is None:
            where = "unreadable"
        else:
            where = "shipped" if f["shipped"] else "mirror-only"
        print(f"  {f['recipe']}  [{where}]", file=sys.stderr)
        if f["license"] is not None:
            print(f"    declares: {f['license']!r}", file=sys.stderr)
        print(f"    problem : {f['problem']}", file=sys.stderr)
    print("", file=sys.stderr)
    print("  license: is what the SBOM's licenseDeclared and the mirror index "
          "carry. A string that is not an SPDX identifier propagates into both "
          "and resolves for nobody.", file=sys.stderr)
    print("  Fix in the recipe: use the SPDX identifier for the licence the "
          "package actually carries, or — when SPDX has no identifier for it — "
          "LicenseRef-<Name>, which is SPDX's own way to say exactly that.",
          file=sys.stderr)
    print(f"  If the identifier is genuinely newer than the bundled list "
          f"({spdx['version']}), refresh {DATA_RELPATH} per its own "
          f"_regenerate note. Do not bypass the gate.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
