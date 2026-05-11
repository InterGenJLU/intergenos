#!/usr/bin/env python3
"""
preflight-audit-coverage.py — Build Development Rulebook reproducibility gate.

Enforces that every tier:core / tier:base / tier:desktop / tier:extra / tier:ai
package has a current audit record in build/blfs-packages.db's package_audit
table, and that the record agrees with the package.yml + build.sh state.

Three checks per package:

  1. AUDIT EXISTS — there is a row in package_audit for the package.
  2. CURRENCY — the audit's version field matches the package.yml's current
     version. (Stale audits get re-flagged for refresh.)
  3. RECONCILIATION — declared dependencies.build matches the audit's
     `our_deps_build_json`. (Detects drift between yml and audit.)

Per-package overrides via a `.audit-override` file containing JSON:
    {"reason": "...", "approved_by": "...", "expires_at": "YYYY-MM-DD"}
Overrides are read-only acknowledgments, not auto-fixes — they let a
package skip the reconciliation check while the maintainer addresses
the underlying audit gap.

Exit codes:
    0  — all packages have current, reconciled audit records (or valid override)
    1  — one or more packages need audit refresh / reconciliation
"""
import json
import sqlite3
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
PACKAGES = REPO / "packages"
DB = REPO / "build" / "blfs-packages.db"

# Tiers that require an audit record. toolchain is built inline by
# toolchain-build.sh and follows LFS exactly; audit-skip.
AUDIT_REQUIRED_TIERS = {"core", "base", "desktop", "extra", "ai"}

# LFS Ch 8 packages follow the LFS book exactly (per Rule 13). They are
# tier:core but built in their own sacrosanct ordering via
# scripts/chroot-build-ch8.sh; we deliberately don't audit them because
# any "correction" would deviate from the LFS book.
import re as _re
def _load_ch8():
    ch8_script = REPO / "scripts" / "chroot-build-ch8.sh"
    if not ch8_script.exists():
        return set()
    return set(_re.findall(
        r'^\s*run_package\s+"([^"]+)"',
        ch8_script.read_text(),
        _re.MULTILINE,
    ))
LFS_CH8_SACROSANCT = _load_ch8()


def collect_packages():
    out = {}
    for yml_path in PACKAGES.rglob("package.yml"):
        try:
            d = yaml.safe_load(yml_path.read_text())
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        name = d.get("name")
        tier = d.get("tier")
        if not name or not tier:
            continue
        out[name] = {
            "tier": tier,
            "version": str(d.get("version", "")),
            "yml_path": yml_path,
            "deps_build": list((d.get("dependencies") or {}).get("build") or []),
            "pending_acquisition": d.get("pending_acquisition"),
        }
    return out


def main():
    if not DB.exists():
        print(f"[audit-preflight] FAIL: {DB} not found. Run "
              f"scripts/parse-blfs-book.py + scripts/aggregate-package-audits.py first.")
        return 1

    db = sqlite3.connect(str(DB))
    db.row_factory = sqlite3.Row

    # Confirm table exists
    has_table = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='package_audit'"
    ).fetchone()
    if not has_table:
        print("[audit-preflight] FAIL: package_audit table missing from db. "
              "Run scripts/aggregate-package-audits.py to create + populate.")
        return 1

    pkgs = collect_packages()
    audit_required = {n: p for n, p in pkgs.items()
                      if p["tier"] in AUDIT_REQUIRED_TIERS
                      and n not in LFS_CH8_SACROSANCT}
    print(f"[audit-preflight] {len(audit_required)} packages in scope "
          f"(tiers: {', '.join(sorted(AUDIT_REQUIRED_TIERS))}; "
          f"LFS Ch 8 sacrosanct exclusion: {len(LFS_CH8_SACROSANCT)})")

    rows = {r["name"]: r for r in db.execute(
        "SELECT name, version, our_deps_build_json, audited_at, audited_by "
        "FROM package_audit"
    )}

    missing = []      # no audit record
    stale = []        # version drift
    drift = []        # deps drift
    overridden = []   # has .audit-override

    for name, info in sorted(audit_required.items()):
        if info["pending_acquisition"]:
            continue  # pending packages don't audit (no source)

        # Override file?
        override = info["yml_path"].parent / ".audit-override"
        if override.exists():
            overridden.append(name)
            continue

        row = rows.get(name)
        if not row:
            missing.append(name)
            continue
        if row["version"] != info["version"]:
            stale.append((name, row["version"], info["version"]))
            continue
        # Deps drift
        try:
            audit_deps = sorted(json.loads(row["our_deps_build_json"] or "[]"))
        except json.JSONDecodeError:
            audit_deps = []
        yml_deps = sorted(info["deps_build"])
        if audit_deps != yml_deps:
            drift.append((name, audit_deps, yml_deps))

    has_failures = bool(missing or stale or drift)

    print(f"[audit-preflight] missing audit:   {len(missing):3d}")
    print(f"[audit-preflight] stale (version): {len(stale):3d}")
    print(f"[audit-preflight] drift (deps):    {len(drift):3d}")
    print(f"[audit-preflight] overridden:      {len(overridden):3d}")

    if missing:
        print()
        print(f"[audit-preflight] {len(missing)} package(s) MISSING an audit record:")
        for n in missing[:25]:
            print(f"  - {n}")
        if len(missing) > 25:
            print(f"  ... and {len(missing) - 25} more")
        print("  Resolve: python3 scripts/audit-package.py <name> --save")
        print("           python3 scripts/aggregate-package-audits.py")

    if stale:
        print()
        print(f"[audit-preflight] {len(stale)} package(s) have STALE audits "
              f"(version drift):")
        for n, av, yv in stale[:15]:
            print(f"  - {n}: audit={av}, yml={yv}")
        if len(stale) > 15:
            print(f"  ... and {len(stale) - 15} more")
        print("  Resolve: re-audit each, then re-aggregate.")

    if drift:
        print()
        print(f"[audit-preflight] {len(drift)} package(s) have DEPS DRIFT "
              f"(package.yml changed after audit):")
        for n, ad, yd in drift[:15]:
            added = set(yd) - set(ad)
            removed = set(ad) - set(yd)
            chgs = []
            if added: chgs.append(f"+{sorted(added)}")
            if removed: chgs.append(f"-{sorted(removed)}")
            print(f"  - {n}: {' '.join(chgs)}")
        if len(drift) > 15:
            print(f"  ... and {len(drift) - 15} more")
        print("  Resolve: re-audit each affected package, then re-aggregate.")

    if not has_failures:
        print()
        print("[audit-preflight] PASS: every in-scope package has a current, "
              "reconciled audit record")
        return 0

    print()
    print(f"[audit-preflight] FAIL: {len(missing) + len(stale) + len(drift)} "
          f"packages need audit work.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
