#!/usr/bin/env python3
"""InterGenOS Source Mirror — Upstream Version Poller (Component 3)

Periodically checks upstream sources for newer versions than what's currently
in package.yml manifests. Downloads new candidates to the mirror's /latest/
directory and writes an advisory updates.json. NEVER modifies package.yml —
updates are informational, requiring explicit owner adoption.

Design decisions (Lane B):
  Q1=B — serve upstream source tarballs as-is, never repackage
  Q2=A — mirror is primary source; hard-fail if unreachable during build

Usage:
    python3 scripts/mirror-poller.py                        # Check all tiers
    python3 scripts/mirror-poller.py --tier core base       # Specific tiers
    python3 scripts/mirror-poller.py --dry-run              # Report only, no download
    python3 scripts/mirror-poller.py --cron                 # Cron mode: quiet unless updates found

Environment:
    MIRROR_LATEST_DIR  — path to mirror's latest/ directory (default: from --mirror-path/latest/)
    MIRROR_PATH        — base mirror path
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
import ssl
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml


PROJECT_ROOT = Path(__file__).parent.parent
PACKAGES_DIR = PROJECT_ROOT / "packages"
TIERS = ["toolchain", "core", "base", "desktop"]


def sha256_file(path: str) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha.update(chunk)
    return sha.hexdigest()


def resolve_url(url: str, name: str, version: str) -> str:
    parts = version.split(".")
    major = parts[0] if parts else ""
    major_minor = ".".join(parts[:2]) if len(parts) >= 2 else version
    return (url
            .replace("${version_major_minor}", major_minor)
            .replace("${version_major}", major)
            .replace("${version}", version)
            .replace("${name}", name))


def parse_version(v: str) -> tuple:
    """Parse a version string into a comparable tuple (major, minor, patch, ...)."""
    parts = []
    for segment in re.split(r"[.-]", v):
        try:
            parts.append(int(segment))
        except ValueError:
            for char in segment:
                if char.isdigit():
                    parts.append(ord(char))
                else:
                    parts.append(ord(char))
    return tuple(parts)


def version_newer(candidate: str, current: str) -> bool:
    """Return True if candidate is a newer version than current."""
    try:
        return parse_version(candidate) > parse_version(current)
    except Exception:
        return False


def resolve_variable(version_str: str, version_tuple: tuple, name: str) -> str:
    """Resolve ${version_major} and ${version_major_minor} for the given version."""
    version = ".".join(str(p) for p in version_tuple)
    parts = version.split(".")
    major = parts[0] if parts else ""
    major_minor = ".".join(parts[:2]) if len(parts) >= 2 else version
    return (version
            .replace("${version_major_minor}", major_minor)
            .replace("${version_major}", major)
            .replace("${version}", version)
            .replace("${name}", name))


def check_gnu_ftp(url_pattern: str, current_version: str, name: str) -> list[dict]:
    """Check GNU FTP mirrors for newer versions by parsing directory listings.

    Pattern example: https://ftpmirror.gnu.org/bash/bash-${version}.tar.gz
    """
    candidates = []
    try:
        dir_url = url_pattern[:url_pattern.rindex("/")]
        ctx = ssl.create_default_context()
        req = urllib.request.Request(dir_url + "/", headers={"User-Agent": "InterGenOS/mirror-poller"})
        listing = urllib.request.urlopen(req, context=ctx, timeout=30).read().decode("utf-8", errors="replace")

        tarball_exts = {".tar.gz", ".tar.xz", ".tar.bz2", ".tgz", ".tar.lz"}
        versions_found = set()

        for line in listing.split("\n"):
            if 'href="' not in line:
                continue
            href = line.split('href="')[1].split('"')[0]
            if not any(href.endswith(ext) for ext in tarball_exts):
                continue

            base = href.rsplit(".tar", 1)[0]
            try:
                ver_start = base.index(name) + len(name)
                if ver_start < len(base) and base[ver_start] in "-_":
                    ver_start += 1
                ver_str = base[ver_start:]
                if ver_str and ver_str[0].isdigit():
                    versions_found.add(ver_str)
            except (ValueError, IndexError):
                continue

        for ver in versions_found:
            if version_newer(ver, current_version):
                new_url = url_pattern.replace("${version}", ver)
                candidates.append({
                    "version": ver,
                    "url": new_url,
                    "source": "gnu-ftp",
                })

    except Exception as e:
        print(f"    GNU-FTP check failed for {name}: {e}", flush=True)

    return candidates


def check_github_releases(org_repo: str, current_version: str, name: str) -> list[dict]:
    """Check GitHub releases API for newer versions.

    upstream_check.repo format: owner/repo (e.g., torvalds/linux)
    """
    candidates = []
    try:
        api_url = f"https://api.github.com/repos/{org_repo}/releases?per_page=10"
        ctx = ssl.create_default_context()
        req = urllib.request.Request(api_url, headers={
            "User-Agent": "InterGenOS/mirror-poller",
            "Accept": "application/vnd.github.v3+json",
        })
        data = json.loads(urllib.request.urlopen(req, context=ctx, timeout=30).read())

        for release in data:
            tag = release.get("tag_name", "")
            ver = tag.lstrip("vV")
            if not ver or not ver[0].isdigit():
                continue
            if version_newer(ver, current_version):
                for asset in release.get("assets", []):
                    url = asset.get("browser_download_url", "")
                    if any(url.endswith(ext) for ext in [".tar.gz", ".tar.xz", ".tar.bz2", ".zip"]):
                        candidates.append({
                            "version": ver,
                            "url": url,
                            "source": "github",
                        })
                        break

    except Exception as e:
        print(f"    GitHub check failed for {name}: {e}", flush=True)

    return candidates


def check_pypi(package_name: str, current_version: str, name: str) -> list[dict]:
    """Check PyPI JSON API for newer versions."""
    candidates = []
    try:
        api_url = f"https://pypi.org/pypi/{package_name}/json"
        ctx = ssl.create_default_context()
        req = urllib.request.Request(api_url, headers={"User-Agent": "InterGenOS/mirror-poller"})
        data = json.loads(urllib.request.urlopen(req, context=ctx, timeout=30).read())

        latest = data.get("info", {}).get("version", "")
        if latest and version_newer(latest, current_version):
            for release in data.get("releases", {}).get(latest, []):
                if release.get("packagetype") == "sdist":
                    candidates.append({
                        "version": latest,
                        "url": release["url"],
                        "source": "pypi",
                    })
                    break

    except Exception as e:
        print(f"    PyPI check failed for {name}: {e}", flush=True)

    return candidates


def check_gnome(url_pattern: str, current_version: str, name: str) -> list[dict]:
    """Check GNOME download servers for newer versions.

    Pattern: https://download.gnome.org/sources/glib/2.88/glib-2.88.1.tar.xz
    """
    candidates = []
    try:
        series_dir = url_pattern[:url_pattern.rindex("/")]
        ctx = ssl.create_default_context()
        req = urllib.request.Request(series_dir + "/", headers={"User-Agent": "InterGenOS/mirror-poller"})
        listing = urllib.request.urlopen(req, context=ctx, timeout=30).read().decode("utf-8", errors="replace")

        tarball_exts = {".tar.gz", ".tar.xz", ".tar.bz2"}
        for line in listing.split("\n"):
            if 'href="' not in line:
                continue
            href = line.split('href="')[1].split('"')[0]
            if name not in href:
                continue
            if not any(href.endswith(ext) for ext in tarball_exts):
                continue

            ver = href.replace(name + "-", "").rsplit(".tar", 1)[0]
            if ver and ver[0].isdigit() and version_newer(ver, current_version):
                new_url = f"{series_dir}/{href}"
                candidates.append({
                    "version": ver,
                    "url": new_url,
                    "source": "gnome",
                })

    except Exception as e:
        print(f"    GNOME check failed for {name}: {e}", flush=True)

    return candidates


def detect_strategy(url: str, pkg: dict) -> str:
    """Detect upstream checking strategy from URL and package.yml metadata.

    Returns one of: gnu-ftp, github, pypi, gnome, custom, unknown
    """
    upstream_check = pkg.get("upstream_check", {})
    if upstream_check and upstream_check.get("type"):
        return upstream_check["type"]

    url_lower = url.lower()

    if "ftpmirror.gnu.org" in url_lower or "ftp.gnu.org" in url_lower:
        return "gnu-ftp"
    if "github.com" in url_lower:
        return "github"
    if "pypi.org" in url_lower or "files.pythonhosted.org" in url_lower:
        return "pypi"
    if "download.gnome.org" in url_lower or "freedesktop.org" in url_lower:
        return "gnome"
    if "kernel.org" in url_lower or "cdn.kernel.org" in url_lower:
        return "custom"
    if "sourceforge.net" in url_lower:
        return "custom"
    if "cpan.org" in url_lower or "metacpan.org" in url_lower:
        return "custom"
    if "sourceware.org" in url_lower:
        return "custom"
    if "git.kernel.org" in url_lower:
        return "custom"
    if "invisible-mirror.net" in url_lower:
        return "custom"

    return "unknown"


def check_upstream(pkg: dict, src_url: str, current_version: str) -> list[dict]:
    """Check upstream for a newer version of a package source."""
    strategy = detect_strategy(src_url, pkg)
    name = pkg.get("name", "?")

    if strategy == "gnu-ftp":
        candidates = check_gnu_ftp(src_url, current_version, name)
    elif strategy == "github":
        upstream = pkg.get("upstream_check", {})
        repo = upstream.get("repo", "")
        if not repo:
            from urllib.parse import urlparse
            parsed = urlparse(src_url)
            path_parts = parsed.path.strip("/").split("/")
            if len(path_parts) >= 2:
                repo = f"{path_parts[0]}/{path_parts[1]}"
        if repo:
            candidates = check_github_releases(repo, current_version, name)
        else:
            candidates = []
    elif strategy == "pypi":
        upstream = pkg.get("upstream_check", {})
        pypi_name = upstream.get("pypi_name", name)
        candidates = check_pypi(pypi_name, current_version, name)
    elif strategy == "gnome":
        candidates = check_gnome(src_url, current_version, name)
    else:
        candidates = []

    return candidates


def download_to_latest(url: str, dest_dir: Path, timeout: int = 300) -> Optional[str]:
    """Download a tarball to the latest/ directory. Returns filename or None."""
    filename = url.split("/")[-1]
    dest = dest_dir / filename

    if dest.exists():
        return filename

    try:
        result = subprocess.run(
            ["wget", "-q", "--timeout=30", "--prefer-family=IPv4",
             "-O", str(dest), url],
            capture_output=True, timeout=timeout, shell=False
        )
        if result.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
            # Validate not an HTML error page
            size = dest.stat().st_size
            if size < 1024:
                with open(dest, "rb") as f:
                    head = f.read(512)
                try:
                    text = head.decode("utf-8", errors="strict")
                    if any(m in text.lower() for m in ["not found", "<html", "<!doctype", "error"]):
                        dest.unlink()
                        return None
                except UnicodeDecodeError:
                    pass
            return filename
    except Exception:
        pass

    if dest.exists():
        dest.unlink()
    return None


def load_packages(tiers: list[str]) -> list[dict]:
    packages = []
    for tier in tiers:
        tier_dir = PACKAGES_DIR / tier
        if not tier_dir.exists():
            continue
        for pkg_yml in sorted(tier_dir.rglob("package.yml")):
            with open(pkg_yml) as f:
                data = yaml.safe_load(f)
                data["_path"] = str(pkg_yml)
                data["_tier"] = tier
                packages.append(data)
    return packages


def cmd_poll(tiers: list[str], latest_dir: Path, dry_run: bool = False, cron_mode: bool = False):
    """Poll upstream for newer versions and stage them in latest/."""
    packages = load_packages(tiers)
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    updates_available = []
    checked = 0
    skipped = 0
    failed = 0

    print(f"\nMirror Poller — {now_iso}")
    print(f"  Tiers: {', '.join(tiers)}")
    print(f"  Packages: {len(packages)}\n")

    for pkg in packages:
        name = pkg.get("name", "?")
        version = str(pkg.get("version", ""))

        for src in pkg.get("source", []):
            url = src.get("url", "")
            if not url:
                continue

            checked += 1
            current_url = resolve_url(url, name, version)

            try:
                candidates = check_upstream(pkg, current_url, version)
            except Exception as e:
                if not cron_mode:
                    print(f"  {name}: upstream check error — {e}")
                failed += 1
                continue

            if not candidates:
                skipped += 1
                continue

            for candidate in candidates:
                new_ver = candidate["version"]
                new_url = candidate["url"]

                if not dry_run:
                    latest_dir.mkdir(parents=True, exist_ok=True)
                    filename = download_to_latest(new_url, latest_dir)
                    if not filename:
                        if not cron_mode:
                            print(f"  {name}: download failed for {new_ver}")
                        failed += 1
                        continue

                    sha = sha256_file(str(latest_dir / filename))
                else:
                    sha = "dry-run"

                severity = "patch"
                try:
                    cv = parse_version(version)
                    nv = parse_version(new_ver)
                    if len(nv) > 2 and len(cv) > 2:
                        if nv[0] > cv[0]:
                            severity = "major"
                        elif nv[1] > cv[1]:
                            severity = "minor"
                except Exception:
                    pass

                entry = {
                    "name": name,
                    "current": version,
                    "latest": new_ver,
                    "url": new_url,
                    "sha256": sha,
                    "detected": now_iso,
                    "severity": severity,
                    "strategy": detect_strategy(url, pkg),
                }
                updates_available.append(entry)

                if not cron_mode:
                    print(f"  {name}: {version} → {new_ver} [{severity}] ({candidate['source']})")

    print(f"\n  Checked: {checked}  Updates found: {len(updates_available)}  Failed: {failed}")

    if updates_available:
        updates_json = {
            "last_check": now_iso,
            "tiers_checked": tiers,
            "updates_available": updates_available,
        }

        if not dry_run:
            updates_path = latest_dir.parent / "updates.json"
            updates_path.write_text(json.dumps(updates_json, indent=2) + "\n")

            sha256sums = []
            for f in sorted(latest_dir.iterdir()):
                if f.is_file() and f.name != "SHA256SUMS":
                    sha = sha256_file(str(f))
                    sha256sums.append(f"{sha}  {f.name}")
            if sha256sums:
                (latest_dir / "SHA256SUMS").write_text("\n".join(sha256sums) + "\n")

        print(f"\n  updates.json written with {len(updates_available)} entries")
        if not cron_mode:
            for u in updates_available:
                print(f"    {u['name']}: {u['current']} → {u['latest']} [{u['severity']}]")
    elif not cron_mode:
        print("  No updates available.")


def main():
    args = sys.argv[1:]

    if not args or "-h" in args or "--help" in args:
        print(__doc__)
        sys.exit(0)

    tiers = []
    dry_run = "--dry-run" in args
    cron_mode = "--cron" in args
    mirror_path = os.environ.get("MIRROR_PATH", str(PROJECT_ROOT / "build" / "mirror"))
    latest_dir = Path(os.environ.get("MIRROR_LATEST_DIR", os.path.join(mirror_path, "latest")))

    if "--mirror-path" in args:
        idx = args.index("--mirror-path")
        if idx + 1 < len(args):
            mirror_path = args[idx + 1]
            latest_dir = Path(mirror_path) / "latest"

    if "--all" in args:
        tiers = TIERS
    elif "--tier" in args:
        idx = args.index("--tier")
        for a in args[idx + 1:]:
            if a.startswith("--"):
                break
            if a in TIERS:
                tiers.append(a)
            else:
                print(f"Unknown tier: {a}. Valid: {', '.join(TIERS)}")
                sys.exit(1)

    if not tiers:
        tiers = TIERS

    cmd_poll(tiers, latest_dir, dry_run=dry_run, cron_mode=cron_mode)


if __name__ == "__main__":
    main()
