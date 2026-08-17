#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""SBOM generator for the InterGenOS ISO's shipped package set.

Emits a SPDX 2.3 JSON document enumerating every package the ISO actually
ships — the post-curation ``iso_include`` subset, NOT the full package
corpus. Companion to scripts/shim-sbom-gen.py, which does the same job at
the shim-binary scope; this one operates at the ISO-artifact scope.

WHAT "SHIPPED" MEANS, AND WHERE THAT TRUTH LIVES
------------------------------------------------
``iso_include`` is not a field this script interprets. It is resolved by
``igos-build/parser.py`` at parse time, which applies the tier-based
default (``tier: extra`` and ``tier: compute`` default to MIRROR; every
other tier defaults to shipped) and rejects a non-boolean override. This
generator imports that parser and reads ``Package.iso_include``, exactly
as scripts/derive-iso-exclusions.py does when it tells ``pkm iso-prep``
which packages to evict from the chroot.

That import is the point. The shipped set in this SBOM and the shipped set
the build pipeline produces are the same computation, so they cannot drift.
A hand-maintained list or a filename glob could each be wrong while looking
right; deriving from the declaration cannot. (This is the same rule that
corrected an earlier build gate: prefer the set a thing declares about
itself over the set a pattern happens to match.)

FAIL-CLOSED
-----------
A package that cannot be identified is a loud refusal naming it, never a
silent skip. Refusals are collected so one run reports every bad package
rather than stopping at the first, and then the exit status is non-zero and
NO document is written. An SBOM that quietly omits a shipped package is
worse than no SBOM: it is a claim of completeness that is false.

Refusal conditions:
  * ``package.yml`` does not parse, or the parser rejects a field;
  * name, version, release or license is missing or empty after parsing;
  * ``--require-archives`` was given and a shipped package has no staged
    archive to hash.

A shipped package with no staged archive is NOT a refusal by default: this
generator can legitimately run before or without the archive corpus (for
review, or in CI on a checkout). It is instead recorded honestly — no
checksum, and a comment saying the archive was not present at generation —
and counted in the summary, so "how many entries carry a hash" is always
visible rather than implied. ``--require-archives`` is the release-gate
posture, where an unhashed entry should stop the line.

LICENSES ARE REPRESENTED, NOT COERCED
-------------------------------------
``licenseDeclared`` carries what ``package.yml`` declares. Most of the tree
declares a valid SPDX expression and it passes through unchanged. Some
declarations are not SPDX identifiers — ``Various (redistributable)`` for
linux-firmware's mixed vendor blobs, for instance. Those become a
``LicenseRef-`` with the raw text preserved in ``extractedLicensingInfos``,
which is what SPDX provides for exactly this case and is already the
in-tree pattern (one package declares
``BSD-3-Clause AND LicenseRef-Intel-SOF-Binary``).

The alternative — mapping an unrecognised declaration to ``NOASSERTION`` —
is rejected deliberately. It would render a document that validates while
having silently dropped the one fact a licence audit needs.

LIMIT, STATED PLAINLY: this validates the SHAPE of an SPDX expression
(identifier tokens joined by AND / OR / WITH, optional parentheses, a
trailing ``+``, and ``LicenseRef-`` / ``DocumentRef-`` tokens). It does NOT
check membership in the SPDX licence list, because a bundled copy of that
list goes stale silently and a network fetch has no place in a generator.
So a well-formed-but-misspelled identifier passes through as declared. A
list-membership check belongs in the package-metadata lint that owns
license: as a field, not here — see the delivery's surfaced findings.

DETERMINISM
-----------
Two runs over an unchanged tree produce byte-identical output, provided
``created`` is fixed: pass ``--created``, or set ``SOURCE_DATE_EPOCH`` (the
same knob build-iso.sh and the source-archive writer use) and it is derived
from that. Package order is sorted by SPDXID, and ``documentNamespace``
carries a digest of the enumerated identity set, so the namespace changes
when and only when the contents do.

Usage:
    scripts/iso-sbom-gen.py --output build/iso-sbom.spdx.json
    scripts/iso-sbom-gen.py --output /tmp/sbom.json --archives /mnt/igos/var/lib/igos/archives
    scripts/iso-sbom-gen.py --output /tmp/sbom.json --require-archives
    SOURCE_DATE_EPOCH=0 scripts/iso-sbom-gen.py --output /tmp/sbom.json

Exit 0 on success; 1 if any shipped package was refused; 2 on bad invocation.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import importlib
import json
import os
import re
import sys
from pathlib import Path

# Import the in-tree parser so iso_include resolution and field validation are
# the build's own, never replicated here. The package directory name contains a
# hyphen, so a normal import statement cannot reach it — same importlib route
# scripts/derive-iso-exclusions.py uses.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
_parser_mod = importlib.import_module("igos-build.parser")
parse_template = _parser_mod.parse_template

TOOL_NAME = "scripts/iso-sbom-gen.py-1.0"
MIRROR_BASE = "https://repo.intergenos.org/x86_64/current"

# One SPDX licence-expression token: an identifier, or a LicenseRef/DocumentRef.
# Identifiers may carry a trailing '+' (the "or-later" shorthand SPDX still
# accepts alongside the -or-later suffix form).
_LICENSE_ID_RE = re.compile(r"^[A-Za-z0-9.\-]+\+?$")
_LICENSE_REF_RE = re.compile(r"^(?:DocumentRef-[A-Za-z0-9.\-]+:)?LicenseRef-[A-Za-z0-9.\-]+$")
_LICENSE_OPERATORS = {"AND", "OR", "WITH"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def spdx_id_fragment(text: str) -> str:
    """Reduce arbitrary text to the character set SPDX allows in an SPDXID.

    SPDX restricts an SPDXID's tail to letters, digits, '.' and '-'. Package
    names in this tree are already within that set; this exists so a
    LicenseRef built from a free-text declaration cannot emit an invalid id.
    Empty input would produce an invalid bare 'LicenseRef-', so it is refused
    by the caller rather than papered over here.
    """
    cleaned = re.sub(r"[^A-Za-z0-9.\-]+", "-", text).strip("-")
    return re.sub(r"-{2,}", "-", cleaned)


def license_expression_is_wellformed(expr: str) -> bool:
    """True if `expr` parses as an SPDX licence expression by SHAPE.

    Checks token structure only — identifiers joined by AND/OR/WITH with
    balanced parentheses. Deliberately does NOT check that each identifier is
    on the SPDX licence list; see the module docstring for why, and for what
    that means about a misspelled identifier.
    """
    if not expr or not expr.strip():
        return False

    # Parentheses must balance, and are separated out so the tokens between
    # them can be inspected on their own.
    spaced = expr.replace("(", " ( ").replace(")", " ) ")
    tokens = spaced.split()
    depth = 0
    for tok in tokens:
        if tok == "(":
            depth += 1
        elif tok == ")":
            depth -= 1
            if depth < 0:
                return False
    if depth != 0:
        return False

    # Ignoring parentheses, the sequence must alternate operand, operator,
    # operand ... — a bare space between two operands (the "Public Domain"
    # shape) is exactly what this rejects.
    meaningful = [t for t in tokens if t not in ("(", ")")]
    if not meaningful:
        return False
    expect_operand = True
    for tok in meaningful:
        if expect_operand:
            if tok.upper() in _LICENSE_OPERATORS:
                return False
            if not (_LICENSE_ID_RE.match(tok) or _LICENSE_REF_RE.match(tok)):
                return False
            expect_operand = False
        else:
            if tok.upper() not in _LICENSE_OPERATORS:
                return False
            expect_operand = True
    # A trailing operator leaves us still expecting an operand.
    return not expect_operand


def license_refs_in(expr: str) -> list[str]:
    """Every LicenseRef- token appearing in a well-formed expression.

    SPDX requires each LicenseRef used to be defined in
    extractedLicensingInfos, so these have to be collected and declared even
    when the surrounding expression passed through untouched.
    """
    out = []
    for tok in expr.replace("(", " ").replace(")", " ").split():
        if _LICENSE_REF_RE.match(tok):
            out.append(tok.split(":")[-1])
    return sorted(set(out))


def resolve_license(declared: str) -> tuple[str, list[dict]]:
    """Map a package.yml `license:` declaration to an SPDX expression.

    Returns (expression, extracted_licensing_infos). A declaration that is a
    well-formed SPDX expression is returned as-is; any LicenseRef it names is
    still declared, because SPDX requires that. A declaration that is not
    well-formed becomes a single LicenseRef whose extractedText is the raw
    declaration, so nothing about what the package said is lost.
    """
    declared = declared.strip()
    if license_expression_is_wellformed(declared):
        infos = [
            {
                "licenseId": ref,
                "name": ref.removeprefix("LicenseRef-").replace("-", " "),
                "extractedText": (
                    f"Declared in package.yml as part of the licence expression "
                    f"{declared!r}. This reference is not an SPDX licence-list "
                    f"identifier; the package's own licence text ships with the "
                    f"package."
                ),
            }
            for ref in license_refs_in(declared)
        ]
        return declared, infos

    fragment = spdx_id_fragment(declared)
    ref = f"LicenseRef-{fragment}"
    info = {
        "licenseId": ref,
        "name": declared,
        "extractedText": (
            f"package.yml declares this package's licence as {declared!r}, which "
            f"is not a well-formed SPDX licence expression. It is recorded here "
            f"verbatim rather than replaced with NOASSERTION, so the declaration "
            f"is preserved exactly as the package states it."
        ),
    }
    return ref, [info]


class Refusal:
    """One shipped package this generator will not describe, and why.

    Carried as data rather than raised, so a single run names every bad
    package instead of stopping at the first one.
    """

    def __init__(self, path: Path, reason: str, name: str | None = None):
        self.path = path
        self.reason = reason
        self.name = name

    def __str__(self) -> str:
        who = self.name or "(unidentified)"
        return f"{who} [{self.path}]: {self.reason}"


def derive_shipped_set(packages_dir: Path) -> tuple[list, list[Refusal], int]:
    """Enumerate the packages the ISO ships.

    Returns (shipped, refusals, mirror_count). `shipped` holds parsed Package
    objects with iso_include True, sorted by name. A package.yml that does not
    parse is a REFUSAL, not a warning: whether it ships is undecidable, and an
    undecidable package must not be silently absent from a completeness claim.
    """
    shipped = []
    refusals: list[Refusal] = []
    mirror_count = 0

    for tier_dir in sorted(packages_dir.iterdir()):
        if not tier_dir.is_dir():
            continue
        for pkg_dir in sorted(tier_dir.iterdir()):
            yml = pkg_dir / "package.yml"
            if not yml.is_file():
                continue
            try:
                pkg = parse_template(yml)
            except Exception as exc:
                refusals.append(Refusal(
                    yml,
                    f"package.yml does not parse ({type(exc).__name__}: {exc}) — "
                    f"cannot tell whether this package ships",
                ))
                continue

            if not pkg.iso_include:
                mirror_count += 1
                continue

            missing = [
                field for field in ("name", "version", "license")
                if not str(getattr(pkg, field, "") or "").strip()
            ]
            if getattr(pkg, "release", None) is None:
                missing.append("release")
            if missing:
                refusals.append(Refusal(
                    yml,
                    f"shipped package is missing required identity: "
                    f"{', '.join(missing)}",
                    getattr(pkg, "name", None),
                ))
                continue
            shipped.append(pkg)

    shipped.sort(key=lambda p: (p.name, p.version))
    return shipped, refusals, mirror_count


def archive_basename(name: str, version: str) -> str:
    """The binary archive filename for a package.

    ``<name>-<version>.igos.tar.gz`` — no release component. That is what
    igos-build/tracker.py, igos-build/builder.py and
    scripts/emit-package-archives.py all compose, so it is what a staged
    archive is actually called. (The corresponding-SOURCE archive DOES carry
    the release: ``<name>-<version>-<release>.igos.src.tar.gz``. The two
    conventions differ, and using the source one here would look for files
    that do not exist.)
    """
    return f"{name}-{version}.igos.tar.gz"


def ship_name(pkg) -> str:
    """The name a package ships under: ``ships_as`` when declared, else name.

    The ch8 dual-name twins (glibc-core -> glibc, gcc-core -> gcc, ...) are
    archived, installed, and published under their ``ships_as`` name — the
    same ships_as-first resolution gen-pkginfo applies to .PKGINFO stamping.
    Describing them under the recipe name would both miss their real staged
    archives and state a package name the ISO does not actually carry.
    """
    return getattr(pkg, "ships_as", None) or pkg.name


def build_package_entry(pkg, archives_dir: Path | None) -> tuple[dict, list[dict], bool]:
    """Build one SPDX package entry. Returns (entry, licensing_infos, hashed)."""
    expr, infos = resolve_license(pkg.license)
    shipped_name = ship_name(pkg)
    basename = archive_basename(shipped_name, pkg.version)

    checksums: list[dict] = []
    hashed = False
    archive_note = "no staged archive was present when this SBOM was generated"
    if archives_dir is not None:
        candidate = archives_dir / basename
        if candidate.is_file():
            checksums.append({
                "algorithm": "SHA256",
                "checksumValue": sha256_file(candidate),
            })
            hashed = True
            archive_note = f"sha256 taken from the staged archive {basename}"

    comment_parts = [
        f"tier {pkg.tier}; ships in the ISO because iso_include resolves True "
        f"(igos-build/parser.py). Archive {basename}; {archive_note}.",
    ]
    if shipped_name != pkg.name:
        comment_parts.append(
            f"Built from recipe '{pkg.name}' and shipped as '{shipped_name}' "
            f"(the recipe's ships_as declaration)."
        )
    comment_parts += [
        f"Release {pkg.release} is package metadata, not part of the binary "
        f"archive filename.",
    ]
    payload_license = getattr(pkg, "payload_license", None)
    if payload_license:
        comment_parts.append(
            f"This package fetches a payload at install time whose licence is "
            f"declared separately as {payload_license!r}; the payload is not "
            f"redistributed by InterGenOS and is not part of this package's "
            f"own bytes."
        )

    entry = {
        "SPDXID": f"SPDXRef-Package-{spdx_id_fragment(shipped_name)}",
        "name": shipped_name,
        "versionInfo": f"{pkg.version}-{pkg.release}",
        "supplier": "Organization: InterGenOS",
        "downloadLocation": f"{MIRROR_BASE}/{basename}",
        "filesAnalyzed": False,
        "checksums": checksums,
        "licenseConcluded": "NOASSERTION",
        "licenseDeclared": expr,
        "copyrightText": "NOASSERTION",
        "comment": " ".join(comment_parts),
    }
    return entry, infos, hashed


def build_spdx_doc(
    shipped: list,
    archives_dir: Path | None,
    created_iso: str,
    iso_tag: str,
) -> tuple[dict, int]:
    """Assemble the SPDX 2.3 document. Returns (doc, packages_with_hashes)."""
    packages: list[dict] = []
    licensing: dict[str, dict] = {}
    hashed_count = 0

    for pkg in shipped:
        entry, infos, hashed = build_package_entry(pkg, archives_dir)
        packages.append(entry)
        hashed_count += 1 if hashed else 0
        for info in infos:
            licensing.setdefault(info["licenseId"], info)

    packages.sort(key=lambda p: p["SPDXID"])

    root = {
        "SPDXID": "SPDXRef-Package-intergenos-iso",
        "name": iso_tag,
        "versionInfo": iso_tag,
        "supplier": "Organization: InterGenOS",
        "downloadLocation": "NOASSERTION",
        "filesAnalyzed": False,
        "checksums": [],
        "licenseConcluded": "NOASSERTION",
        "licenseDeclared": "NOASSERTION",
        "copyrightText": "NOASSERTION",
        "comment": (
            f"The InterGenOS ISO artifact. Contains the {len(packages)} packages "
            f"enumerated in this document — the post-curation iso_include set, "
            f"not the full package corpus. Mirror-only packages are deliberately "
            f"absent; scripts/derive-iso-exclusions.py evicts them from the "
            f"chroot before the squashfs is assembled."
        ),
    }

    # The namespace tracks CONTENT: same identity set in, same namespace out;
    # any package added, removed or re-versioned changes it. Built from the
    # identity tuples rather than the rendered JSON so that a formatting change
    # to this generator does not read as a content change.
    identity_digest = sha256_bytes(
        "\n".join(
            f"{p['name']}\t{p['versionInfo']}\t{p['licenseDeclared']}"
            for p in packages
        ).encode("utf-8")
    )

    relationships = [{
        "spdxElementId": "SPDXRef-DOCUMENT",
        "relationshipType": "DESCRIBES",
        "relatedSpdxElement": root["SPDXID"],
    }]
    relationships.extend(
        {
            "spdxElementId": root["SPDXID"],
            "relationshipType": "CONTAINS",
            "relatedSpdxElement": entry["SPDXID"],
        }
        for entry in packages
    )

    doc = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"InterGenOS ISO {iso_tag}",
        "documentNamespace": (
            f"https://intergenstudios.com/sbom/iso/{iso_tag}-{identity_digest[:16]}"
        ),
        "creationInfo": {
            "created": created_iso,
            "creators": [
                f"Tool: {TOOL_NAME}",
                "Organization: InterGenOS",
            ],
            "licenseListVersion": "3.24",
        },
        "packages": [root] + packages,
        "relationships": relationships,
    }
    if licensing:
        doc["hasExtractedLicensingInfos"] = [
            licensing[k] for k in sorted(licensing)
        ]
    return doc, hashed_count


def default_created() -> str:
    """Creation timestamp: SOURCE_DATE_EPOCH when set, else now.

    Honouring SOURCE_DATE_EPOCH is what makes byte-identical re-runs possible
    without the caller having to pass --created; defaulting to "now" otherwise
    keeps an ordinary run honest about when it ran.
    """
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch:
        try:
            when = datetime.datetime.fromtimestamp(
                int(epoch), datetime.timezone.utc)
        except (ValueError, OverflowError, OSError):
            raise SystemExit(
                f"ERROR: SOURCE_DATE_EPOCH is not a usable unix timestamp: {epoch!r}")
        return when.strftime("%Y-%m-%dT%H:%M:%SZ")
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--output", required=True, type=Path,
                    help="Output SPDX 2.3 JSON file path")
    ap.add_argument("--repo-root", type=Path, default=_PROJECT_ROOT,
                    help="Repo root holding packages/ (default: this script's repo)")
    ap.add_argument("--packages", type=Path, default=None,
                    help="Packages dir (default: <repo-root>/packages)")
    ap.add_argument("--archives", type=Path, default=None,
                    help="Directory of staged <name>-<version>.igos.tar.gz archives "
                         "to hash. Omit to emit identity without checksums.")
    ap.add_argument("--require-archives", action="store_true",
                    help="Refuse any shipped package with no staged archive. The "
                         "release-gate posture; off by default so the generator "
                         "can run on a bare checkout.")
    ap.add_argument("--iso-tag", default="intergenos-iso",
                    help="Identifier for the ISO this SBOM describes")
    ap.add_argument("--created",
                    help="ISO 8601 UTC creation timestamp. Default: from "
                         "SOURCE_DATE_EPOCH if set, else now.")
    args = ap.parse_args(argv)

    packages_dir = args.packages or (args.repo_root / "packages")
    if not packages_dir.is_dir():
        print(f"ERROR: packages dir not found: {packages_dir}", file=sys.stderr)
        return 2
    if args.archives is not None and not args.archives.is_dir():
        print(f"ERROR: --archives is not a directory: {args.archives}",
              file=sys.stderr)
        return 2
    if args.require_archives and args.archives is None:
        print("ERROR: --require-archives needs --archives <dir> to check against",
              file=sys.stderr)
        return 2

    shipped, refusals, mirror_count = derive_shipped_set(packages_dir)

    if args.require_archives:
        for pkg in shipped:
            candidate = args.archives / archive_basename(ship_name(pkg), pkg.version)
            if not candidate.is_file():
                refusals.append(Refusal(
                    packages_dir / pkg.tier / pkg.name / "package.yml",
                    f"--require-archives: no staged archive "
                    f"{archive_basename(ship_name(pkg), pkg.version)}",
                    pkg.name,
                ))
        refused = {r.name for r in refusals if r.name}
        shipped = [p for p in shipped if p.name not in refused]

    if refusals:
        print(f"REFUSING to write an SBOM: {len(refusals)} shipped package(s) "
              f"could not be described.", file=sys.stderr)
        for refusal in refusals:
            print(f"  REFUSED {refusal}", file=sys.stderr)
        print("An SBOM that omits a shipped package states a completeness it "
              "does not have; fix the packages above and re-run.",
              file=sys.stderr)
        return 1

    if not shipped:
        print(f"REFUSING to write an SBOM: no shipped packages found under "
              f"{packages_dir}. An empty document would assert that the ISO "
              f"ships nothing.", file=sys.stderr)
        return 1

    created = args.created or default_created()
    doc, hashed_count = build_spdx_doc(
        shipped, args.archives, created, args.iso_tag)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(doc, indent=2, sort_keys=False) + "\n", encoding="utf-8")

    print(f"Wrote SPDX 2.3 JSON to {args.output}")
    print(f"  shipped packages described : {len(shipped)}")
    print(f"  mirror-only, excluded      : {mirror_count}")
    print(f"  entries carrying a sha256  : {hashed_count} of {len(shipped)}")
    if args.archives is None:
        print("  (no --archives given, so no entry carries a checksum)")
    elif hashed_count != len(shipped):
        print(f"  NOTE: {len(shipped) - hashed_count} shipped package(s) had no "
              f"staged archive; each entry says so in its comment. Use "
              f"--require-archives to make that a refusal.")
    print(f"  non-SPDX licence refs      : "
          f"{len(doc.get('hasExtractedLicensingInfos', []))}")
    print(f"  relationships              : {len(doc['relationships'])}")
    print(f"  created                    : {created}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
