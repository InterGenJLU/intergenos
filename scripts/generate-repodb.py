#!/usr/bin/env python3
"""E1.B.6 — InterGenOS.db index generator + signer.

Calls pkm.repo.generate_index() + sign_index() library functions to
produce the signed repository index that pkm clients fetch.

Usage:
    python3 scripts/generate-repodb.py /var/lib/igos/archives/
    python3 scripts/generate-repodb.py --gpg-key NK2 /path/to/archives/
    python3 scripts/generate-repodb.py --no-sign /path/to/archives/

Required files in package_dir:
    *.igos.tar.gz — per-package archives (output of E1.B.5 emit-package-archives.py)

Outputs:
    InterGenOS.db — gzipped JSON index (pkm fetchable)
    InterGenOS.db.sig — PGP detached signature (verified by pkm before install)

Library functions at pkm/repo.py:414 (generate_index) and :467 (sign_index).
Schema documented at pkm/repo.py:14-38.
"""

import sys
from pathlib import Path

# Ensure pkm module is importable when run from project root
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from pkm.repo import generate_index, sign_index


RELEASE_KEYS = {
    "NK1": "D7AA641D81ACD690C5AD865E7276E14DD8886BFE",
    "NK2": "81DD223F9BA9B3F2AFBFFC5AFA24B042975F775E",
    "S1": "D7AA641D81ACD690C5AD865E7276E14DD8886BFE",
    "S2": "81DD223F9BA9B3F2AFBFFC5AFA24B042975F775E",
}


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
        "--gpg-key", default="NK1",
        choices=list(RELEASE_KEYS.keys()),
        help="Release key to sign with (default: NK1)",
    )
    parser.add_argument("--no-sign", action="store_true", help="Skip signing")

    args = parser.parse_args()

    package_dir = Path(args.package_dir)
    if not package_dir.is_dir():
        print(f"ERROR: {package_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    archives = list(package_dir.glob("*.igos.tar.gz"))
    if not archives:
        print(f"WARNING: no .igos.tar.gz files in {package_dir}", file=sys.stderr)

    output = Path(args.output) if args.output else None

    # Step 1: Generate index
    print(f"Generating index from {len(archives)} archives...", file=sys.stderr)
    index_path = generate_index(str(package_dir), arch="x86_64", output=output)
    index_path = Path(index_path)
    print(f"Index: {index_path} ({index_path.stat().st_size} bytes)", file=sys.stderr)

    # Step 2: Sign
    if not args.no_sign:
        gpg_fp = RELEASE_KEYS[args.gpg_key.upper()]
        print(f"Signing with {args.gpg_key} ({gpg_fp[:16]}...)...", file=sys.stderr)
        sig_path = sign_index(str(index_path), gpg_key_id=gpg_fp)
        sig_path = Path(sig_path)
        print(f"Signature: {sig_path} ({sig_path.stat().st_size} bytes)", file=sys.stderr)

    print("Done.", file=sys.stderr)


if __name__ == "__main__":
    main()
