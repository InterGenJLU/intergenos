#!/usr/bin/env python3
"""Silent-feature-loss preflight gate.

Promoted from prototype (Scan B primary + supplement) used during the
Build #8 → Build #9 remediation arc. Catches the class of bug where a
package is declared as a build-time dependency in another package's
``package.yml``, but the consumer's configure script:

* Fails to detect the dep at probe time ("checking for X... no") and
  defaults to a soft-disable instead of erroring out, OR
* Reports the feature as ``no`` / ``disabled`` / ``None`` in its
  end-of-configure summary block.

The canonical case that drove this scan into existence: systemd built
without 15 in-tree security/hardening deps (libseccomp, libapparmor,
libcryptsetup, libfido2, libgcrypt, gnutls, …) plus ukify/homed/man/
sysupdate disabled — none of which surfaced as halts because configure
chose silent defaults. The build "succeeded" but the systemd binary
shipped without the features we claimed.

Three classification passes:
  Pass 1 — DECLARED-FAILED:
    For each consumer pkg's declared dep, search the configure log for
    detection-failure patterns. If hit → dep was declared but configure
    didn't see it.
  Pass 2 — BLFS-{REQUIRED,RECOMMENDED}-UNDECLARED-FAILED:
    For each pkg's BLFS-truth deps (required/recommended) NOT declared
    in our package.yml, search the log. If hit → dep is missing from our
    declared set AND configure tried to find it.
  Pass 3 — BLFS-OPTIONAL-INTREE-FAILED:
    For each BLFS-optional dep that EXISTS in our tree, search the log.
    If hit → we have the package but didn't declare/wire it.

Plus a supplemental scan (formerly ``scan_summary_disables``):
  - SUMMARY-DISABLED:    end-of-configure "feature: disabled/no/None" lines
  - MESON-NOT-FOUND-INTREE: ``Dependency X found: NO`` where X is in our tree

Chroot-data presence:
  This scan reads CHROOT_INSTALLED + CHROOT_LOGS from the build VM. If
  those paths don't exist (e.g., running on a workstation without a
  mounted chroot), the gate SKIPS with an informational message — exit 0,
  no block — because there's no prior-build state to audit. The scan
  blocks only when chroot data is present AND findings surface.

Exit codes:
  0 — clean (no findings, OR chroot data absent → skip-with-info)
  1 — findings present (build kickoff should halt)
  2 — environment problem (repo source missing — distinct from chroot absent)

Usage:
  scripts/preflight-silent-loss.py             # gate mode (terse pass/fail)
  scripts/preflight-silent-loss.py --report    # verbose: emit JSON + TSV
  scripts/preflight-silent-loss.py --root /alt/repo --chroot /alt/igos

Environment:
  INTERGENOS_ROOT    — repo root (default: autodetect from script location)
  INTERGENOS_CHROOT  — chroot mount point (default: /mnt/igos)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


# ----------------------------------------------------------------------
# Path discovery + configuration
# ----------------------------------------------------------------------

def discover_repo_root(arg_root: str | None) -> Path:
    if arg_root:
        return Path(arg_root).resolve()
    env_root = os.environ.get("INTERGENOS_ROOT")
    if env_root:
        return Path(env_root).resolve()
    return Path(__file__).resolve().parent.parent


def discover_chroot_root(arg_chroot: str | None) -> Path:
    if arg_chroot:
        return Path(arg_chroot).resolve()
    env_chroot = os.environ.get("INTERGENOS_CHROOT")
    if env_chroot:
        return Path(env_chroot).resolve()
    return Path("/mnt/igos")


# ----------------------------------------------------------------------
# Name-aliasing for log matching
# ----------------------------------------------------------------------

def name_variants(name: str) -> set[str]:
    """Search-variant set for matching dep names in log lines."""
    name = re.sub(r"-[\d.]+$", "", name)
    variants = {name}
    base = re.sub(r"-(pass\d+|bootstrap|core|host)$", "", name)
    variants.add(base)
    if base.startswith("lib"):
        variants.add(base[3:])
    else:
        variants.add("lib" + base)
    stripped = re.sub(r"\d+$", "", base)
    if stripped and stripped != base:
        variants.add(stripped)
        if stripped.startswith("lib"):
            variants.add(stripped[3:])
        else:
            variants.add("lib" + stripped)
    variants |= {v.lower() for v in list(variants)}
    return {v.rstrip(".-") for v in variants if v and len(v) >= 3}


# ----------------------------------------------------------------------
# package.yml parser (stdlib — same shape as preflight-build-order)
# ----------------------------------------------------------------------

def parse_deps_build(pkg_yml_path: Path) -> list[str]:
    if not pkg_yml_path.is_file():
        return []
    deps: list[str] = []
    in_deps = False
    in_build = False
    deps_indent = -1
    build_indent = -1
    with pkg_yml_path.open() as fp:
        for raw in fp:
            line = raw.rstrip("\n")
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(line) - len(stripped)
            if indent == 0:
                in_deps = stripped.startswith("dependencies:")
                in_build = False
                deps_indent = 0 if in_deps else -1
                continue
            if not in_deps:
                continue
            if not in_build:
                if stripped.startswith("build:") and indent > deps_indent:
                    in_build = True
                    build_indent = indent
                continue
            if indent < build_indent:
                in_build = False
                continue
            if indent == build_indent:
                if stripped.startswith("- "):
                    item = stripped[2:].strip().strip('"').strip("'")
                    if "#" in item:
                        item = item.split("#", 1)[0].strip()
                    if item:
                        deps.append(item)
                else:
                    in_build = False
                continue
            if stripped.startswith("- "):
                item = stripped[2:].strip().strip('"').strip("'")
                if "#" in item:
                    item = item.split("#", 1)[0].strip()
                if item:
                    deps.append(item)
    return deps


def find_package_yml(packages_dir: Path, pkg_name: str) -> Path | None:
    candidates = [pkg_name, f"{pkg_name}-core", f"{pkg_name}-pass1"]
    for tier_dir in packages_dir.iterdir():
        if not tier_dir.is_dir():
            continue
        for name in candidates:
            candidate = tier_dir / name / "package.yml"
            if candidate.is_file():
                return candidate
    return None


def in_tree(packages_dir: Path, pkg_name: str) -> bool:
    for tier_dir in packages_dir.iterdir():
        if not tier_dir.is_dir():
            continue
        if (tier_dir / pkg_name / "package.yml").is_file():
            return True
    return False


# ----------------------------------------------------------------------
# Installed-package list (from chroot)
# ----------------------------------------------------------------------

def list_installed_packages(installed_dir: Path) -> list[tuple[str, str]]:
    """Return [(pkg_name, manifest_name)] sorted by manifest name."""
    out = []
    for entry in sorted(installed_dir.iterdir()):
        manifest = entry.name
        if manifest.startswith(".") or manifest.endswith(".bak"):
            continue
        m = re.match(r"^(.+?)-(\d.*)$", manifest)
        pkg_name = m.group(1) if m else manifest
        out.append((pkg_name, manifest))
    return out


def find_log_for_pkg(logs_dir: Path, pkg_name: str) -> Path | None:
    candidates = sorted(logs_dir.glob(f"{pkg_name}-*.log"),
                        key=lambda p: p.stat().st_mtime,
                        reverse=True)
    return candidates[0] if candidates else None


# ----------------------------------------------------------------------
# BLFS truth lookup
# ----------------------------------------------------------------------

class BlfsLookup:
    def __init__(self, db_path: Path):
        self.con = sqlite3.connect(str(db_path))
        self.aliases: dict[str, list[str]] = {}
        for igos_name, blfs_anchor in self.con.execute(
            "SELECT igos_name, blfs_anchor FROM aliases"
        ):
            self.aliases.setdefault(igos_name, []).append(blfs_anchor)

    def lookup_deps(self, pkg_name: str) -> list[tuple[str, str]]:
        base = re.sub(r"-(pass\d+|bootstrap|core|host)$", "", pkg_name)
        rows = self.con.execute(
            "SELECT id FROM packages WHERE anchor_id = ?", (base,)
        ).fetchall()
        if not rows:
            rows = self.con.execute(
                "SELECT id FROM packages WHERE name = ?", (base,)
            ).fetchall()
        if not rows and base in self.aliases:
            for alias in self.aliases[base]:
                rows = self.con.execute(
                    "SELECT id FROM packages WHERE name = ? OR anchor_id = ?",
                    (alias, alias),
                ).fetchall()
                if rows:
                    break
        if not rows:
            return []
        pkg_id = rows[0][0]
        return self.con.execute(
            "SELECT dep_name, dep_type FROM dependencies WHERE package_id = ?",
            (pkg_id,),
        ).fetchall()


# ----------------------------------------------------------------------
# Pattern matching for failed-detection lines
# ----------------------------------------------------------------------

_DETECTION_PATTERNS = [
    (r"checking for {name}\.\.\.\s*no\b", "autotools-checking-for"),
    (r"checking for {name}\.h\.\.\.\s*no\b", "autotools-checking-header"),
    (r"checking for {name}-[\d.]+\.\.\.\s*no\b", "autotools-checking-versioned"),
    (r"checking for {name}-config\.\.\.\s*no\b", "autotools-checking-config-script"),
    (r"^[ \t]+{name}\s*:\s*no\b", "autotools-summary-feature-no"),
    (r"^[ \t]+{name}\s*:\s*disabled\b", "autotools-summary-feature-disabled"),
    (r"^[ \t]+{name}\s*:\s*None\b", "autotools-summary-feature-none"),
    (r"^[ \t]+(with|--with)[-_]{name}\s*:\s*no\b", "autotools-summary-with-no"),
    (r"^[ \t]+{name} support\s*:\s*no\b", "autotools-summary-support-no"),
    (r"Run-time dependency {name}.* found:\s*NO\b", "meson-runtime-dep"),
    (r"Dependency {name}.* found:\s*NO\b", "meson-dep"),
    (r"Library {name} found:\s*NO\b", "meson-library"),
    (r"Program {name}.* found:\s*NO\b", "meson-program"),
    (r"Could NOT find {name}\b", "cmake-could-not-find"),
    (r"\b{name}_FOUND\s*[:=].*\bFALSE\b", "cmake-found-false"),
    (r"Package {name} was not found", "pkgconfig-not-found"),
    (r"Package '{name}', required by .*, not found", "pkgconfig-required-not-found"),
    (r"configure: error: {name} (not found|is required|required)", "configure-error-required"),
]


def scan_log_for_dep(log_text: str, dep_name: str) -> list[tuple[int, str, str]]:
    variants = name_variants(dep_name)
    findings = []
    seen_keys: set[tuple[int, str]] = set()
    for variant in variants:
        if len(variant) < 3:
            continue
        v_esc = re.escape(variant)
        for tmpl, kind in _DETECTION_PATTERNS:
            pat = re.compile(tmpl.format(name=v_esc), re.IGNORECASE | re.MULTILINE)
            for m in pat.finditer(log_text):
                line_no = log_text.count("\n", 0, m.start()) + 1
                key = (line_no, kind)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                line_start = log_text.rfind("\n", 0, m.start()) + 1
                line_end = log_text.find("\n", m.end())
                if line_end == -1:
                    line_end = len(log_text)
                match_line = log_text[line_start:line_end].strip()
                findings.append((line_no, kind, match_line))
    return findings


# ----------------------------------------------------------------------
# Supplement: end-of-configure summary block + meson "found: NO"
# ----------------------------------------------------------------------

_SUMMARY_LINE = re.compile(
    r"^[ \t]+([A-Za-z][A-Za-z0-9_+.-]+)\s*[:=]\s*(disabled|no|NO|None|FALSE|off)\b",
    re.MULTILINE,
)
_MESON_FOUND_NO = re.compile(
    r"^(Run-time dependency|Dependency|Library|Program)\s+(\S+).*found:\s*NO\b",
    re.MULTILINE,
)

_NOISE = {
    # Cross-compile / build-host probes
    "windows", "windows.h", "win32", "winsock", "winsock2",
    "dlltool", "sysroot", "mt", "windres", "vfork.h", "minix",
    "kqueue", "kevent", "epoll", "inotify", "darwin", "macos", "msys",
    "_LARGEFILE_SOURCE", "_FILE_OFFSET_BITS", "_LARGE_FILES",
    "code", "danted",
    "alloca", "memcpy", "vsnprintf", "snprintf", "fseeko",
    "valgrind", "gprof", "lcov", "gcov", "address-sanitizer", "asan",
    "static", "shared", "ipv6", "tests", "test", "examples", "docs",
    "debug", "profile", "coverage", "fuzzing", "fuzz", "oss_fuzz",
    "rpath", "dependency", "soname", "versioning",
}


def is_noise(feat: str) -> bool:
    flat = feat.lower().replace("-", "").replace("_", "").replace(".", "")
    if flat in {n.lower().replace("-", "").replace("_", "").replace(".", "") for n in _NOISE}:
        return True
    if len(feat) <= 2:
        return True
    return False


def scan_summary_block(log_text: str, packages_dir: Path) -> tuple[list[dict], list[dict]]:
    summary_feats: list[dict] = []
    seen = set()
    for m in _SUMMARY_LINE.finditer(log_text):
        feat = m.group(1)
        value = m.group(2)
        if is_noise(feat):
            continue
        key = (feat.lower(), value.lower())
        if key in seen:
            continue
        seen.add(key)
        line_no = log_text.count("\n", 0, m.start()) + 1
        summary_feats.append({
            "feature": feat,
            "value": value,
            "line_no": line_no,
            "line": m.group(0).strip(),
        })

    meson_no: list[dict] = []
    for m in _MESON_FOUND_NO.finditer(log_text):
        kind = m.group(1)
        target = m.group(2).rstrip(",")
        if is_noise(target):
            continue
        line_no = log_text.count("\n", 0, m.start()) + 1
        tree_match = in_tree(packages_dir, target.replace("lib", "")) or in_tree(packages_dir, target)
        meson_no.append({
            "kind": kind,
            "target": target,
            "in_tree": tree_match,
            "line_no": line_no,
            "line": m.group(0).strip(),
        })
    return summary_feats, meson_no


# ----------------------------------------------------------------------
# Main scan loop
# ----------------------------------------------------------------------

def scan(repo: Path, chroot: Path) -> dict:
    """Run all passes. Returns a result dict with findings + metadata."""
    packages_dir = repo / "packages"
    blfs_db = repo / "build" / "blfs-packages.db"
    chroot_installed = chroot / "var/lib/igos/packages"
    chroot_logs = chroot / "mnt/intergenos/build/logs"

    result: dict = {
        "repo": str(repo),
        "chroot": str(chroot),
        "blfs_db_present": blfs_db.is_file(),
        "chroot_installed_present": chroot_installed.is_dir(),
        "chroot_logs_present": chroot_logs.is_dir(),
        "skipped": False,
        "skip_reason": None,
        "installed_count": 0,
        "findings": [],
        "log_missing": [],
        "yml_missing": [],
        "blfs_no_truth": [],
        "summary_disabled": {},
        "meson_not_found_intree": {},
    }

    if not blfs_db.is_file():
        result["skipped"] = True
        result["skip_reason"] = f"BLFS db not at {blfs_db}"
        return result
    if not chroot_installed.is_dir() or not chroot_logs.is_dir():
        result["skipped"] = True
        result["skip_reason"] = (
            f"chroot data absent (installed={chroot_installed.is_dir()}, "
            f"logs={chroot_logs.is_dir()}) — no prior-build state to audit"
        )
        return result

    blfs = BlfsLookup(blfs_db)
    installed = list_installed_packages(chroot_installed)
    result["installed_count"] = len(installed)

    findings: list[dict] = []
    summary_per_pkg: dict[str, list[dict]] = {}
    meson_per_pkg: dict[str, list[dict]] = {}

    for pkg_name, manifest in installed:
        log_path = find_log_for_pkg(chroot_logs, pkg_name)
        if log_path is None:
            result["log_missing"].append(pkg_name)
            continue
        try:
            log_text = log_path.read_text(errors="replace")
        except OSError:
            continue

        yml = find_package_yml(packages_dir, pkg_name)
        declared_deps = set(parse_deps_build(yml)) if yml else set()
        if yml is None:
            result["yml_missing"].append(pkg_name)

        blfs_deps = blfs.lookup_deps(pkg_name)
        if not blfs_deps:
            result["blfs_no_truth"].append(pkg_name)

        blfs_by_type: dict[str, list[str]] = {}
        for dep_name, dep_type in blfs_deps:
            blfs_by_type.setdefault(dep_type, []).append(dep_name)

        # Pass 1: declared-failed
        for dep in declared_deps:
            for line_no, kind, line in scan_log_for_dep(log_text, dep):
                findings.append({
                    "type": "DECLARED-FAILED",
                    "pkg": pkg_name,
                    "manifest": manifest,
                    "dep": dep,
                    "log": log_path.name,
                    "line_no": line_no,
                    "kind": kind,
                    "match": line[:200],
                })

        # Pass 2: BLFS required/recommended not declared, failed in log
        for dep_type, dep_list in blfs_by_type.items():
            if dep_type not in ("required", "recommended"):
                continue
            for dep in dep_list:
                if dep in declared_deps:
                    continue
                for line_no, kind, line in scan_log_for_dep(log_text, dep):
                    findings.append({
                        "type": f"BLFS-{dep_type.upper()}-UNDECLARED-FAILED",
                        "pkg": pkg_name,
                        "manifest": manifest,
                        "dep": dep,
                        "log": log_path.name,
                        "line_no": line_no,
                        "kind": kind,
                        "match": line[:200],
                    })

        # Pass 3: BLFS optional in-tree not declared, failed in log
        for dep in blfs_by_type.get("optional", []):
            if dep in declared_deps:
                continue
            if find_package_yml(packages_dir, dep) is None:
                continue
            for line_no, kind, line in scan_log_for_dep(log_text, dep):
                findings.append({
                    "type": "BLFS-OPTIONAL-INTREE-FAILED",
                    "pkg": pkg_name,
                    "manifest": manifest,
                    "dep": dep,
                    "log": log_path.name,
                    "line_no": line_no,
                    "kind": kind,
                    "match": line[:200],
                })

        # Supplement: summary-disabled + meson-not-found-in-tree
        summary_feats, meson_no = scan_summary_block(log_text, packages_dir)
        if summary_feats:
            summary_per_pkg[pkg_name] = summary_feats
        intree_meson = [m for m in meson_no if m["in_tree"]]
        if intree_meson:
            meson_per_pkg[pkg_name] = intree_meson

    # Deduplicate BLFS-* against DECLARED-FAILED for same (pkg, dep-base)
    def dep_base(d: str) -> str:
        return re.sub(r"-[\d.]+$", "", d)

    declared_failed_keys = {
        (f["pkg"], dep_base(f["dep"]))
        for f in findings if f["type"] == "DECLARED-FAILED"
    }
    findings = [
        f for f in findings
        if not (f["type"].startswith("BLFS-")
                and (f["pkg"], dep_base(f["dep"])) in declared_failed_keys)
    ]

    result["findings"] = findings
    result["summary_disabled"] = summary_per_pkg
    result["meson_not_found_intree"] = meson_per_pkg
    return result


# ----------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------

def emit_summary(result: dict) -> None:
    print("=== preflight-silent-loss ===")
    print(f"Repo:    {result['repo']}")
    print(f"Chroot:  {result['chroot']}")

    if result["skipped"]:
        print(f"SKIP — {result['skip_reason']}")
        print()
        print("PASS — chroot data absent; nothing to audit. "
              "Gate intentionally does not block first-build scenarios.")
        return

    print(f"BLFS db: present ({result['blfs_db_present']})")
    print(f"Installed packages scanned: {result['installed_count']}")
    print(f"Logs missing: {len(result['log_missing'])}")
    print(f"package.yml missing: {len(result['yml_missing'])}")
    print(f"BLFS truth not found: {len(result['blfs_no_truth'])}")
    print()

    findings = result["findings"]
    summary_per_pkg = result["summary_disabled"]
    meson_per_pkg = result["meson_not_found_intree"]
    print(f"TOTAL FINDINGS: {len(findings)} "
          f"(+ {sum(len(v) for v in summary_per_pkg.values())} summary-disabled lines "
          f"+ {sum(len(v) for v in meson_per_pkg.values())} meson-in-tree-NO lines)")

    if not findings and not summary_per_pkg and not meson_per_pkg:
        print()
        print("PASS — no silent feature losses detected in prior-build chroot.")
        return

    by_type: dict[str, list[dict]] = {}
    for f in findings:
        by_type.setdefault(f["type"], []).append(f)

    for t in ("DECLARED-FAILED",
              "BLFS-REQUIRED-UNDECLARED-FAILED",
              "BLFS-RECOMMENDED-UNDECLARED-FAILED",
              "BLFS-OPTIONAL-INTREE-FAILED"):
        items = by_type.get(t, [])
        if items:
            print(f"  {t}: {len(items)}")

    print()
    print("=== First 10 unique (pkg, dep, type) tuples ===")
    seen: dict[tuple, list[dict]] = {}
    for f in findings:
        key = (f["type"], f["pkg"], f["dep"])
        seen.setdefault(key, []).append(f)
    for i, ((t, pkg, dep), entries) in enumerate(sorted(seen.items())):
        if i >= 10:
            print(f"  ... ({len(seen) - 10} more unique tuples)")
            break
        kinds = sorted(set(e["kind"] for e in entries))
        print(f"  [{t}] {pkg} <- {dep}  ({len(entries)} hits, kinds={','.join(kinds)})")

    if summary_per_pkg:
        print()
        print("=== Summary-disabled features (first 10 packages) ===")
        for i, pkg in enumerate(sorted(summary_per_pkg)):
            if i >= 10:
                print(f"  ... ({len(summary_per_pkg) - 10} more packages)")
                break
            feats = summary_per_pkg[pkg][:3]
            feat_str = ", ".join(f"{f['feature']}={f['value']}" for f in feats)
            more = (f" (+{len(summary_per_pkg[pkg]) - 3} more)"
                    if len(summary_per_pkg[pkg]) > 3 else "")
            print(f"  {pkg}: {feat_str}{more}")

    if meson_per_pkg:
        print()
        print("=== Meson-not-found where target IS in our tree (first 10 packages) ===")
        for i, pkg in enumerate(sorted(meson_per_pkg)):
            if i >= 10:
                print(f"  ... ({len(meson_per_pkg) - 10} more packages)")
                break
            ms = meson_per_pkg[pkg][:3]
            m_str = ", ".join(m["target"] for m in ms)
            more = (f" (+{len(meson_per_pkg[pkg]) - 3} more)"
                    if len(meson_per_pkg[pkg]) > 3 else "")
            print(f"  {pkg}: {m_str}{more}")

    print()
    print("FAIL — silent feature losses detected. Resolve by: "
          "(1) adding the missing dep to consumer's package.yml "
          "dependencies.build, (2) reordering build to ensure dep is built "
          "before consumer, or (3) accepting the loss explicitly with a "
          "rationale comment in package.yml if the dep is genuinely "
          "optional and not desired in InterGenOS.")


def write_artifacts(repo: Path, result: dict, ts: str) -> tuple[Path, Path]:
    build_dir = repo / "build"
    build_dir.mkdir(exist_ok=True)
    json_path = build_dir / f"preflight-silent-loss-{ts}.json"
    tsv_path = build_dir / f"preflight-silent-loss-{ts}.tsv"
    json_path.write_text(json.dumps({**result, "timestamp": ts}, indent=2))
    with tsv_path.open("w") as fp:
        fp.write("type\tpkg\tdep\tlog\tline_no\tkind\tmatch\n")
        for f in result["findings"]:
            fp.write("\t".join([
                f["type"], f["pkg"], f["dep"], f["log"],
                str(f["line_no"]), f["kind"],
                f["match"].replace("\t", " ").replace("\n", " "),
            ]) + "\n")
    return json_path, tsv_path


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Preflight silent-feature-loss gate.",
        epilog="Exit 0 on clean OR skipped (chroot absent); 1 on findings; 2 on env problem.",
    )
    ap.add_argument("--root", help="repo root (overrides INTERGENOS_ROOT + autodetect)")
    ap.add_argument("--chroot", help="chroot mount point (default /mnt/igos)")
    ap.add_argument("--report", action="store_true",
                    help="also write JSON + TSV artifacts to <repo>/build/")
    args = ap.parse_args()

    repo = discover_repo_root(args.root)
    if not (repo / "packages").is_dir():
        print(f"ERROR: repo root {repo} missing packages/ — "
              f"is this an InterGenOS checkout?", file=sys.stderr)
        return 2

    chroot = discover_chroot_root(args.chroot)
    result = scan(repo, chroot)
    emit_summary(result)

    if args.report:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        json_path, tsv_path = write_artifacts(repo, result, ts)
        print()
        print(f"Report artifacts:")
        print(f"  JSON: {json_path}")
        print(f"  TSV:  {tsv_path}")

    # Exit code:
    #   - skipped (chroot absent): exit 0 (intentional skip; not a block)
    #   - findings: exit 1
    #   - clean: exit 0
    if result["skipped"]:
        return 0
    if (result["findings"] or result["summary_disabled"]
            or result["meson_not_found_intree"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
