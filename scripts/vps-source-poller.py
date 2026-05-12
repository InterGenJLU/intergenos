#!/usr/bin/env python3
"""InterGenOS VPS Source Poller — Upstream Version Checker (E1.A.3 Component 3)

Periodically checks upstream sources for newer versions than what's currently
in package.yml manifests. Downloads new candidates to the mirror's latest/
directory and writes an advisory updates.json. NEVER modifies package.yml —
updates are informational, requiring explicit owner adoption.

Design decisions (Lane B):
  Q1=B — serve upstream source tarballs as-is, never repackage
  Q2=A — mirror is primary source; hard-fail if unreachable during build

Usage:
    python3 scripts/vps-source-poller.py --all --dry-run     # Check all tiers
    python3 scripts/vps-source-poller.py --tier core base    # Specific tiers
    python3 scripts/vps-source-poller.py --cron              # Cron mode: quiet unless updates found

Environment:
    MIRROR_LATEST_DIR  — path to mirror's latest/ directory
    MIRROR_PATH        — base mirror path
"""

import hashlib
import json
import os
import subprocess
import sys
import yaml
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from upstream_check import get_checker, STRATEGIES


PROJECT_ROOT = Path(__file__).parent.parent
PACKAGES_DIR = PROJECT_ROOT / "packages"
TIERS = ["toolchain", "core", "base", "desktop", "extra", "ai"]


def sha256_file(path):
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha.update(chunk)
    return sha.hexdigest()


def resolve_url(url, name, version):
    parts = version.split(".")
    major = parts[0] if parts else ""
    major_minor = ".".join(parts[:2]) if len(parts) >= 2 else version
    return (url
            .replace("${version_major_minor}", major_minor)
            .replace("${version_major}", major)
            .replace("${version}", version)
            .replace("${name}", name))


def detect_strategy(url, pkg):
    upstream = pkg.get("upstream_check", {})
    if upstream.get("type"):
        return upstream["type"]

    url_lower = url.lower()
    if "ftpmirror.gnu.org" in url_lower or "ftp.gnu.org" in url_lower:
        return "gnu-ftp"
    if "github.com" in url_lower:
        return "github"
    if "pypi.org" in url_lower or "files.pythonhosted.org" in url_lower:
        return "pypi"
    if "download.gnome.org" in url_lower:
        return "gnome"
    if "freedesktop.org" in url_lower:
        return "freedesktop"
    if "crates.io" in url_lower:
        return "cargo"
    return "custom"


def check_upstream(pkg, src_url, current_version):
    strategy = detect_strategy(src_url, pkg)
    name = pkg.get("name", "?")
    checker = get_checker(strategy)
    if checker is None:
        return []
    try:
        raw = checker.check(src_url, current_version, name, pkg)
        return [{"version": c.version, "url": c.url, "source": c.source} for c in raw]
    except Exception as e:
        print(f"  {name}: checker error — {e}", flush=True)
        return []


def download_to_latest(url, dest_dir, timeout=300):
    filename = url.split("/")[-1].split("?")[0]
    dest = dest_dir / filename
    if dest.exists():
        return filename
    try:
        result = subprocess.run(
            ["wget", "-q", "--timeout=30", "--prefer-family=IPv4", "-O", str(dest), url],
            capture_output=True, timeout=timeout,
        )
        if result.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
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


def load_packages(tiers):
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


def cmd_poll(tiers, latest_dir, dry_run=False, cron_mode=False, parallel=8):
    packages = load_packages(tiers)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    updates_available = []
    checked = 0
    skipped = 0
    failed = 0

    if not cron_mode:
        print(f"\nVPS Source Poller — {now_iso}")
        print(f"  Tiers: {', '.join(tiers)}")
        print(f"  Packages: {len(packages)}")
        print(f"  Strategies: {', '.join(sorted(STRATEGIES))}")
        print(f"  Parallel workers: {parallel}\n")

    def _check_one(pkg):
        name = pkg.get("name", "?")
        version = str(pkg.get("version", ""))
        deps = []
        chk, skp, err = 0, 0, 0
        for src in pkg.get("source", []):
            url = src.get("url", "")
            if not url:
                continue
            chk += 1
            current_url = resolve_url(url, name, version)
            candidates = check_upstream(pkg, current_url, version)
            if not candidates:
                skp += 1
            for c in candidates:
                new_ver = c["version"]
                new_url = c["url"]
                if not dry_run:
                    latest_dir.mkdir(parents=True, exist_ok=True)
                    filename = download_to_latest(new_url, latest_dir)
                    if not filename:
                        err += 1
                        continue
                    sha = sha256_file(str(latest_dir / filename))
                else:
                    sha = "dry-run"

                severity = "patch"
                try:
                    cv = _parse_version(version)
                    nv = _parse_version(new_ver)
                    if nv[0] > cv[0]:
                        severity = "major"
                    elif len(nv) > 1 and len(cv) > 1 and nv[1] > cv[1]:
                        severity = "minor"
                except Exception:
                    pass

                deps.append({
                    "name": name,
                    "current": version,
                    "latest": new_ver,
                    "url": new_url,
                    "sha256": sha,
                    "detected": now_iso,
                    "severity": severity,
                    "strategy": detect_strategy(url, pkg),
                })
        return name, deps, chk, skp, err

    with ThreadPoolExecutor(max_workers=parallel) as executor:
        futures = {executor.submit(_check_one, pkg): pkg for pkg in packages}
        for future in as_completed(futures):
            name, deps, c, s, e = future.result()
            checked += c
            skipped += s
            failed += e
            updates_available.extend(deps)
            if deps and not cron_mode:
                for d in deps:
                    print(f"  {d['name']}: {d['current']} → {d['latest']} [{d['severity']}] ({d['strategy']})")

    if not cron_mode:
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

        if not cron_mode:
            print(f"\n  updates.json written with {len(updates_available)} entries")
            for u in updates_available:
                print(f"    {u['name']}: {u['current']} → {u['latest']} [{u['severity']}] ({u['strategy']})")
    elif not cron_mode:
        print("  No updates available.")


def _parse_version(v):
    import re
    parts = []
    for segment in re.split(r"[.-]", v):
        try:
            parts.append(int(segment))
        except ValueError:
            for char in segment:
                parts.append(ord(char))
    return tuple(parts)


def main():
    args = sys.argv[1:]
    if not args or "-h" in args or "--help" in args:
        print(__doc__)
        sys.exit(0)

    tiers = []
    dry_run = "--dry-run" in args
    cron_mode = "--cron" in args
    parallel = 8
    mirror_path = os.environ.get("MIRROR_PATH", str(PROJECT_ROOT / "build" / "mirror"))
    latest_dir = Path(os.environ.get("MIRROR_LATEST_DIR", os.path.join(mirror_path, "latest")))

    if "--mirror-path" in args:
        idx = args.index("--mirror-path")
        if idx + 1 < len(args):
            mirror_path = args[idx + 1]
            latest_dir = Path(mirror_path) / "latest"

    if "--parallel" in args:
        idx = args.index("--parallel")
        if idx + 1 < len(args):
            parallel = int(args[idx + 1])

    if "--all" in args:
        tiers = list(TIERS)
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
        tiers = list(TIERS)

    cmd_poll(tiers, latest_dir, dry_run=dry_run, cron_mode=cron_mode, parallel=parallel)


if __name__ == "__main__":
    main()
