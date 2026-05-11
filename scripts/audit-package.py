#!/usr/bin/env python3
"""audit-package.py — Per-package audit extractor.

For a given package name, produces a structured audit record (JSON) that
captures the build-truth for the package: build system, dependencies,
configure flags, patches, bundled libraries, install output, test
command, and reproducibility primitives.

The script does mechanical detection where possible (parsing tarball
contents, meson_options.txt, configure.ac, package.yml, build.sh, and
the BLFS book db) and flags ambiguous fields with `_needs_review: true`.
A parallel agent or maintainer uses the output as a starting point and
fills in the judgment calls.

Usage:
    python3 scripts/audit-package.py <name> [--out FILE] [--db PATH]

Output: JSON to stdout (or to --out file).
        Format conforms to package_audit table schema (see
        scripts/aggregate-package-audits.py).

Exit codes:
    0   — audit produced
    2   — package directory not found
    3   — source tarball not found / unreadable
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timezone

import yaml

REPO = Path("/mnt/intergenos")
SOURCES = REPO / "build" / "sources"
BLFS_DB = REPO / "build" / "blfs-packages.db"
AUDITS_DIR = REPO / "build" / "audits"
SCHEMA_VERSION = 1


# ----------------------------------------------------------------------
# Locating the package on disk
# ----------------------------------------------------------------------

def find_package(name: str) -> Path | None:
    for tier in ("toolchain", "core", "base", "desktop", "extra", "ai"):
        p = REPO / "packages" / tier / name
        if p.is_dir():
            return p
    return None


# ----------------------------------------------------------------------
# Source-tarball inspection (tar tf — no extraction needed)
# ----------------------------------------------------------------------

def find_source_tarball(pkg_yml: dict) -> Path | None:
    sources = pkg_yml.get("source") or []
    if not sources:
        return None
    s0 = sources[0]
    url = s0.get("url") if isinstance(s0, dict) else None
    if not url:
        return None
    version = str(pkg_yml.get("version", ""))
    tarball = url.replace("${version}", version).rstrip("/").rsplit("/", 1)[-1]
    p = SOURCES / tarball
    return p if p.exists() else None


def list_tarball_contents(tarball: Path) -> list[str]:
    """Return list of filenames inside the tarball. Empty list on failure."""
    try:
        suffix = tarball.suffix
        if suffix in (".gz", ".tgz", ".bz2", ".tbz2", ".xz", ".txz"):
            r = subprocess.run(
                ["tar", "-tf", str(tarball)],
                capture_output=True, text=True, timeout=120
            )
        elif suffix == ".zip":
            r = subprocess.run(
                ["unzip", "-l", str(tarball)],
                capture_output=True, text=True, timeout=120
            )
            # Skip the first 3 lines (header) and the last 2 (footer)
            lines = r.stdout.splitlines()
            content_lines = lines[3:-2] if len(lines) > 5 else []
            return [l.split(maxsplit=3)[-1] for l in content_lines if l.strip()]
        else:
            return []
        if r.returncode != 0:
            return []
        return [l.strip() for l in r.stdout.splitlines() if l.strip()]
    except Exception:
        return []


# ----------------------------------------------------------------------
# Build-system detection (from tarball contents)
# ----------------------------------------------------------------------

def detect_build_system(paths: list[str]) -> str:
    """Detect the primary build system from tarball file list."""
    # Look for build-system markers at root-or-near-root depth.
    # Tarballs typically have a single top-level dir; the build file lives
    # one level down.
    relevant = set()
    for p in paths:
        parts = p.split("/")
        if len(parts) <= 3:
            relevant.add(parts[-1])

    indicators = [
        ("meson.build", "meson"),
        ("CMakeLists.txt", "cmake"),
        ("Cargo.toml", "cargo"),
        ("configure", "autotools"),
        ("configure.ac", "autotools"),
        ("configure.in", "autotools"),
        ("pyproject.toml", "python"),
        ("setup.py", "python"),
        ("setup.cfg", "python"),
        ("Makefile.PL", "perl-makemaker"),
        ("Build.PL", "perl-build"),
        ("Rakefile", "ruby-rake"),
        ("package.json", "nodejs"),
        ("Makefile", "make"),
        ("makefile", "make"),
        ("GNUmakefile", "make"),
    ]
    for marker, system in indicators:
        if marker in relevant:
            return system
    return "unknown"


# ----------------------------------------------------------------------
# Bundled-libraries detection (Rule 5 — multi-source / vendor extracts)
# ----------------------------------------------------------------------

VENDOR_DIRS = ("vendor", "third_party", "third-party", "contrib", "subprojects",
               "deps", "external", "extern", "3rdparty", "third")


def detect_bundled_libs(paths: list[str]) -> list[str]:
    """Return list of vendor-style top-level dirs inside the tarball."""
    found = set()
    for p in paths:
        parts = p.split("/")
        if len(parts) >= 2:
            second = parts[1].lower()
            if second in VENDOR_DIRS:
                found.add(parts[1])
    return sorted(found)


# ----------------------------------------------------------------------
# Documentation files seen in the tarball
# ----------------------------------------------------------------------

def detect_docs_seen(paths: list[str]) -> list[str]:
    docs = set()
    interesting = re.compile(
        r"^(README|INSTALL|BUILDING|HACKING|NEWS|CHANGELOG|CONTRIBUTING|AUTHORS|TODO|NOTES)",
        re.IGNORECASE,
    )
    for p in paths:
        last = p.rstrip("/").rsplit("/", 1)[-1]
        m = interesting.match(last)
        if m:
            docs.add(m.group(1).upper())
    return sorted(docs)


# ----------------------------------------------------------------------
# Parsing meson_options.txt / configure.ac to extract option declarations
# ----------------------------------------------------------------------

def extract_one_file(tarball: Path, member: str, dest: Path) -> Path | None:
    """Extract a single named member from tarball to dest. Returns path or None."""
    try:
        if tarball.suffix == ".zip":
            r = subprocess.run(
                ["unzip", "-q", "-o", str(tarball), member, "-d", str(dest)],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0:
                p = dest / member
                return p if p.exists() else None
        else:
            r = subprocess.run(
                ["tar", "-xf", str(tarball), "-C", str(dest), member],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0:
                p = dest / member
                return p if p.exists() else None
    except Exception:
        return None
    return None


def parse_meson_options(text: str) -> list[dict]:
    """Parse meson_options.txt; return list of {name, type, value, description}."""
    out = []
    # Match option('name', type:'string', value:'default', description:'...')
    # over multiple lines if needed
    for m in re.finditer(
        r"option\s*\(\s*['\"]([^'\"]+)['\"]\s*,(.*?)\)",
        text, re.DOTALL,
    ):
        name = m.group(1)
        body = m.group(2)
        opt_type = ""
        value = ""
        desc = ""
        tm = re.search(r"type\s*:\s*['\"]([^'\"]+)['\"]", body)
        if tm: opt_type = tm.group(1)
        vm = re.search(r"value\s*:\s*['\"]([^'\"]*)['\"]", body)
        if vm: value = vm.group(1)
        dm = re.search(r"description\s*:\s*['\"]([^'\"]*)['\"]", body)
        if dm: desc = dm.group(1)
        out.append({"name": name, "type": opt_type, "default": value, "description": desc})
    return out


def parse_configure_ac_options(text: str) -> list[dict]:
    """Parse configure.ac AC_ARG_ENABLE / AC_ARG_WITH macros."""
    out = []
    for kind, pattern in (
        ("enable", r"AC_ARG_ENABLE\(\[?([^],\s]+)\]?,\s*\[?([^]]*)\]?"),
        ("with", r"AC_ARG_WITH\(\[?([^],\s]+)\]?,\s*\[?([^]]*)\]?"),
    ):
        for m in re.finditer(pattern, text):
            out.append({
                "kind": kind,
                "name": m.group(1),
                "help": m.group(2)[:200],
            })
    return out


# ----------------------------------------------------------------------
# Parsing our package.yml + build.sh
# ----------------------------------------------------------------------

def parse_build_sh_configure(build_sh: Path) -> str:
    """Extract configure() function body text from build.sh."""
    if not build_sh.exists():
        return ""
    text = build_sh.read_text()
    m = re.search(r'configure\s*\(\s*\)\s*\{([\s\S]*?)^\}', text, re.MULTILINE)
    return m.group(1) if m else ""


CONFIGURE_FLAG_RE = re.compile(r'(--[a-z][a-z0-9-]*(?:=[^\s\\"]+)?)')
MESON_OPTION_RE = re.compile(r'-D\s*([A-Za-z_][A-Za-z_0-9-]*)\s*=\s*(\S+)')


def parse_flags_from_configure_body(body: str) -> dict:
    """Return {autotools_flags: [...], meson_options: [...]} from build.sh's
    configure() body text."""
    autotools = sorted(set(CONFIGURE_FLAG_RE.findall(body)))
    meson_pairs = [{"name": m.group(1), "value": m.group(2).rstrip(',\\')}
                   for m in MESON_OPTION_RE.finditer(body)]
    # Dedupe by name (last-wins)
    meson_by_name = {}
    for pair in meson_pairs:
        meson_by_name[pair["name"]] = pair
    return {
        "autotools_flags": autotools,
        "meson_options": list(meson_by_name.values()),
    }


# ----------------------------------------------------------------------
# BLFS db cross-reference
# ----------------------------------------------------------------------

def blfs_lookup(name: str) -> dict | None:
    if not BLFS_DB.exists():
        return None
    db = sqlite3.connect(str(BLFS_DB))
    db.row_factory = sqlite3.Row

    # Try direct anchor match, then alias table
    rows = list(db.execute(
        "SELECT id, anchor_id, name, version FROM packages WHERE anchor_id = ? OR name = ?",
        (name, name),
    ))
    if not rows:
        # Alias table
        for r in db.execute("SELECT blfs_anchor FROM aliases WHERE igos_name = ?", (name,)):
            anchor = r["blfs_anchor"]
            rows = list(db.execute(
                "SELECT id, anchor_id, name, version FROM packages WHERE anchor_id = ?",
                (anchor,),
            ))
            if rows:
                break
    if not rows:
        return None

    pkg = rows[0]
    deps = []
    for r in db.execute(
        "SELECT dep_anchor, dep_name, dep_version, dep_type, note "
        "FROM dependencies WHERE package_id = ?",
        (pkg["id"],),
    ):
        deps.append({
            "anchor": r["dep_anchor"],
            "name": r["dep_name"],
            "version": r["dep_version"],
            "type": r["dep_type"],
            "note": r["note"],
        })
    patches = []
    for r in db.execute(
        "SELECT filename, url, required FROM patches WHERE package_id = ?",
        (pkg["id"],),
    ):
        patches.append({"filename": r["filename"], "url": r["url"],
                        "required": bool(r["required"])})
    return {
        "anchor_id": pkg["anchor_id"],
        "blfs_name": pkg["name"],
        "blfs_version": pkg["version"],
        "deps": deps,
        "patches": patches,
    }


# ----------------------------------------------------------------------
# Main audit
# ----------------------------------------------------------------------

def audit(name: str, audited_by: str = "audit-package.py") -> dict:
    pkg_dir = find_package(name)
    if not pkg_dir:
        raise SystemExit(2)

    yml_path = pkg_dir / "package.yml"
    pkg_yml = yaml.safe_load(yml_path.read_text())
    tier = pkg_yml.get("tier")
    version = str(pkg_yml.get("version", ""))

    tarball = find_source_tarball(pkg_yml)
    paths = list_tarball_contents(tarball) if tarball else []
    if tarball and not paths:
        # Tarball exists but we couldn't list — likely format issue
        notes_missing_tarball = f"tarball at {tarball} unreadable"
    else:
        notes_missing_tarball = "" if tarball else "no source tarball declared or downloaded"

    build_system = detect_build_system(paths) if paths else "unknown"
    bundled = detect_bundled_libs(paths) if paths else []
    docs_seen = detect_docs_seen(paths) if paths else []

    build_sh = pkg_dir / "build.sh"
    cfg_body = parse_build_sh_configure(build_sh)
    flags = parse_flags_from_configure_body(cfg_body)

    # For meson packages, attempt to extract meson_options.txt and parse it
    upstream_options = []
    if build_system == "meson" and tarball:
        # Find a path that looks like <top>/meson_options.txt
        candidate = next((p for p in paths if p.endswith("/meson_options.txt")), None)
        if candidate:
            with tempfile.TemporaryDirectory() as tmp:
                extracted = extract_one_file(tarball, candidate, Path(tmp))
                if extracted and extracted.exists():
                    upstream_options = parse_meson_options(extracted.read_text(errors="replace"))
    # For autotools, try configure.ac
    elif build_system == "autotools" and tarball:
        candidate = next((p for p in paths if p.endswith("/configure.ac")), None)
        if candidate:
            with tempfile.TemporaryDirectory() as tmp:
                extracted = extract_one_file(tarball, candidate, Path(tmp))
                if extracted and extracted.exists():
                    upstream_options = parse_configure_ac_options(extracted.read_text(errors="replace"))

    # Our declared deps
    deps = pkg_yml.get("dependencies") or {}
    our_deps_build = list(deps.get("build") or [])
    our_deps_host = list(deps.get("host") or [])
    our_deps_runtime = list(deps.get("runtime") or [])

    # BLFS book truth
    blfs = blfs_lookup(name) or {}

    record = {
        # Identity
        "name": name,
        "version": version,
        "tier": tier,
        "package_dir": str(pkg_dir.relative_to(REPO)),

        # Source
        "source_url": (pkg_yml.get("source") or [{}])[0].get("url") if pkg_yml.get("source") else None,
        "source_sha256": (pkg_yml.get("source") or [{}])[0].get("sha256") if pkg_yml.get("source") else None,
        "source_tarball": tarball.name if tarball else None,
        "source_tarball_exists": tarball is not None,
        "_notes_source": notes_missing_tarball,

        # Build system + tarball inspection
        "build_system": build_system,
        "bundled_libs": bundled,
        "docs_seen": docs_seen,
        "tarball_files_count": len(paths),

        # Our declared state
        "our_deps_build": our_deps_build,
        "our_deps_host": our_deps_host,
        "our_deps_runtime": our_deps_runtime,
        "our_autotools_flags": flags["autotools_flags"],
        "our_meson_options": flags["meson_options"],
        "our_patches": pkg_yml.get("patches") or [],

        # Upstream truth (from tarball)
        "upstream_options": upstream_options,

        # BLFS book truth (from db)
        "blfs": blfs,

        # Reproducibility primitives — heuristic
        "reproducibility": {
            # These are placeholder; require deeper inspection
            "source_date_epoch_supported": None,
            "parallel_build_supported": None,
            "deterministic_install": None,
            "_notes": "manual review required",
        },

        # Expected install output — placeholder for agent fill-in
        "expected_binaries": [],
        "expected_libs": [],
        "expected_headers": [],
        "expected_pkgconfig": [],

        # Tests
        "test_command": None,
        "test_known_failures": [],

        # Reconciliation flags — populated by the agent or aggregator
        "_needs_review": [],
        "_mismatches": [],

        # Audit metadata
        "audit_version": SCHEMA_VERSION,
        "audited_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "audited_by": audited_by,
    }

    # Auto-detect some _needs_review triggers
    if build_system == "unknown" and paths:
        record["_needs_review"].append("build-system-undetected")
    if bundled:
        # Rule 5 trigger — verify build.sh extracts each vendor tarball
        cfg_text = cfg_body
        for b in bundled:
            if b not in cfg_text and "tar xf" not in cfg_text.lower():
                record["_needs_review"].append(f"bundled-lib-{b}-extract-unclear")
    if not tarball:
        record["_needs_review"].append("source-tarball-missing")
    # Cross-reference declared deps with BLFS truth
    if blfs and blfs.get("deps"):
        blfs_required = [d["name"].lower() for d in blfs["deps"] if d["type"] == "required"]
        our_lower = [d.lower() for d in our_deps_build]
        for r in blfs_required:
            # Strip version suffixes / case
            r_short = re.split(r'[-_]\d', r)[0].lower()
            if r_short not in [d.split('-')[0] for d in our_lower]:
                record["_mismatches"].append({
                    "field": "deps_build",
                    "issue": f"BLFS required dep '{r}' not in our dependencies.build",
                })

    return record


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--out", help="Output file (default: stdout)")
    ap.add_argument("--audited-by", default="audit-package.py")
    ap.add_argument("--save", action="store_true",
                    help="Write to build/audits/<name>.json")
    args = ap.parse_args()

    record = audit(args.name, audited_by=args.audited_by)

    out_text = json.dumps(record, indent=2, sort_keys=True)

    if args.save:
        AUDITS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = AUDITS_DIR / f"{args.name}.json"
        out_path.write_text(out_text + "\n")
        print(f"wrote {out_path}")
    elif args.out:
        Path(args.out).write_text(out_text + "\n")
    else:
        print(out_text)


if __name__ == "__main__":
    main()
