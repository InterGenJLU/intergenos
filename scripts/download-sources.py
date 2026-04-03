#!/usr/bin/env python3
"""InterGenOS Source Tarball Manager

Downloads source tarballs for package templates, computes SHA256 checksums,
and optionally updates package.yml files with the hashes.

Usage:
    python3 scripts/download-sources.py --tier desktop              # Download desktop sources
    python3 scripts/download-sources.py --tier core base desktop    # Multiple tiers
    python3 scripts/download-sources.py --all                       # All tiers
    python3 scripts/download-sources.py --all --update-checksums    # Download + update package.yml
    python3 scripts/download-sources.py --verify                    # Verify existing tarballs
    python3 scripts/download-sources.py --all --dry-run             # Show what would be downloaded
"""

import hashlib
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).parent.parent
PACKAGES_DIR = PROJECT_ROOT / "packages"
SOURCES_DIR = PROJECT_ROOT / "build" / "sources"

TIERS = ["toolchain", "core", "base", "desktop"]


def sha256_file(path: str) -> str:
    """Compute SHA256 hash of a file."""
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha.update(chunk)
    return sha.hexdigest()


def resolve_url(url: str, name: str, version: str) -> str:
    """Replace ${version} and ${name} in URL templates."""
    return url.replace("${version}", version).replace("${name}", name)


def validate_download(dest: str) -> bool:
    """Verify a downloaded file is actually an archive, not an error page.

    Returns True if the file looks valid. Removes the file and returns False
    if it's suspiciously small or is plain text (HTML error page, "Not Found", etc.).
    """
    if not os.path.exists(dest):
        return False

    size = os.path.getsize(dest)

    # Archives should be at least 1KB — anything smaller is almost certainly
    # an error page or empty response
    if size < 1024:
        with open(dest, "rb") as f:
            head = f.read(512)
        # Check if it's text (HTML error, "Not Found", redirect page, etc.)
        try:
            text = head.decode("utf-8", errors="strict")
            if any(marker in text.lower() for marker in ["not found", "<html", "<!doctype", "error", "redirect"]):
                print(f"    CORRUPT: downloaded file is a text error page ({size} bytes: {text.strip()[:80]})", flush=True)
                os.unlink(dest)
                return False
        except UnicodeDecodeError:
            pass  # Binary data — probably fine, just very small

    return True


def download_file(url: str, dest: str, timeout: int = 300) -> bool:
    """Download a file using wget, falling back to curl. Returns True on success."""
    try:
        # Try wget first
        result = subprocess.run(
            ["wget", "-q", "--timeout=30", "-O", dest, url],
            capture_output=True, timeout=timeout,
        )
        if result.returncode == 0 and os.path.exists(dest) and os.path.getsize(dest) > 0:
            if validate_download(dest):
                return True

        # wget failed — try curl as fallback (some sites block wget)
        if os.path.exists(dest):
            os.unlink(dest)
        result = subprocess.run(
            ["curl", "-sL", "--connect-timeout", "30", "-o", dest, url],
            capture_output=True, timeout=timeout,
        )
        if result.returncode == 0 and os.path.exists(dest) and os.path.getsize(dest) > 0:
            if validate_download(dest):
                return True

        return False
    except subprocess.TimeoutExpired:
        print(f"    TIMEOUT: {url}", flush=True)
        if os.path.exists(dest):
            os.unlink(dest)
        return False
    except Exception as e:
        print(f"    ERROR: {e}", flush=True)
        return False


def load_packages(tiers: list[str]) -> list[dict]:
    """Load all package.yml files for the given tiers."""
    packages = []
    for tier in tiers:
        tier_dir = PACKAGES_DIR / tier
        if not tier_dir.exists():
            print(f"  WARNING: tier directory not found: {tier_dir}")
            continue
        for pkg_yml in sorted(tier_dir.rglob("package.yml")):
            with open(pkg_yml) as f:
                data = yaml.safe_load(f)
                data["_path"] = pkg_yml
                data["_tier"] = tier
                packages.append(data)
    return packages


def get_source_info(pkg: dict) -> list[dict]:
    """Extract source URL, filename, and expected SHA256 for each source."""
    sources = []
    name = pkg.get("name", "")
    version = str(pkg.get("version", ""))

    for src in pkg.get("source", []):
        url = resolve_url(src.get("url", ""), name, version)
        sha256 = src.get("sha256", "NEEDS_CHECKSUM")

        # Determine local filename
        filename = src.get("filename")
        if filename:
            filename = filename.replace("${version}", version).replace("${name}", name)
        else:
            filename = url.split("/")[-1]

        sources.append({
            "url": url,
            "filename": filename,
            "sha256": sha256,
            "needs_checksum": sha256 == "NEEDS_CHECKSUM",
        })
    return sources


def cmd_download(tiers: list[str], update_checksums: bool = False, dry_run: bool = False):
    """Download missing source tarballs."""
    packages = load_packages(tiers)
    print(f"\nScanning {len(packages)} packages across tiers: {', '.join(tiers)}\n")

    SOURCES_DIR.mkdir(parents=True, exist_ok=True)

    to_download = []
    already_have = 0
    total_sources = 0

    for pkg in packages:
        for src in get_source_info(pkg):
            total_sources += 1
            dest = SOURCES_DIR / src["filename"]

            if dest.exists() and dest.stat().st_size > 0:
                already_have += 1
                # If we have the file but need checksum, compute it
                if update_checksums and src["needs_checksum"]:
                    to_download.append({
                        "pkg": pkg,
                        "src": src,
                        "dest": dest,
                        "action": "checksum_only",
                    })
            else:
                # Remove empty files from failed downloads
                if dest.exists() and dest.stat().st_size == 0:
                    dest.unlink()
                to_download.append({
                    "pkg": pkg,
                    "src": src,
                    "dest": dest,
                    "action": "download",
                })

    downloads_needed = len([d for d in to_download if d["action"] == "download"])
    checksums_needed = len([d for d in to_download if d["action"] == "checksum_only"])

    print(f"  Total sources: {total_sources}")
    print(f"  Already cached: {already_have}")
    print(f"  To download: {downloads_needed}")
    if checksums_needed:
        print(f"  Checksum only: {checksums_needed}")
    print()

    if dry_run:
        for item in to_download:
            if item["action"] == "download":
                print(f"  [DRY] Would download: {item['src']['filename']}")
                print(f"         URL: {item['src']['url']}")
        return

    succeeded = 0
    failed = 0
    checksummed = 0

    for i, item in enumerate(to_download, 1):
        src = item["src"]
        dest = item["dest"]
        pkg = item["pkg"]
        name = pkg.get("name", "?")

        if item["action"] == "download":
            print(f"  [{i}/{len(to_download)}] Downloading {src['filename']}...", flush=True)
            if download_file(src["url"], str(dest)):
                size = dest.stat().st_size
                human = f"{size/1024/1024:.1f}MB" if size > 1024*1024 else f"{size/1024:.0f}KB"
                print(f"    OK ({human})", flush=True)
                succeeded += 1

                # Compute checksum for newly downloaded file
                if update_checksums:
                    sha = sha256_file(str(dest))
                    update_package_checksum(pkg["_path"], src["url"], sha)
                    print(f"    SHA256: {sha[:16]}... (updated)", flush=True)
                    checksummed += 1
            else:
                print(f"    FAILED: {src['url']}", flush=True)
                # Remove empty/partial file from failed download
                if dest.exists():
                    dest.unlink()
                failed += 1

        elif item["action"] == "checksum_only":
            print(f"  [{i}/{len(to_download)}] Computing checksum: {src['filename']}...", flush=True)
            sha = sha256_file(str(dest))
            update_package_checksum(pkg["_path"], src["url"], sha)
            print(f"    SHA256: {sha[:16]}... (updated)", flush=True)
            checksummed += 1

    print(f"\nDone: {succeeded} downloaded, {failed} failed, {checksummed} checksums updated", flush=True)
    if failed:
        print(f"\n  WARNING: {failed} downloads failed. Re-run to retry.", flush=True)


def update_package_checksum(pkg_path: Path, url: str, sha256: str):
    """Update a package.yml file with a real SHA256 checksum."""
    with open(pkg_path) as f:
        content = f.read()

    # Find the source entry matching this URL and replace its checksum
    # We look for the NEEDS_CHECKSUM on the line after the URL
    lines = content.split("\n")
    found_url = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Match the URL line (may have ${version} or resolved form)
        if stripped.startswith("url:") or stripped.startswith("- url:"):
            found_url = True
            continue
        if found_url and "sha256:" in stripped:
            if "NEEDS_CHECKSUM" in stripped:
                indent = len(line) - len(line.lstrip())
                lines[i] = " " * indent + f"sha256: {sha256}"
                found_url = False
                break
            found_url = False

    with open(pkg_path, "w") as f:
        f.write("\n".join(lines))


def cmd_verify(tiers: list[str]):
    """Verify cached tarballs match their declared SHA256."""
    packages = load_packages(tiers)
    print(f"\nVerifying sources for {len(packages)} packages\n")

    good = 0
    bad = 0
    missing = 0
    unchecked = 0

    for pkg in packages:
        for src in get_source_info(pkg):
            dest = SOURCES_DIR / src["filename"]

            if not dest.exists():
                missing += 1
                continue

            if src["needs_checksum"]:
                unchecked += 1
                continue

            actual = sha256_file(str(dest))
            if actual == src["sha256"]:
                good += 1
            else:
                bad += 1
                print(f"  MISMATCH: {src['filename']}")
                print(f"    expected: {src['sha256']}")
                print(f"    actual:   {actual}")

    print(f"\nResults: {good} verified, {bad} mismatched, {missing} missing, {unchecked} no checksum")


def main():
    args = sys.argv[1:]

    if not args or "-h" in args or "--help" in args:
        print(__doc__)
        sys.exit(0)

    # Parse arguments
    tiers = []
    update_checksums = "--update-checksums" in args
    dry_run = "--dry-run" in args
    verify_mode = "--verify" in args

    if "--all" in args:
        tiers = TIERS
    elif "--tier" in args:
        idx = args.index("--tier")
        # Collect all following args that aren't flags
        for a in args[idx+1:]:
            if a.startswith("--"):
                break
            if a in TIERS:
                tiers.append(a)
            else:
                print(f"Unknown tier: {a}. Valid: {', '.join(TIERS)}")
                sys.exit(1)

    if not tiers and not verify_mode:
        print("Error: specify --tier <name> or --all")
        sys.exit(1)

    if verify_mode:
        if not tiers:
            tiers = TIERS
        cmd_verify(tiers)
    else:
        cmd_download(tiers, update_checksums=update_checksums, dry_run=dry_run)


if __name__ == "__main__":
    main()
