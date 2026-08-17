#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
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

REPO = Path(__file__).resolve().parent.parent  # worktree-relative (branch-model isolation)
SOURCES = REPO / "build" / "sources"
BLFS_DB = REPO / "build" / "blfs-packages.db"
SCHEMA_VERSION = 1


def _resolve_audits_dir():
    """Resolve where per-package audit JSONs land.

    decided 2026-05-29: audit-output classifier files live in the
    private project repo (intergenos-private:audits/per-package/), not in
    the public source tree. Resolution order:

      1. $INTERGENOS_AUDITS_DIR — explicit override.
      2. $INTERGENOS_PRIVATE_REPO/audits/per-package — env-located private
         repo + canonical audits subpath.
      3. <REPO>/../intergenos-private/audits/per-package — sibling layout
         (anchor-tracker's canonical default).
      4. <REPO>/build/audits — fallback for setups without the private
         repo configured; emits stderr warning naming the env-var
         override path.
    """
    import os
    env_dir = os.environ.get("INTERGENOS_AUDITS_DIR")
    if env_dir:
        return Path(env_dir)
    env_private = os.environ.get("INTERGENOS_PRIVATE_REPO")
    if env_private:
        return Path(env_private) / "audits" / "per-package"
    sibling = REPO.parent / "intergenos-private" / "audits" / "per-package"
    if sibling.parent.parent.exists():
        return sibling
    print(
        "WARNING: writing audit output to public build/audits/ "
        "(now gitignored). Set $INTERGENOS_AUDITS_DIR or "
        "$INTERGENOS_PRIVATE_REPO to use the private-repo canonical "
        "location.",
        file=sys.stderr,
    )
    return REPO / "build" / "audits"


AUDITS_DIR = _resolve_audits_dir()


# ----------------------------------------------------------------------
# Locating the package on disk
# ----------------------------------------------------------------------

def find_package(name: str) -> Path | None:
    for tier in ("toolchain", "core", "base", "desktop", "extra", "ai"):
        p = REPO / "packages" / tier / name
        if p.is_dir():
            return p
    # Fallback: nested sub-packages (e.g. packages/extra/intergen-web-ui/
    # websockets/) that the audit-coverage gate enumerates via a recursive
    # walk but this flat tier/name lookup misses — without this, such a
    # package can never get an audit record and fails the coverage gate
    # forever. Match a directory named exactly <name> that holds a package.yml.
    matches = sorted({m.parent for m in (REPO / "packages").rglob(f"{name}/package.yml")})
    if matches:
        return matches[0]
    return None


# ----------------------------------------------------------------------
# Source-tarball inspection (tar tf — no extraction needed)
# ----------------------------------------------------------------------

def find_source_tarball(pkg_yml: dict) -> Path | None:
    sources = pkg_yml.get("source") or []
    if not sources:
        return None
    s0 = sources[0]
    if not isinstance(s0, dict):
        return None
    version = str(pkg_yml.get("version", ""))
    # Prefer explicit `filename:` override if present — handles cases where
    # the URL template doesn't produce the actual on-disk filename (e.g.,
    # GitHub releases that emit `v${version}.tar.gz` but get renamed during
    # download, or upstream rename quirks).
    name = pkg_yml.get("name", "")
    def expand(s):
        return s.replace("${version}", version).replace("${name}", name)
    if s0.get("filename"):
        tarball = expand(s0["filename"])
    else:
        url = s0.get("url")
        if not url:
            return None
        tarball = expand(url).rstrip("/").rsplit("/", 1)[-1]
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
        # Distinguish intentionally-sourceless packages from missing-on-disk:
        #   source: []     → intentional (helpers, internal pkm/intergen/llama-cpp);
        #                    do NOT flag as blocker.
        #   missing yml    → real audit blocker.
        sources_raw = pkg_yml.get("source")
        if sources_raw == [] or sources_raw is None and not pkg_yml.get("source"):
            record["source_intentionally_sourceless"] = True
        else:
            record["_needs_review"].append("source-tarball-missing")

    # Cross-reference declared deps with BLFS truth, with proper normalization.
    if blfs and blfs.get("deps"):
        # Build alias maps for normalization. Many-to-many: a single BLFS
        # anchor can correspond to multiple InterGenOS packages (e.g.,
        # `libsigc` BLFS anchor ↔ libsigcpp + libsigcpp2 IGOS packages —
        # both satisfy).
        alias_blfs_to_igos: dict[str, set[str]] = {}
        if BLFS_DB.exists():
            adb = sqlite3.connect(str(BLFS_DB))
            for r in adb.execute("SELECT blfs_anchor, igos_name FROM aliases"):
                alias_blfs_to_igos.setdefault(r[0], set()).add(r[1])

        def alias_candidates(blfs_anchor: str) -> set[str]:
            """All InterGenOS names that satisfy this BLFS anchor."""
            return set(s.lower() for s in alias_blfs_to_igos.get(blfs_anchor, set()))

        # Build a set of our package names + their normalized forms. Include
        # BOTH build AND runtime deps: BLFS's single "required" class does not
        # split build-vs-runtime, so a dep we correctly declare under runtime:
        # (e.g. a pure-Python import dep like pytest→iniconfig/pluggy/pygments)
        # is satisfied, NOT a build-dep gap. Checking deps_build alone produced
        # one false-positive per such dep (the pytest-class reconciliation
        # noise the Phase-5 triage surfaced). (D-1, authorized 2026-07-01.)
        our_all_deps = our_deps_build + our_deps_runtime
        our_pkg_set = set(d.lower() for d in our_all_deps)
        # Also accept normalized base names (lib-foo-1.2 → lib-foo)
        for d in our_all_deps:
            base = re.sub(r'-?\d+(\.\d+)+.*$', '', d).lower()
            our_pkg_set.add(base)

        for dep in blfs["deps"]:
            if dep["type"] != "required":
                continue
            blfs_anchor = dep["anchor"]
            blfs_name = dep["name"]

            # Try multiple match strategies (case-insensitive)
            candidates: set[str] = set()
            # Aliases first — many-to-many
            candidates.update(alias_candidates(blfs_anchor))
            candidates.add(blfs_anchor.lower())
            candidates.add(blfs_name.split('-')[0].lower())
            # Also handle versioned anchors like 'gtk-3.24.51' → 'gtk3'
            # (collapse the major-version onto the name) and the
            # already-stripped base
            anchor_base = re.sub(r'-?\d+(\.\d+)+.*$', '', blfs_anchor).lower()
            candidates.add(anchor_base)
            # Pull out the leading major from a versioned anchor (e.g.
            # 'gtk-3' → 'gtk3', 'gtk-4' → 'gtk4')
            m = re.match(r'^([a-z][a-z0-9_-]*?)-(\d+)', blfs_anchor.lower())
            if m:
                candidates.add(f"{m.group(1)}{m.group(2)}")
                candidates.add(m.group(1))

            # Skip BLFS-specific names that are aggregate metapackages,
            # build-environment placeholders, or known-cycle false positives.
            BLFS_AGGREGATES = {
                # Xorg cluster aggregates
                "xorg libraries", "xorg-libraries",
                "xorg applications", "xorg-applications",
                "xorg7-lib", "xorg7-app", "xorg7-driver",
                "xorg7-font", "xorg7-proto", "xorg-cf-files",
                "x window system", "xorg-env",
                # BLFS env-setup pseudo-packages
                "server-mail",  # MTA placeholder; exim provides this
                "mta",          # ditto
                "tetex",        # texlive cluster
            }
            if blfs_anchor.lower() in BLFS_AGGREGATES or \
               blfs_name.lower() in BLFS_AGGREGATES:
                continue

            # Known cycles handled by our bootstrap variants — skip:
            # xdg-desktop-portal → portal-gnome/gtk/lxqt (the 2-pass cycle)
            # newt → slang (we satisfy via slang-pass1)
            #
            # (wayland-protocols → wayland was previously skipped here
            # under the incorrect claim that "wayland-protocols is XML-
            # only, no real dep on wayland". Scan A.2 surfaced 2026-05-12
            # that meson.build:11 hard-requires wayland-scanner at
            # configure time. The dep is real; the skip was removed
            # alongside the core→desktop retier of wayland-protocols.)
            KNOWN_CYCLES = {
                ("xdg-desktop-portal", "xdg-desktop-portal-gnome"),
                ("xdg-desktop-portal", "xdg-desktop-portal-gtk"),
                ("xdg-desktop-portal", "xdg-desktop-portal-lxqt"),
                # newt's slang dep is satisfied via slang-pass1 in core
                ("newt", "slang"),
                # SDL2 build needs only SDL2 itself; BLFS lists SDL3 as
                # alt-major-version — skip.
                ("sdl2", "sdl3"),
            }
            if (name, blfs_anchor) in KNOWN_CYCLES:
                continue

            # Deliberate architectural divergences — the BLFS-required dep is
            # genuinely NOT a build dep of OUR build, by design. Distinct from
            # KNOWN_CYCLES (bootstrap-ordering cycles): here the dep is
            # intentionally absent because we build the package differently
            # than BLFS does. Each entry is a curated, reviewed decision.
            DELIBERATE_DIVERGENCES = {
                # shadow is our pre-PAM FIRST pass (LFS Ch 8 order): it is
                # built before linux-pam exists, so it cannot — and must not —
                # depend on it. This is the documented SCC-3 auth cycle
                # {libpwquality, linux-pam, shadow, systemd}, dissolved in
                # production via chroot-build-ch8.sh: the PAM-enabled rebuild is
                # a SEPARATE package, shadow-pam (chroot-build-core-extra.sh),
                # which DOES declare linux-pam (build + runtime). BLFS has a
                # single shadow page covering both passes, hence the false
                # positive against our two-pass split. Triaged intentional
                # (open since the 2026-05-31 audit). Same shape as
                # ("newt","slang") above.
                ("shadow", "linux-pam"),
                # maturin: we build the binary directly via `cargo build`
                # (vendored, --offline --frozen) and hand-install the PEP517
                # Python shim. We do NOT use the pip/PEP517 build-isolation
                # path, so the upstream `setuptools-rust` build-backend
                # requirement does not apply to our build. Same shape as
                # ("sdl2","sdl3") above (BLFS dep not used by our build).
                ("maturin", "setuptools_rust"),
                # iniconfig: we build with the setuptools backend (declared
                # build: setuptools), NOT hatch-vcs. BLFS lists hatch-vcs as the
                # upstream PEP517 build-backend requirement, but our build does
                # not use it, so the dep genuinely does not apply — same shape as
                # ("maturin","setuptools_rust") above. (D-2, authorized
                # 2026-07-01; Phase-5 triage.)
                ("iniconfig", "hatch-vcs"),
            }
            if (name, blfs_anchor) in DELIBERATE_DIVERGENCES:
                continue

            if not (candidates & our_pkg_set):
                record["_mismatches"].append({
                    "field": "deps_build",
                    "issue": f"BLFS required dep '{blfs_anchor}' (name={blfs_name}) not in our dependencies.build or .runtime",
                    "tried_candidates": sorted(candidates),
                })

    return record


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--out", help="Output file (default: stdout)")
    ap.add_argument("--audited-by", default="audit-package.py")
    ap.add_argument("--save", action="store_true",
                    help=("Write to <AUDITS_DIR>/<name>.json. "
                          "AUDITS_DIR resolves from $INTERGENOS_AUDITS_DIR "
                          "or $INTERGENOS_PRIVATE_REPO/audits/per-package "
                          "or sibling intergenos-private/audits/per-package "
                          "(fallback to public build/audits/ with warning)."))
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
