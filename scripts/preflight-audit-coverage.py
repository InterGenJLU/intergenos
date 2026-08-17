#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
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
AUDIT_REQUIRED_TIERS = {"core", "base", "desktop", "extra", "ai", "compute"}

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


def collect_packages(malformed: list | None = None):
    """Inventory packages/*/*/package.yml. A manifest that cannot be parsed
    (or lacks name:/tier:) lands in `malformed` — main() FAILS on any entry;
    the old bare `continue` silently shrank the audited inventory."""
    if malformed is None:
        malformed = []
    out = {}
    for yml_path in PACKAGES.rglob("package.yml"):
        try:
            d = yaml.safe_load(yml_path.read_text())
        except Exception as e:
            malformed.append((str(yml_path), f"parse error: {e}"))
            continue
        if not isinstance(d, dict):
            malformed.append((str(yml_path),
                              f"top level is {type(d).__name__}, expected a mapping"))
            continue
        name = d.get("name")
        tier = d.get("tier")
        if not name or not tier:
            malformed.append((str(yml_path), "missing required name:/tier: fields"))
            continue
        src = d.get("source") or []
        primary = src[0] if src and isinstance(src[0], dict) else {}
        deps = d.get("dependencies") or {}
        out[name] = {
            "tier": tier,
            "version": str(d.get("version", "")),
            "yml_path": yml_path,
            "deps_build": list(deps.get("build") or []),
            "deps_host": list(deps.get("host") or []),
            "deps_runtime": list(deps.get("runtime") or []),
            "source_sha256": primary.get("sha256"),
            "patches": d.get("patches") or [],
            "build_sh": yml_path.parent / "build.sh",
            "pending_acquisition": d.get("pending_acquisition"),
        }
    return out


def _load_audit_package_mod():
    """Load scripts/audit-package.py by file path (hyphenated name) so the
    gate re-derives build.sh flags with the SAME parser the audit writer
    used — shared code, zero derivation drift."""
    import importlib.util
    path = Path(__file__).resolve().parent / "audit-package.py"
    spec = importlib.util.spec_from_file_location("igos_audit_package", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _norm_list(value) -> list:
    """Order-insensitive, structure-stable normalization for comparisons."""
    return sorted(json.dumps(e, sort_keys=True) for e in (value or []))


def audit_currency_mismatches(row, info, audit_mod) -> list[str]:
    """Compare EVERY audit-recorded input against the package's current
    state; return the mismatched field names. The old check compared only
    version + deps.build, so a same-version change to the source pin, tier,
    host/runtime deps, patches, or configure flags kept a stale PASS."""
    fields = []
    if row["tier"] != info["tier"]:
        fields.append(f"tier (audit={row['tier']}, yml={info['tier']})")
    if (row["source_sha256"] or None) != (info["source_sha256"] or None):
        fields.append("source_sha256")
    for col, key in (("our_deps_host_json", "deps_host"),
                     ("our_deps_runtime_json", "deps_runtime")):
        try:
            audit_val = json.loads(row[col] or "[]")
        except json.JSONDecodeError:
            audit_val = []
        if _norm_list(audit_val) != _norm_list(info[key]):
            fields.append(key)
    try:
        audit_patches = json.loads(row["our_patches_json"] or "[]")
    except json.JSONDecodeError:
        audit_patches = []
    if _norm_list(audit_patches) != _norm_list(info["patches"]):
        fields.append("patches")
    # Flags: re-derive from the CURRENT build.sh with the audit writer's
    # own parser and compare against what the audit recorded.
    body = audit_mod.parse_build_sh_configure(info["build_sh"])
    flags = audit_mod.parse_flags_from_configure_body(body)
    try:
        audit_auto = json.loads(row["our_autotools_flags_json"] or "[]")
    except json.JSONDecodeError:
        audit_auto = []
    try:
        audit_meson = json.loads(row["our_meson_options_json"] or "[]")
    except json.JSONDecodeError:
        audit_meson = []
    if _norm_list(audit_auto) != _norm_list(flags["autotools_flags"]):
        fields.append("configure flags (autotools)")
    if _norm_list(audit_meson) != _norm_list(flags["meson_options"]):
        fields.append("configure flags (meson)")
    return fields


def _validate_override(path: Path) -> str | None:
    """Return a problem string if the .audit-override is unusable, else None.

    Contract (documented in the module header since the field's inception):
    JSON with non-empty string `reason` and `approved_by`, and `expires_at`
    as YYYY-MM-DD strictly in the future of 'today'. An override that fails
    any of these is a FAILURE of the gate, never a working waiver.
    """
    from datetime import date
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        return f"not valid JSON ({e})"
    if not isinstance(data, dict):
        return f"top level is {type(data).__name__}, expected an object"
    for field in ("reason", "approved_by", "expires_at"):
        v = data.get(field)
        if not isinstance(v, str) or not v.strip():
            return f"missing/empty required field '{field}'"
    try:
        expires = date.fromisoformat(data["expires_at"])
    except ValueError:
        return f"expires_at {data['expires_at']!r} is not a YYYY-MM-DD date"
    if expires < date.today():
        return f"EXPIRED on {data['expires_at']} — re-approve or fix the audit gap"
    return None


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

    malformed: list = []
    pkgs = collect_packages(malformed)
    audit_required = {n: p for n, p in pkgs.items()
                      if p["tier"] in AUDIT_REQUIRED_TIERS
                      and n not in LFS_CH8_SACROSANCT}
    print(f"[audit-preflight] {len(audit_required)} packages in scope "
          f"(tiers: {', '.join(sorted(AUDIT_REQUIRED_TIERS))}; "
          f"LFS Ch 8 sacrosanct exclusion: {len(LFS_CH8_SACROSANCT)})")

    # Completeness contract: a manifest the inventory pass dropped is a
    # package this gate silently never audited; an empty scope certifies
    # nothing. Both FAIL — a PASS here must mean the WHOLE tree was checked.
    if malformed:
        print(f"[audit-preflight] FAIL: {len(malformed)} package manifest(s) "
              f"could not be inventoried — audit coverage cannot be certified:")
        for path, reason in malformed:
            print(f"  - {path}: {reason}")
        return 1
    if not audit_required:
        print(f"[audit-preflight] FAIL: zero packages in audit scope under "
              f"{PACKAGES} — an empty scan certifies nothing (wrong repo root?)")
        return 1

    rows = {r["name"]: r for r in db.execute(
        "SELECT name, version, tier, source_sha256, our_deps_build_json, "
        "our_deps_host_json, our_deps_runtime_json, our_patches_json, "
        "our_autotools_flags_json, our_meson_options_json, "
        "audited_at, audited_by "
        "FROM package_audit"
    )}
    audit_mod = _load_audit_package_mod()

    missing = []        # no audit record
    stale = []          # version drift
    drift = []          # deps drift
    stale_inputs = []   # (name, [fields]) — same version, other inputs drifted
    overridden = []     # (name, reason, expires_at) — VALID .audit-override
    bad_overrides = []  # (name, path, problem) — unusable override = FAILURE

    for name, info in sorted(audit_required.items()):
        if info["pending_acquisition"]:
            continue  # pending packages don't audit (no source)

        # Override file? Enforced, not existence-checked: the documented
        # contract ({"reason", "approved_by", "expires_at"}) is parsed and
        # every field required, and an expired override is DEAD — the old
        # existence-only check made any empty file a permanent, silent
        # audit exemption.
        override = info["yml_path"].parent / ".audit-override"
        if override.exists():
            problem = _validate_override(override)
            if problem:
                bad_overrides.append((name, str(override), problem))
            else:
                data = json.loads(override.read_text())
                overridden.append((name, data["reason"], data["expires_at"]))
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
            continue
        # Full-input currency: same version, but a recorded audit input
        # (tier / source pin / host+runtime deps / patches / flags) changed
        # since the audit ran.
        mismatched = audit_currency_mismatches(row, info, audit_mod)
        if mismatched:
            stale_inputs.append((name, mismatched))

    has_failures = bool(missing or stale or drift or stale_inputs
                        or bad_overrides)

    print(f"[audit-preflight] missing audit:   {len(missing):3d}")
    print(f"[audit-preflight] stale (version): {len(stale):3d}")
    print(f"[audit-preflight] drift (deps):    {len(drift):3d}")
    print(f"[audit-preflight] stale (inputs):  {len(stale_inputs):3d}")
    print(f"[audit-preflight] overridden:      {len(overridden):3d}")
    print(f"[audit-preflight] bad overrides:   {len(bad_overrides):3d}")

    if stale_inputs:
        print()
        print(f"[audit-preflight] {len(stale_inputs)} package(s) have STALE "
              f"AUDIT INPUTS (same version, but a recorded input changed "
              f"after the audit ran):")
        for n, fields in stale_inputs[:15]:
            print(f"  - {n}: {', '.join(fields)}")
        if len(stale_inputs) > 15:
            print(f"  ... and {len(stale_inputs) - 15} more")
        print("  Resolve: re-audit each, then re-aggregate.")

    if bad_overrides:
        print()
        print(f"[audit-preflight] {len(bad_overrides)} .audit-override file(s) "
              f"are UNUSABLE (an invalid or expired override is a gate "
              f"failure, never a silent waiver):")
        for n, p, problem in bad_overrides:
            print(f"  - {n}: {problem}")
            print(f"      ({p})")
        print("  Contract: JSON {\"reason\", \"approved_by\", "
              "\"expires_at\": \"YYYY-MM-DD\" (future)}")

    if overridden:
        print()
        print(f"[audit-preflight] active overrides (each expires):")
        for n, reason, expires in overridden:
            print(f"  - {n}: until {expires} — {reason[:100]}")

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
    print(f"[audit-preflight] FAIL: "
          f"{len(missing) + len(stale) + len(drift) + len(stale_inputs) + len(bad_overrides)} "
          f"packages need audit work.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
