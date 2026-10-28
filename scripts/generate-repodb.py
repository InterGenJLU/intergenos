#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""E1.B.6 — InterGenOS.db index generator + signer.

Calls pkm.repo.generate_index() + sign_index() library functions to
produce the signed repository index that pkm clients fetch.

Usage:
    python3 scripts/generate-repodb.py /var/lib/igos/archives/
    python3 scripts/generate-repodb.py --gpg-key S2 /path/to/archives/
    python3 scripts/generate-repodb.py --no-sign /path/to/archives/
    python3 scripts/generate-repodb.py --sources-dir build/sources-archives \
        /var/lib/igos/archives/

GPG keys (canonical names from pkm/release-keys.json):
    S1 — primary signing (Nitrokey NK1)  — also accepts legacy alias 'NK1'
    S2 — off-site backup (Nitrokey NK2) — also accepts legacy alias 'NK2'

Required files in package_dir:
    *.igos.tar.gz — per-package archives (output of E1.B.5 emit-package-archives.py)

Read from --sources-dir (default <package_dir>/sources):
    *.igos.src.tar.gz — corresponding-source archives (output of
    scripts/build-source-archives.py). Every entry whose archive is present
    gains source_filename + source_sha256, which is what SOURCES.md section 4
    tells a user to verify a source archive against. Packages with no archive
    gain neither field and are COUNTED AND NAMED in this script's summary, so
    the absence is visible rather than a silent null in the signed index.

Outputs:
    InterGenOS.db — gzipped JSON index (pkm fetchable)
    InterGenOS.db.sig — PGP detached signature (verified by pkm before install)

Library functions at pkm/repo.py:414 (generate_index) and :467 (sign_index).
Schema documented at pkm/repo.py:14-38.
"""

import gzip
import json
import sys
from pathlib import Path

# Ensure pkm module is importable when run from project root
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from pkm.repo import generate_index, sign_index


def _load_release_keys():
    """Load release key fingerprints from pkm/release-keys.json.

    Canonical source: pkm/release-keys.json (referenced by docs/signing-key.md).
    Returns dict of {key_name: fingerprint, ...} including both canonical
    names (S1/S2) and legacy aliases (NK1/NK2).
    """
    config_path = _project_root / "pkm" / "release-keys.json"
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        data = json.load(f)
    keys = {}
    for name, info in data.get("keys", {}).items():
        keys[name] = info["fingerprint"]
        for alias in info.get("aliases", []):
            keys[alias] = info["fingerprint"]
    return keys


def _report_source_archives(index_path, sources_dir):
    """Name and count the corresponding-source archives the index records.

    Reads the WRITTEN index, so what is reported is what was signed. Prints the
    sources directory used, how many of the entries carry the two source
    fields, and the name of every package that carries neither. A run whose
    sources directory does not exist says so and states the zero explicitly: a
    publish that quietly recorded nothing about sources, while SOURCES.md
    promises the index records them, is the failure this summary exists to make
    impossible to miss.
    """
    with gzip.open(index_path, "rt", encoding="utf-8") as f:
        written = json.load(f)
    entries = written.get("packages", {})
    recorded = sorted(n for n, e in entries.items() if "source_sha256" in e)
    missing = sorted(n for n in entries if n not in set(recorded))

    print(f"Corresponding-source archives read from {sources_dir}", file=sys.stderr)
    if not Path(sources_dir).is_dir():
        print(
            f"WARNING: no such sources directory: {sources_dir} — 0 of "
            f"{len(entries)} entries carry source_filename/source_sha256. "
            f"SOURCES.md section 4 documents that this index records them; "
            f"generate them with scripts/build-source-archives.py and pass "
            f"--sources-dir.",
            file=sys.stderr,
        )
    print(
        f"Source archive recorded for {len(recorded)} of {len(entries)} packages",
        file=sys.stderr,
    )
    if missing:
        print(
            f"  {len(missing)} with no corresponding-source archive: "
            f"{', '.join(missing)}",
            file=sys.stderr,
        )


def load_security_advisories(path, *, required=False):
    """Load curated advisories; required inputs fail before index generation."""
    advisories = {}
    if path:
        adv_path = Path(path)
        if required or adv_path.exists():
            try:
                import yaml
            except ImportError:
                print(
                    "ERROR: PyYAML is required to parse security-advisories.yml. "
                    "Install via the build host's package manager (the "
                    "generate-repodb.py command line alone also accepts "
                    "--security-advisories '' to disable advisory stamping).",
                    file=sys.stderr,
                )
                sys.exit(1)
            try:
                with open(adv_path) as f:
                    advisories = yaml.safe_load(f)
            except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
                reason = " ".join(str(e).splitlines())
                print(
                    f"ERROR: cannot load {adv_path}: {reason}",
                    file=sys.stderr,
                )
                sys.exit(1)
            if advisories is None:
                advisories = {}
            if not isinstance(advisories, dict):
                print(f"ERROR: {adv_path}: expected a mapping", file=sys.stderr)
                sys.exit(1)
            for key, entry in advisories.items():
                if not isinstance(entry, dict):
                    reason = "expected a mapping"
                elif entry.get("cves") and (
                    not isinstance(entry["cves"], list)
                    or any(not isinstance(cve, str) or not cve for cve in entry["cves"])
                ):
                    reason = "cves must be a list of non-empty strings"
                else:
                    continue
                location = " ".join(f"{adv_path}: {key}".splitlines())
                print(f"ERROR: {location}: {reason}", file=sys.stderr)
                sys.exit(1)
            advisory_count = sum(
                1 for v in advisories.values()
                if isinstance(v, dict) and v.get("cves")
            )
            print(
                f"Loaded {advisory_count} security advisory entries from {adv_path}",
                file=sys.stderr,
            )
        else:
            print(
                f"WARNING: --security-advisories {adv_path} not found; "
                f"security: false on all entries.",
                file=sys.stderr,
            )

    return advisories


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="E1.B.6 — Generate and sign InterGenOS.db repository index"
    )
    parser.add_argument(
        "package_dir",
        help="Directory containing .igos.tar.gz archives",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output path for InterGenOS.db (default: <package_dir>/InterGenOS.db)",
    )
    parser.add_argument(
        "--gpg-key", default="S1",
        help="Release key to sign with (default: S1, canonical names from pkm/release-keys.json)",
    )
    parser.add_argument("--no-sign", action="store_true", help="Skip signing")
    parser.add_argument(
        "--sources-dir",
        help="Directory holding the corresponding-source archives "
             "(<name>-<version>-<release>.igos.src.tar.gz, as emitted by "
             "scripts/build-source-archives.py). Default: <package_dir>/sources, "
             "which is where the published layout puts them. Each index entry "
             "whose archive is present records its source_filename and "
             "source_sha256.",
    )
    parser.add_argument(
        "--security-advisories",
        default=str(_project_root / "docs" / "governance" / "security-advisories.yml"),
        help="Path to hand-curated security advisories YAML (Q7 / O-030). "
             "Default: docs/governance/security-advisories.yml. Pass empty "
             "string to disable advisory stamping.",
    )

    args = parser.parse_args()

    release_keys = _load_release_keys()
    if not release_keys:
        print("ERROR: pkm/release-keys.json not found or empty", file=sys.stderr)
        sys.exit(1)

    key_choices = sorted(release_keys.keys())
    # Add --gpg-key choices dynamically
    parser_help = argparse.ArgumentParser()
    # Re-parse with choices now known (for accurate usage message)
    # We already parsed; just validate key
    gpg_key = args.gpg_key.upper()
    if gpg_key not in release_keys:
        print(f"ERROR: unknown GPG key '{args.gpg_key}'. Valid: {', '.join(key_choices)}", file=sys.stderr)
        sys.exit(1)

    package_dir = Path(args.package_dir)
    if not package_dir.is_dir():
        print(f"ERROR: {package_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    archives = list(package_dir.glob("*.igos.tar.gz"))
    if not archives:
        print(f"WARNING: no .igos.tar.gz files in {package_dir}", file=sys.stderr)

    output = Path(args.output) if args.output else None

    advisories = load_security_advisories(args.security_advisories)

    # Step 1: Generate index
    sources_dir = Path(args.sources_dir) if args.sources_dir else package_dir / "sources"
    print(f"Generating index from {len(archives)} archives...", file=sys.stderr)
    index_path = generate_index(
        str(package_dir), arch="x86_64", output=output,
        security_advisories=advisories, sources_dir=str(sources_dir),
    )
    index_path = Path(index_path)
    print(f"Index: {index_path} ({index_path.stat().st_size} bytes)", file=sys.stderr)

    # Step 1b: the corresponding-source summary, READ BACK OUT OF THE FILE that
    # was just written rather than out of what was passed in. The index is the
    # artifact; anything else is an echo of an intention. A package with no
    # source archive carries no source field at all, so the only way that
    # absence is visible is this summary naming it.
    _report_source_archives(index_path, sources_dir)

    # Step 2: Sign
    if not args.no_sign:
        gpg_fp = release_keys[gpg_key]
        canonical_name = args.gpg_key.upper()
        print(f"Signing with {canonical_name} ({gpg_fp[:16]}...)...", file=sys.stderr)
        sig_path = sign_index(str(index_path), gpg_key_id=gpg_fp)
        sig_path = Path(sig_path)
        print(f"Signature: {sig_path} ({sig_path.stat().st_size} bytes)", file=sys.stderr)

    print("Done.", file=sys.stderr)


if __name__ == "__main__":
    main()
