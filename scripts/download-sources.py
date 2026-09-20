#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
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
# The mirror's web root on the VPS and the URL that serves it. Measured
# 2026-09-20 over ssh on the publish port: /home/intergenos/repo is the
# document root for repo.intergenos.org, and repo/sources holds exactly one
# entry, the plain directory current/ (no symlink, no atomic-swap target —
# the release publisher swaps only repo/x86_64/current). So an upload under
# repo/sources/current is served at /sources/current, and that is the one
# place a source fetch looks.
MIRROR_SERVED_ROOT_PATH = "/home/intergenos/repo"
MIRROR_SERVED_ROOT_URL = "https://repo.intergenos.org"

# The upload target is a path on the VPS; the fetch base is a URL. They must
# name the SAME served directory or an uploaded tarball is a silent miss for
# every fetch, so both are derived from the pair above and a test holds them
# together.
DEFAULT_MIRROR_PATH = f"{MIRROR_SERVED_ROOT_PATH}/sources"
DEFAULT_MIRROR = f"intergenos@origin.intergenstudios.com:{DEFAULT_MIRROR_PATH}"
DEFAULT_MIRROR_FETCH_BASE = f"{MIRROR_SERVED_ROOT_URL}/sources/current"
DEFAULT_UPDATES_JSON = str(PROJECT_ROOT / "build" / "updates.json")

TIERS = ["toolchain", "core", "base", "desktop", "ai", "compute", "extra"]


def mirror_upload_dir(mirror_path: str) -> str:
    """The directory an upload to mirror_path actually writes into.

    The upload stages into current/ and rsyncs that, so the served directory
    is one level below the path the operator passes.
    """
    return f"{mirror_path.rstrip('/')}/current"


def served_url_for(remote_dir: str) -> str:
    """The public URL that serves a directory under the mirror's web root.

    Raises ValueError for a path outside the web root, which is served by
    nothing and would make an upload invisible to every fetch.
    """
    rel = os.path.relpath(remote_dir, MIRROR_SERVED_ROOT_PATH)
    if rel == os.pardir or rel.startswith(os.pardir + os.sep) or os.path.isabs(rel):
        raise ValueError(
            f"{remote_dir} is outside the mirror web root {MIRROR_SERVED_ROOT_PATH}; "
            "nothing serves it")
    return f"{MIRROR_SERVED_ROOT_URL}/{rel}"


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


def _has_pin(expected_sha256: str) -> bool:
    """True when the recipe carries a real sha256 to verify bytes against."""
    return bool(expected_sha256) and not expected_sha256.startswith(
        ("NEEDS_CHECKSUM", "placeholder", "VERIFY_ON_FIRST_BUILD"))


def mirror_fetch_base() -> str:
    """The served directory sources are fetched from, without a trailing slash."""
    return os.environ.get(
        "MIRROR_FETCH_BASE", DEFAULT_MIRROR_FETCH_BASE).rstrip("/")


def _pin_matches(dest: str, expected_sha256: str, where: str) -> bool:
    """Check a downloaded file against its pin; delete it and report a miss."""
    actual = sha256_file(dest)
    if actual == expected_sha256:
        return True
    print(f"    {where} CHECKSUM MISMATCH: expected {expected_sha256[:16]}... "
          f"got {actual[:16]}...", flush=True)
    os.unlink(dest)
    return False


# How many times an UPSTREAM fetch is attempted before a source is called
# unfetchable, and how long to wait between attempts. Decided 2026-09-20: on
# 2026-09-19 a source was recorded as having no fetchable tarball after one
# failed wget and one failed curl inside the same minute; ten attempts the next
# morning all succeeded and hashed to the pin. The server had been shedding
# load. One attempt cannot tell a server having a bad minute from a URL that is
# gone, so a bounded retry makes that difference visible instead of guessing.
# The mirror leg keeps its single attempt: it asks with -f, and a 404 there is
# an answer, not a transient.
DEFAULT_UPSTREAM_ATTEMPTS = 3
DEFAULT_UPSTREAM_BACKOFF_SECONDS = 2.0


def source_fetch_attempts() -> int:
    """Upstream attempts per source; at least one. SOURCE_FETCH_ATTEMPTS overrides."""
    try:
        n = int(os.environ.get("SOURCE_FETCH_ATTEMPTS", str(DEFAULT_UPSTREAM_ATTEMPTS)))
    except ValueError:
        n = DEFAULT_UPSTREAM_ATTEMPTS
    return max(1, n)


def source_fetch_backoff() -> float:
    """Seconds to wait after a failed attempt, multiplied by the attempt number.

    SOURCE_FETCH_BACKOFF overrides it; 0 disables the wait, which is what the
    tests use so a retry test does not spend real seconds sleeping.
    """
    try:
        s = float(os.environ.get("SOURCE_FETCH_BACKOFF", str(DEFAULT_UPSTREAM_BACKOFF_SECONDS)))
    except ValueError:
        s = DEFAULT_UPSTREAM_BACKOFF_SECONDS
    return max(0.0, s)


def _upstream_attempt(url: str, dest: str, timeout: int, have_pin: bool,
                      expected_sha256: str) -> tuple:
    """One upstream attempt — wget, then curl for the sites that block wget.

    Returns (verdict, detail). The verdict is one of:
      "ok"        the bytes are in place and, where there is a pin, verified;
      "mismatch"  a COMPLETE transfer whose bytes are not the pinned ones. That
                  is a definitive answer about what the server serves, so it is
                  never retried — retrying it would only turn a clear signal
                  that the bytes changed into a slower clear signal;
      "transient" the transfer itself failed (refused, reset, timed out, cut
                  short, or an error page in place of an archive). This is the
                  only verdict that earns another attempt.
    A truncated transfer lands here as "transient", not "mismatch", because the
    tool reports a non-zero exit before its bytes are ever hashed — which is
    exactly how 2026-09-19's two partials failed.
    """
    for tool, argv in (
        ("wget", ["wget", "-q", "--timeout=30", "--prefer-family=IPv4", "-O", dest, url]),
        ("curl", ["curl", "-sL", "--connect-timeout", "30",
                  "--proto", "=https,http", "--tlsv1.2", "-o", dest, url]),
    ):
        if os.path.exists(dest):
            os.unlink(dest)
        try:
            result = subprocess.run(argv, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return "transient", f"{tool} timed out after {timeout}s"
        if result.returncode != 0:
            continue
        if not os.path.exists(dest) or os.path.getsize(dest) == 0:
            continue
        if not validate_download(dest):
            continue
        if not have_pin:
            return "ok", tool
        if _pin_matches(dest, expected_sha256, ""):
            return "ok", tool
        return "mismatch", (
            f"{tool} transferred {os.path.basename(url)} in full and its sha256 is "
            "not the pinned one")
    return "transient", "wget and curl both failed to transfer the file"


def download_file(url: str, dest: str, timeout: int = 300, expected_sha256: str = "") -> bool:
    """Download a file: the project mirror first, upstream second.

    Order decided 2026-09-20. Software comes from the project's own mirror
    when the mirror serves it, and from upstream only when it does not; every
    upstream pull prints the URL it is taking bytes from, so a run discloses
    what it fetched from the internet. Before this, upstream was tried first
    and the mirror was reached only after wget AND curl had both failed, so an
    ordinary run pulled from the internet even where the mirror already held a
    pinned, byte-identical copy.

    The order changes WHERE the bytes come from, never whether they are
    checked: the sha256 pin is enforced on both paths, so a tampered mirror
    copy is rejected exactly like a tampered upstream one — the mirror gives
    availability, the pin gives integrity. A source with no pin skips the
    mirror entirely (decided 2026-05-30): with nothing to verify against, a
    mirror copy is no safer than an upstream one.

    The upstream leg is attempted up to source_fetch_attempts() times with a
    short growing pause, and every attempt is printed. A source is only called
    unfetchable after all of them, and the failure line says how many were
    made, so "upstream is down right now" stops reading as "this pin is dead".

    Security hardening:
    - Warns on HTTP (non-HTTPS) URLs
    - Enforces TLS 1.2+ via --proto/--tlsv1.2; the mirror leg is HTTPS-only
    - Verifies SHA256 against the expected value on every path that has one
    - A complete transfer whose bytes miss the pin is never retried
    """
    # Warn on insecure URLs
    if url.startswith("http://"):
        print(f"    WARNING: insecure HTTP URL: {url}", flush=True)

    have_pin = _has_pin(expected_sha256)
    mirror_url = f"{mirror_fetch_base()}/{os.path.basename(dest)}" if have_pin else ""
    if mirror_url == url:
        # The recipe already points at the mirror — one fetch, not two.
        mirror_url = ""

    # ---- the mirror, first, one attempt ------------------------------
    if mirror_url:
        # -f fails on HTTP 404 so a not-yet-mirrored source does not write
        # an error page; --proto =https forces HTTPS for the mirror fetch.
        try:
            mresult = subprocess.run(
                ["curl", "-sfL", "--connect-timeout", "30",
                 "--proto", "=https", "-o", dest, mirror_url],
                capture_output=True, timeout=timeout,
            )
            mirror_ok = mresult.returncode == 0
        except subprocess.TimeoutExpired:
            mirror_ok = False
        if (mirror_ok and os.path.exists(dest) and os.path.getsize(dest) > 0
                and validate_download(dest)):
            if _pin_matches(dest, expected_sha256, "MIRROR"):
                print(f"    OK from mirror (sha256 pin verified): {mirror_url}", flush=True)
                return True
        elif os.path.exists(dest):
            os.unlink(dest)
        print(f"    not served by the mirror ({mirror_url}) — pulling from upstream", flush=True)

    # ---- upstream, second, up to N attempts --------------------------
    attempts = source_fetch_attempts()
    backoff = source_fetch_backoff()
    for attempt in range(1, attempts + 1):
        print(f"    upstream fetch attempt {attempt} of {attempts}: {url}", flush=True)
        try:
            verdict, detail = _upstream_attempt(url, dest, timeout, have_pin, expected_sha256)
        except Exception as e:  # noqa: BLE001 — reported, then the attempt is spent
            verdict, detail = "transient", f"{type(e).__name__}: {e}"
        if verdict == "ok":
            return True
        if verdict == "mismatch":
            print(f"    attempt {attempt} of {attempts}: {detail} — NOT RETRIED, "
                  "the transfer completed and the bytes are not the pinned ones", flush=True)
            if os.path.exists(dest):
                os.unlink(dest)
            return False
        print(f"    attempt {attempt} of {attempts} failed: {detail}", flush=True)
        if attempt < attempts and backoff:
            pause = backoff * attempt
            print(f"    waiting {pause:g}s before the next attempt", flush=True)
            time.sleep(pause)

    if os.path.exists(dest):
        os.unlink(dest)
    print(f"    FAILED after {attempts} upstream attempt(s): {url}", flush=True)
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

    The directory written here is the directory sources are fetched from:
    mirror_path + /current is served at the fetch base download_file() reads,
    and a test holds the two defaults together. An upload path outside the
    mirror's web root is refused, because nothing would serve it.

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
        mirror_path = os.environ.get("MIRROR_PATH", DEFAULT_MIRROR_PATH)

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

    upload_dir = mirror_upload_dir(mirror_path)
    try:
        public_url = served_url_for(upload_dir)
    except ValueError as exc:
        # An upload nothing serves is a silent miss for every fetch, so it is
        # refused here — before the copy, and in a dry run too — rather than
        # discovered as a 404 months later.
        print(f"ERROR: {exc}")
        sys.exit(1)

    if dry_run:
        total_size = sum(item["size"] for item in to_upload)
        print(f"  [DRY RUN] Would upload {len(to_upload)} files ({total_size / 1024 / 1024:.1f} MB total)")
        print(f"  [DRY RUN] Destination: {mirror_host or '<--mirror-host required>'}:{upload_dir}/")
        print(f"  [DRY RUN] Served at:   {public_url}/ — where source fetches read")
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
        print(f"  Uploading to {mirror_host}:{upload_dir}/ ...")
        print(f"  Served at {public_url}/ — the base source fetches read")
        print()

        ssh_key = os.environ.get("MIRROR_SSH_KEY",
                                  os.path.expanduser("~/.ssh/id_ed25519"))
        ssh_port = os.environ.get("MIRROR_SSH_PORT", "2200")

        remote_dest = f"{mirror_host}:{upload_dir}/"
        result = subprocess.run(
            ["rsync", "-avz", "--progress",
             "-e", f"ssh -p {ssh_port} -i {ssh_key} -o StrictHostKeyChecking=accept-new",
             f"{current_path}/", remote_dest],
            capture_output=True, text=True, timeout=7200, shell=False
        )

        if result.returncode == 0:
            print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)
            print(f"\n  UPLOAD COMPLETE — {len(to_upload)} files synced to {remote_dest}")
            print(f"  Public URL: {public_url}/")
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
                        help=f"Upload local cache to VPS mirror (default: {DEFAULT_MIRROR}, "
                             f"served at {DEFAULT_MIRROR_FETCH_BASE}/)")
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
