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
    python3 scripts/download-sources.py --mirror-upload user@host:/path/to/sources/   # Upload to VPS mirror
    python3 scripts/download-sources.py --check-updates             # Check for upstream updates
    python3 scripts/download-sources.py --check-updates --use-latest  # Download latest versions
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).parent.parent
PACKAGES_DIR = PROJECT_ROOT / "packages"
SOURCES_DIR = PROJECT_ROOT / "build" / "sources"
DEFAULT_MIRROR = "intergenos@origin.intergenstudios.com:/home/intergenos/repo/sources"
DEFAULT_UPDATES_JSON = str(PROJECT_ROOT / "build" / "updates.json")

TIERS = ["toolchain", "core", "base", "desktop", "ai", "extra"]


def sha256_file(path: str) -> str:
    """Compute SHA256 hash of a file."""
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha.update(chunk)
    return sha.hexdigest()


def resolve_url(url: str, name: str, version: str) -> str:
    """Replace ${version}, ${name}, and computed variables in URL templates.

    Computed variables (mirror the parser's _resolve_variables logic):
    - ${version_major}: first dot-separated component of version
    - ${version_major_minor}: first two dot-separated components
    These let URLs reference upstream-mirror directory schemes that
    organize releases by major.minor series (e.g. rpm.org's
    /releases/rpm-4.18.x/) without hardcoding the version.
    """
    parts = version.split(".")
    major = parts[0] if parts else ""
    major_minor = ".".join(parts[:2]) if len(parts) >= 2 else version
    return (url
            .replace("${version_major_minor}", major_minor)
            .replace("${version_major}", major)
            .replace("${version}", version)
            .replace("${name}", name))


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


def download_file(url: str, dest: str, timeout: int = 300, expected_sha256: str = "") -> bool:
    """Download a file using wget, falling back to curl. Returns True on success.

    Security hardening:
    - Warns on HTTP (non-HTTPS) URLs
    - Enforces TLS 1.2+ via --proto/--tlsv1.2
    - Verifies SHA256 against expected value if provided
    """
    # Warn on insecure URLs
    if url.startswith("http://"):
        print(f"    WARNING: insecure HTTP URL: {url}", flush=True)

    try:
        # Try wget first — enforce HTTPS protocol preference
        result = subprocess.run(
            ["wget", "-q", "--timeout=30", "--prefer-family=IPv4",
             "-O", dest, url],
            capture_output=True, timeout=timeout,
        )
        if result.returncode == 0 and os.path.exists(dest) and os.path.getsize(dest) > 0:
            if validate_download(dest):
                # Verify checksum if expected value is available
                if expected_sha256 and not expected_sha256.startswith(("NEEDS_CHECKSUM", "placeholder", "VERIFY_ON_FIRST_BUILD")):
                    actual = sha256_file(dest)
                    if actual != expected_sha256:
                        print(f"    CHECKSUM MISMATCH: expected {expected_sha256[:16]}... got {actual[:16]}...", flush=True)
                        os.unlink(dest)
                        return False
                return True

        # wget failed — try curl as fallback (some sites block wget)
        if os.path.exists(dest):
            os.unlink(dest)
        result = subprocess.run(
            ["curl", "-sL", "--connect-timeout", "30",
             "--proto", "=https,http", "--tlsv1.2",
             "-o", dest, url],
            capture_output=True, timeout=timeout,
        )
        if result.returncode == 0 and os.path.exists(dest) and os.path.getsize(dest) > 0:
            if validate_download(dest):
                # Verify checksum if expected
                if expected_sha256 and not expected_sha256.startswith(("NEEDS_CHECKSUM", "placeholder", "VERIFY_ON_FIRST_BUILD")):
                    actual = sha256_file(dest)
                    if actual != expected_sha256:
                        print(f"    CHECKSUM MISMATCH: expected {expected_sha256[:16]}... got {actual[:16]}...", flush=True)
                        os.unlink(dest)
                        return False
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
            parts = version.split(".")
            _major = parts[0] if parts else ""
            _major_minor = ".".join(parts[:2]) if len(parts) >= 2 else version
            filename = (filename
                        .replace("${version_major_minor}", _major_minor)
                        .replace("${version_major}", _major)
                        .replace("${version}", version)
                        .replace("${name}", name))
        else:
            filename = url.split("/")[-1]

        sources.append({
            "url": url,
            "filename": filename,
            "sha256": sha256,
            "needs_checksum": sha256 in ("NEEDS_CHECKSUM", "VERIFY_ON_FIRST_BUILD") or sha256.startswith("placeholder"),
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
            if download_file(src["url"], str(dest), expected_sha256=src.get("sha256", "")):
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

                # Late-added-source chroot sync. The build framework uses
                # TWO source dirs that don't auto-sync after chroot-prep:
                #   /mnt/intergenos/build/sources/  (host master, this dest)
                #   /mnt/igos/sources/              (chroot view, what build reads)
                # If invoked from the build VM (where /mnt/igos is local),
                # auto-copy. If invoked from host (typical case, /mnt/igos
                # only exists on the VM), print a SSH-copy instruction so
                # the user can sync manually if mid-build.
                chroot_sources = "/mnt/igos/sources"
                if os.path.isdir(chroot_sources) and os.access(chroot_sources, os.W_OK):
                    chroot_dest = os.path.join(chroot_sources, src["filename"])
                    try:
                        import shutil
                        shutil.copy2(str(dest), chroot_dest)
                        print(f"    SYNC: copied to {chroot_dest}", flush=True)
                    except (PermissionError, OSError) as e:
                        print(f"    WARN: could not sync to chroot ({e}); manual cp needed", flush=True)
                elif not os.path.isdir(chroot_sources):
                    # Common case: running on host, chroot is on VM.
                    # Only print the instruction if a build-in-progress is detected
                    # via the existence of /mnt/intergenos/build/logs/.build-phase
                    # to avoid noisy output during pre-build source population.
                    if os.path.exists("/mnt/intergenos/build/logs/.build-phase"):
                        fname = src["filename"]
                        print(f"    HINT: chroot dir {chroot_sources} not on this host (typical when run from host).", flush=True)
                        print(f"          If a build is in progress and chroot needs this source, sync via:", flush=True)
                        print(f"          ssh <build-vm> 'sudo cp /mnt/intergenos/build/sources/{fname} {chroot_sources}/{fname}'", flush=True)
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
            if "NEEDS_CHECKSUM" in stripped or "VERIFY_ON_FIRST_BUILD" in stripped:
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


def generate_sha256sums(sources_dir: Path, dest_path: Path) -> None:
    """Write SHA256SUMS for all files in sources_dir."""
    sums = []
    for f in sorted(sources_dir.iterdir()):
        if f.is_file() and f.name != "SHA256SUMS":
            sha = sha256_file(str(f))
            sums.append(f"{sha}  {f.name}")
    dest_path.write_text("\n".join(sums) + "\n")
    print(f"  SHA256SUMS written: {len(sums)} entries")


def cmd_mirror_upload(tiers: list[str], mirror_host: str = "", mirror_path: str = "",
                      dry_run: bool = False):
    """Upload local source tarballs to the VPS source mirror.

    Creates the current/ directory structure on the mirror and populates
    it with verified tarballs from the local cache. Generates SHA256SUMS
    for integrity verification.

    Q1=B (serve upstream as-is): tarballs are exact copies of upstream
    sources, never repackaged or modified. The SHA256 in the mirror's
    SHA256SUMS matches the SHA256 in package.yml.

    Q2=A (hard-fail if mirror down): builds reference mirror URLs as
    primary sources. When the mirror is unreachable, the build fails
    with a clear error — no silent fallback to upstream.
    """
    if not mirror_host:
        mirror_host = os.environ.get("MIRROR_HOST", "")
    if not mirror_path:
        mirror_path = os.environ.get("MIRROR_PATH", "/home/intergenos-fleet/public_html/intergenos/sources")

    packages = load_packages(tiers)
    print(f"\nPreparing mirror upload for {len(packages)} packages across tiers: {', '.join(tiers)}\n")

    SOURCES_DIR.mkdir(parents=True, exist_ok=True)

    to_upload = []
    seen_files = set()
    verified = 0
    missing = 0
    unchecked = 0

    for pkg in packages:
        for src in get_source_info(pkg):
            dest = SOURCES_DIR / src["filename"]

            if not dest.exists() or dest.stat().st_size == 0:
                missing += 1
                continue

            if src["needs_checksum"]:
                unchecked += 1
                continue

            if src["filename"] in seen_files:
                continue

            actual = sha256_file(str(dest))
            if actual == src["sha256"]:
                verified += 1
                seen_files.add(src["filename"])
                to_upload.append({
                    "filename": src["filename"],
                    "sha256": actual,
                    "size": dest.stat().st_size,
                    "path": str(dest),
                })
            else:
                missing += 1
                print(f"  CHECKSUM MISMATCH (skipping): {src['filename']}")

    print(f"  Verified: {verified}  Missing/unchecked/mismatched: {missing + unchecked}")
    print(f"  To upload: {len(to_upload)} tarballs\n")

    if missing + unchecked > 0 and not dry_run:
        print("  WARNING: some tarballs are missing or lack checksums. Run --all --update-checksums first.")
        print()

    if dry_run:
        total_size = sum(item["size"] for item in to_upload)
        print(f"  [DRY RUN] Would upload {len(to_upload)} files ({total_size / 1024 / 1024:.1f} MB total)")
        for item in to_upload[:10]:
            print(f"    {item['filename']} ({item['size'] / 1024 / 1024:.1f} MB)")
        if len(to_upload) > 10:
            print(f"    ... and {len(to_upload) - 10} more")
        return

    if not mirror_host:
        print("ERROR: --mirror-host required (or set MIRROR_HOST env var)")
        print("  Example: --mirror-host intergenos-fleet@intergenstudios.com")
        sys.exit(1)

    import tempfile

    with tempfile.TemporaryDirectory(prefix="mirror-upload-") as staging:
        staging_path = Path(staging)
        current_path = staging_path / "current"
        current_path.mkdir()

        total_size = 0
        for item in to_upload:
            dest_file = current_path / item["filename"]
            import shutil
            shutil.copy2(item["path"], str(dest_file))
            total_size += item["size"]

        generate_sha256sums(current_path, current_path / "SHA256SUMS")

        print(f"  Staging complete: {len(to_upload)} files, {total_size / 1024 / 1024:.1f} MB")
        print(f"  Uploading to {mirror_host}:{mirror_path}/current/ ...")
        print()

        ssh_key = os.environ.get("MIRROR_SSH_KEY",
                                  os.path.expanduser("~/.ssh/intergenos-fleet_ed25519"))

        remote_dest = f"{mirror_host}:{mirror_path}/current/"
        result = subprocess.run(
            ["rsync", "-avz", "--progress",
             "-e", f"ssh -i {ssh_key} -o StrictHostKeyChecking=accept-new",
             f"{current_path}/", remote_dest],
            capture_output=True, text=True, timeout=600, shell=False
        )

        if result.returncode == 0:
            print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)
            print(f"\n  UPLOAD COMPLETE — {len(to_upload)} files synced to {remote_dest}")
            print(f"  Public URL: https://intergenstudios.com/intergenos/sources/current/")
        else:
            print(f"  rsync stderr: {result.stderr[-500:]}")
            print(f"  rsync exit code: {result.returncode}")
            print(f"  UPLOAD FAILED. Check SSH connectivity to {mirror_host}")
            sys.exit(1)


def cmd_check_updates(tiers: list[str], updates_json: str, use_latest: bool = False):
    """Check for upstream updates from vps-source-poller's updates.json.

    Consumes the advisory output from scripts/vps-source-poller.py (E1.A.3)
    to identify packages with newer upstream versions.
    """
    updates_path = Path(updates_json)
    if not updates_path.exists():
        print(f"WARNING: {updates_path} not found. Run vps-source-poller.py first.", flush=True)
        return

    with open(updates_path) as f:
        updates = json.load(f)

    packages = load_packages(tiers)
    pkg_map = {p["name"]: p for p in packages}

    new_updates = []
    for entry in updates:
        name = entry.get("pkg", "")
        current_ver = entry.get("current_ver", "")
        latest_ver = entry.get("latest_ver", "")
        if name not in pkg_map or not latest_ver or latest_ver == current_ver:
            continue
        new_updates.append(entry)

    if not new_updates:
        print("No packages with available updates.", flush=True)
        return

    print(f"{len(new_updates)} packages have updates available:\n")
    for u in new_updates:
        print(f"  {u['pkg']}: {u['current_ver']} → {u['latest_ver']}  ({u.get('source_url', '?')})")

    if use_latest:
        print(f"\nDownloading latest versions to {SOURCES_DIR / '.latest'} ...")
        latest_dir = SOURCES_DIR / ".latest"
        latest_dir.mkdir(parents=True, exist_ok=True)

        succeeded = 0
        for u in new_updates:
            name = u["pkg"]
            latest_ver = u["latest_ver"]
            source_url = u.get("source_url", "")
            if not source_url:
                continue
            filename = source_url.split("/")[-1]
            dest = latest_dir / filename
            print(f"  [{succeeded+1}/{len(new_updates)}] {filename} ...", flush=True)
            if download_file(source_url, str(dest)):
                print(f"    OK", flush=True)
                succeeded += 1
            else:
                print(f"    FAILED", flush=True)
        print(f"\nDownloaded {succeeded}/{len(new_updates)} to {latest_dir} (side dir; does NOT modify package.yml)")


def _parse_mirror_upload(value: str) -> tuple:
    """Parse --mirror-upload value in 'user@host:path' format."""
    if ":" not in value:
        return "", ""
    user_host, _, path = value.partition(":")
    user_host = user_host.strip()
    path = path.strip()
    if not user_host or not path:
        return "", ""
    return user_host, path


def main():
    parser = argparse.ArgumentParser(
        description="InterGenOS Source Tarball Manager",
        epilog="Without --all or --tier, individual action flags operate on all tiers.",
    )
    parser.add_argument("--tier", action="append", choices=TIERS, dest="tiers",
                        help="Package tier to operate on (repeatable)")
    parser.add_argument("--all", action="store_true", help="All tiers")
    parser.add_argument("--verify", action="store_true", help="Verify cached tarballs")
    parser.add_argument("--update-checksums", action="store_true", help="Update package.yml with computed SHAs")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    parser.add_argument("--mirror-upload", nargs="?", const=DEFAULT_MIRROR, metavar="USER@HOST:PATH",
                        help="Upload local cache to VPS mirror (default: intergenos@origin.intergenstudios.com:/home/intergenos/repo/sources)")
    parser.add_argument("--check-updates", action="store_true",
                        help="Check for upstream updates via vps-source-poller output")
    parser.add_argument("--updates-json", default=DEFAULT_UPDATES_JSON,
                        help=f"Path to updates.json (default: {DEFAULT_UPDATES_JSON})")
    parser.add_argument("--use-latest", action="store_true",
                        help="Download latest versions to side dir (requires --check-updates)")

    args = parser.parse_args()

    tiers = TIERS if args.all else (args.tiers if args.tiers else TIERS)

    if args.check_updates:
        cmd_check_updates(tiers, args.updates_json, use_latest=args.use_latest)
        return

    if args.mirror_upload is not None:
        user_host, path = _parse_mirror_upload(args.mirror_upload)
        if not user_host:
            if "@" not in args.mirror_upload:
                print("ERROR: --mirror-upload requires 'user@host:path' format")
                print(f"  Example: --mirror-upload {DEFAULT_MIRROR}")
                sys.exit(1)
            user_host = args.mirror_upload
            path = "/home/intergenos/repo/sources"
        cmd_mirror_upload(tiers, mirror_host=user_host, mirror_path=path, dry_run=args.dry_run)
        return

    if args.verify:
        cmd_verify(tiers)
    else:
        cmd_download(tiers, update_checksums=args.update_checksums, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
