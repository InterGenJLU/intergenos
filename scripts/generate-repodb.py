#!/usr/bin/env python3
"""InterGenOS.db index generator + signer — E1.B.6

Self-contained tool that walks a package directory, reads metadata from
each .igos.tar.gz archive, generates the gzipped JSON repository index,
and PGP-signs it with the release key.

Usage:
    python3 scripts/generate-repodb.py /path/to/packages/
    python3 scripts/generate-repodb.py --gpg-key NK2 /path/to/packages/
    python3 scripts/generate-repodb.py --output /tmp/InterGenOS.db --no-sign /path/to/packages/

Index schema matches pkm/repo.py parser. See pkm/repo.py for the full
format documentation and pkm-side verification.
"""

import argparse
import gzip
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Release key fingerprints
RELEASE_KEYS = {
    "NK1": "D7AA641D81ACD690C5AD865E7276E14DD8886BFE",
    "NK2": "81DD223F9BA9B3F2AFFBFC5AFA24B042975F775E",
    # Aliases
    "S1": "D7AA641D81ACD690C5AD865E7276E14DD8886BFE",
    "S2": "81DD223F9BA9B3F2AFFBFC5AFA24B042975F775E",
}
DEFAULT_KEY = "NK1"


def _sha256_file(path):
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _read_package_meta(archive_path):
    """Read metadata from .PKGINFO or metadata.json inside a .igos.tar.gz."""
    try:
        result = subprocess.run(
            ["tar", "-xf", str(archive_path), "-O", ".PKGINFO"],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout:
            return _parse_pkginfo(result.stdout)

        result = subprocess.run(
            ["tar", "-xf", str(archive_path), "-O", "metadata.json"],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout:
            return json.loads(result.stdout)
    except Exception:
        pass
    return None


def _parse_pkginfo(text):
    """Parse .PKGINFO key=value format (Arch-style)."""
    meta = {"depends": []}
    for line in text.strip().splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key == "depend":
            meta["depends"].append(value)
        elif key == "pkgname":
            meta["name"] = value
        elif key == "pkgver":
            meta["version"] = value
        elif key == "pkgdesc":
            meta["description"] = value
        elif key in ("license", "tier", "builddate", "size"):
            if key == "builddate":
                key = "build_date"
            elif key == "size":
                key = "installed_size"
                value = int(value)
            meta[key] = value
    return meta


def _package_name_from_filename(filename):
    """Derive package name from .igos.tar.gz filename.

    Filenames follow: pkgname-version-release.igos.tar.gz
    The package name is everything before the last two dash-separated segments
    (version and release). Examples:
      firefox-138.0-1.igos.tar.gz          → firefox
      gcc-pass1-14.2.0-1.igos.tar.gz       → gcc-pass1
      systemd-pass2-259.1-1.igos.tar.gz    → systemd-pass2
    """
    name = filename.replace(".igos.tar.gz", "")
    parts = name.rsplit("-", 2)
    if len(parts) >= 3:
        return parts[0]
    return name


def _build_manifest(archive_path):
    """Build a signed manifest entry for a single package archive.

    Computes SHA-256 of the archive file, writes it alongside the archive
    as <filename>.sha256, then PGP clear-signs it as <filename>.manifest.

    Returns (sha256_hex, manifest_path) or raises on failure.
    """
    sha = _sha256_file(archive_path)
    sha_path = Path(str(archive_path) + ".sha256")

    # Write plain SHA-256 hash file
    sha_path.write_text(f"{sha}  {archive_path.name}\n")

    # PGP clear-sign it
    manifest_path = Path(str(archive_path) + ".manifest")
    result = subprocess.run(
        ["gpg", "--clearsign", "--output", str(manifest_path), str(sha_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"PGP clear-sign failed for {archive_path.name}: {result.stderr}")
    sha_path.unlink(missing_ok=True)
    return sha, manifest_path


def generate_index(package_dir, arch="x86_64", output=None):
    """Generate InterGenOS.db from a directory of .igos.tar.gz packages.

    Args:
        package_dir: Path to directory containing .igos.tar.gz archives
        arch: Target architecture string (default: "x86_64")
        output: Output path for the gzipped JSON index (default: <dir>/InterGenOS.db)

    Returns:
        Path to the generated index file
    """
    package_dir = Path(package_dir)
    if not package_dir.is_dir():
        print(f"ERROR: package_dir '{package_dir}' is not a directory", file=sys.stderr)
        sys.exit(1)

    if output is None:
        output = package_dir / "InterGenOS.db"
    output = Path(output)

    packages = {}
    pkg_files = sorted(package_dir.glob("*.igos.tar.gz"))

    if not pkg_files:
        print(f"WARNING: no .igos.tar.gz files found in {package_dir}", file=sys.stderr)

    for pkg_file in pkg_files:
        print(f"  indexing: {pkg_file.name}", file=sys.stderr)
        meta = _read_package_meta(pkg_file)

        if not meta:
            print(f"  WARNING: no .PKGINFO or metadata.json in {pkg_file.name}, deriving from filename", file=sys.stderr)
            meta = {}

        sha = _sha256_file(pkg_file)
        meta["sha256"] = sha
        meta["size"] = pkg_file.stat().st_size
        meta["filename"] = pkg_file.name

        name = meta.pop("name", None) or _package_name_from_filename(pkg_file.name)
        packages[name] = meta

    index = {
        "version": 1,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "arch": arch,
        "package_count": len(packages),
        "packages": packages,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(output, "wt", encoding="utf-8") as f:
        json.dump(index, f, indent=2, sort_keys=True)

    return output


def sign_index(index_path, gpg_key_id=None):
    """PGP detached-sign the repository index.

    Produces <index_path>.sig alongside the index.
    """
    cmd = ["gpg", "--detach-sign", "--armor", "--output", str(Path(str(index_path) + ".sig"))]
    if gpg_key_id:
        cmd.extend(["--local-user", gpg_key_id])
    cmd.append(str(index_path))

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"GPG signing failed: {result.stderr.strip()}")

    return Path(str(index_path) + ".sig")


def main():
    parser = argparse.ArgumentParser(
        description="Generate and sign InterGenOS.db repository index (E1.B.6)",
    )
    parser.add_argument(
        "package_dir",
        help="Directory containing .igos.tar.gz package archives",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output path for InterGenOS.db (default: <package_dir>/InterGenOS.db)",
    )
    parser.add_argument(
        "-a", "--arch", default="x86_64",
        help="Target architecture (default: x86_64)",
    )
    parser.add_argument(
        "--gpg-key", default=DEFAULT_KEY,
        choices=list(RELEASE_KEYS.keys()),
        help=f"Release key to sign with (default: {DEFAULT_KEY})",
    )
    parser.add_argument(
        "--no-sign", action="store_true",
        help="Skip GPG signing (generate index only)",
    )
    parser.add_argument(
        "--build-manifests", action="store_true",
        help="Also generate per-package .manifest files (PGP clear-signed SHA-256)",
    )

    args = parser.parse_args()

    print(f"Generating InterGenOS.db for {args.arch} from {args.package_dir}", file=sys.stderr)

    index_path = generate_index(args.package_dir, arch=args.arch, output=args.output)
    print(f"Index written: {index_path} ({index_path.stat().st_size} bytes)", file=sys.stderr)

    if not args.no_sign:
        gpg_key = RELEASE_KEYS[args.gpg_key.upper()]
        sig_path = sign_index(index_path, gpg_key_id=gpg_key)
        print(f"Signature written: {sig_path} ({sig_path.stat().st_size} bytes)", file=sys.stderr)

    if args.build_manifests:
        pkg_dir = Path(args.package_dir)
        for pkg_file in sorted(pkg_dir.glob("*.igos.tar.gz")):
            try:
                sha, manifest = _build_manifest(pkg_file)
                print(f"  manifest: {manifest.name} (SHA-256: {sha[:16]}...)", file=sys.stderr)
            except Exception as e:
                print(f"  FAILED {pkg_file.name}: {e}", file=sys.stderr)

    print("Done.", file=sys.stderr)


if __name__ == "__main__":
    main()
