#!/usr/bin/env python3
"""aggregate-package-audits.py — Ingest per-package audit JSONs into the
build database + produce a reconciliation report.

Reads:
    build/audits/*.json (per-package audit records, format defined by
    scripts/audit-package.py)

Writes:
    1. build/blfs-packages.db: creates / updates the `package_audit`
       table with one row per audited package.
    2. build/audit-reconciliation-<ts>.tsv: a flat report of every
       declared-vs-audit-truth discrepancy, suitable for maintainer
       review before applying corrections.

Schema:
    See PACKAGE_AUDIT_SCHEMA below. Stable across re-runs (idempotent
    upserts).

Usage:
    python3 scripts/aggregate-package-audits.py [--audits-dir DIR]
                                                [--db PATH]
                                                [--report PATH]
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timezone

REPO = Path("/mnt/intergenos")
AUDITS_DIR = REPO / "build" / "audits"
DB_PATH = REPO / "build" / "blfs-packages.db"


PACKAGE_AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS package_audit (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    version TEXT NOT NULL,
    tier TEXT NOT NULL,
    package_dir TEXT,

    source_url TEXT,
    source_sha256 TEXT,
    source_tarball TEXT,
    source_tarball_exists INTEGER NOT NULL DEFAULT 0,

    build_system TEXT,
    bundled_libs_json TEXT,
    docs_seen_json TEXT,
    tarball_files_count INTEGER,

    -- Our currently-declared state (denormalized from package.yml /
    -- build.sh at audit time)
    our_deps_build_json TEXT,
    our_deps_host_json TEXT,
    our_deps_runtime_json TEXT,
    our_autotools_flags_json TEXT,
    our_meson_options_json TEXT,
    our_patches_json TEXT,

    -- Upstream truth as discovered (meson_options.txt, configure.ac)
    upstream_options_json TEXT,

    -- BLFS book reference (denormalized cross-reference)
    blfs_anchor TEXT,
    blfs_deps_json TEXT,
    blfs_patches_json TEXT,

    -- Reproducibility primitives
    source_date_epoch_supported INTEGER,
    parallel_build_supported INTEGER,
    deterministic_install INTEGER,
    reproducibility_notes TEXT,

    -- Expected install output (agent-filled)
    expected_binaries_json TEXT,
    expected_libs_json TEXT,
    expected_headers_json TEXT,
    expected_pkgconfig_json TEXT,

    -- Tests
    test_command TEXT,
    test_known_failures_json TEXT,

    -- Reconciliation
    needs_review_json TEXT,
    mismatches_json TEXT,

    -- Audit metadata
    audit_version INTEGER NOT NULL DEFAULT 1,
    audited_at TEXT NOT NULL,
    audited_by TEXT NOT NULL,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_package_audit_tier ON package_audit(tier);
CREATE INDEX IF NOT EXISTS idx_package_audit_build_system ON package_audit(build_system);
CREATE INDEX IF NOT EXISTS idx_package_audit_audited_at ON package_audit(audited_at);
"""


def upsert_record(db: sqlite3.Connection, rec: dict) -> None:
    """Idempotent upsert by name."""
    def j(v):
        return json.dumps(v) if v is not None else None

    blfs = rec.get("blfs") or {}
    repro = rec.get("reproducibility") or {}

    db.execute(
        """
        INSERT INTO package_audit (
            name, version, tier, package_dir,
            source_url, source_sha256, source_tarball, source_tarball_exists,
            build_system, bundled_libs_json, docs_seen_json, tarball_files_count,
            our_deps_build_json, our_deps_host_json, our_deps_runtime_json,
            our_autotools_flags_json, our_meson_options_json, our_patches_json,
            upstream_options_json,
            blfs_anchor, blfs_deps_json, blfs_patches_json,
            source_date_epoch_supported, parallel_build_supported,
            deterministic_install, reproducibility_notes,
            expected_binaries_json, expected_libs_json, expected_headers_json,
            expected_pkgconfig_json,
            test_command, test_known_failures_json,
            needs_review_json, mismatches_json,
            audit_version, audited_at, audited_by, notes
        ) VALUES (
            :name, :version, :tier, :package_dir,
            :source_url, :source_sha256, :source_tarball, :source_tarball_exists,
            :build_system, :bundled_libs_json, :docs_seen_json, :tarball_files_count,
            :our_deps_build_json, :our_deps_host_json, :our_deps_runtime_json,
            :our_autotools_flags_json, :our_meson_options_json, :our_patches_json,
            :upstream_options_json,
            :blfs_anchor, :blfs_deps_json, :blfs_patches_json,
            :source_date_epoch_supported, :parallel_build_supported,
            :deterministic_install, :reproducibility_notes,
            :expected_binaries_json, :expected_libs_json, :expected_headers_json,
            :expected_pkgconfig_json,
            :test_command, :test_known_failures_json,
            :needs_review_json, :mismatches_json,
            :audit_version, :audited_at, :audited_by, :notes
        )
        ON CONFLICT(name) DO UPDATE SET
            version = excluded.version,
            tier = excluded.tier,
            package_dir = excluded.package_dir,
            source_url = excluded.source_url,
            source_sha256 = excluded.source_sha256,
            source_tarball = excluded.source_tarball,
            source_tarball_exists = excluded.source_tarball_exists,
            build_system = excluded.build_system,
            bundled_libs_json = excluded.bundled_libs_json,
            docs_seen_json = excluded.docs_seen_json,
            tarball_files_count = excluded.tarball_files_count,
            our_deps_build_json = excluded.our_deps_build_json,
            our_deps_host_json = excluded.our_deps_host_json,
            our_deps_runtime_json = excluded.our_deps_runtime_json,
            our_autotools_flags_json = excluded.our_autotools_flags_json,
            our_meson_options_json = excluded.our_meson_options_json,
            our_patches_json = excluded.our_patches_json,
            upstream_options_json = excluded.upstream_options_json,
            blfs_anchor = excluded.blfs_anchor,
            blfs_deps_json = excluded.blfs_deps_json,
            blfs_patches_json = excluded.blfs_patches_json,
            source_date_epoch_supported = excluded.source_date_epoch_supported,
            parallel_build_supported = excluded.parallel_build_supported,
            deterministic_install = excluded.deterministic_install,
            reproducibility_notes = excluded.reproducibility_notes,
            expected_binaries_json = excluded.expected_binaries_json,
            expected_libs_json = excluded.expected_libs_json,
            expected_headers_json = excluded.expected_headers_json,
            expected_pkgconfig_json = excluded.expected_pkgconfig_json,
            test_command = excluded.test_command,
            test_known_failures_json = excluded.test_known_failures_json,
            needs_review_json = excluded.needs_review_json,
            mismatches_json = excluded.mismatches_json,
            audit_version = excluded.audit_version,
            audited_at = excluded.audited_at,
            audited_by = excluded.audited_by,
            notes = excluded.notes
        """,
        {
            "name": rec["name"],
            "version": rec.get("version", ""),
            "tier": rec.get("tier", ""),
            "package_dir": rec.get("package_dir"),
            "source_url": rec.get("source_url"),
            "source_sha256": rec.get("source_sha256"),
            "source_tarball": rec.get("source_tarball"),
            "source_tarball_exists": 1 if rec.get("source_tarball_exists") else 0,
            "build_system": rec.get("build_system"),
            "bundled_libs_json": j(rec.get("bundled_libs")),
            "docs_seen_json": j(rec.get("docs_seen")),
            "tarball_files_count": rec.get("tarball_files_count"),
            "our_deps_build_json": j(rec.get("our_deps_build")),
            "our_deps_host_json": j(rec.get("our_deps_host")),
            "our_deps_runtime_json": j(rec.get("our_deps_runtime")),
            "our_autotools_flags_json": j(rec.get("our_autotools_flags")),
            "our_meson_options_json": j(rec.get("our_meson_options")),
            "our_patches_json": j(rec.get("our_patches")),
            "upstream_options_json": j(rec.get("upstream_options")),
            "blfs_anchor": blfs.get("anchor_id"),
            "blfs_deps_json": j(blfs.get("deps")),
            "blfs_patches_json": j(blfs.get("patches")),
            "source_date_epoch_supported": repro.get("source_date_epoch_supported"),
            "parallel_build_supported": repro.get("parallel_build_supported"),
            "deterministic_install": repro.get("deterministic_install"),
            "reproducibility_notes": repro.get("_notes"),
            "expected_binaries_json": j(rec.get("expected_binaries")),
            "expected_libs_json": j(rec.get("expected_libs")),
            "expected_headers_json": j(rec.get("expected_headers")),
            "expected_pkgconfig_json": j(rec.get("expected_pkgconfig")),
            "test_command": rec.get("test_command"),
            "test_known_failures_json": j(rec.get("test_known_failures")),
            "needs_review_json": j(rec.get("_needs_review")),
            "mismatches_json": j(rec.get("_mismatches")),
            "audit_version": rec.get("audit_version", 1),
            "audited_at": rec.get("audited_at", ""),
            "audited_by": rec.get("audited_by", ""),
            "notes": rec.get("notes"),
        },
    )


def reconciliation_rows(rec: dict) -> list[tuple]:
    """Produce TSV rows: (name, category, issue_summary, detail)."""
    rows = []
    name = rec["name"]
    for issue in rec.get("_needs_review") or []:
        rows.append((name, "needs-review", issue, ""))
    for m in rec.get("_mismatches") or []:
        rows.append((name, "mismatch", m.get("field", ""), m.get("issue", "")))
    if rec.get("bundled_libs"):
        rows.append((name, "rule-5-trigger", "bundled-libs",
                     ", ".join(rec["bundled_libs"])))
    if not rec.get("source_tarball_exists"):
        rows.append((name, "blocker", "source-tarball-missing",
                     rec.get("_notes_source", "")))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audits-dir", default=str(AUDITS_DIR))
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--report", default=None,
                    help="Path for reconciliation TSV "
                         "(default: build/audit-reconciliation-<ts>.tsv)")
    args = ap.parse_args()

    audits_dir = Path(args.audits_dir)
    audit_files = sorted(audits_dir.glob("*.json"))
    if not audit_files:
        print(f"No audit JSONs found in {audits_dir}", file=sys.stderr)
        return 1

    db = sqlite3.connect(args.db)
    db.executescript(PACKAGE_AUDIT_SCHEMA)

    ingested = 0
    all_recon_rows = []
    for f in audit_files:
        try:
            rec = json.loads(f.read_text())
        except Exception as e:
            print(f"  SKIP {f.name}: {e}", file=sys.stderr)
            continue
        upsert_record(db, rec)
        ingested += 1
        all_recon_rows.extend(reconciliation_rows(rec))
    db.commit()

    print(f"Ingested {ingested}/{len(audit_files)} audit records into {args.db}")

    # Reconciliation report
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = Path(args.report) if args.report else (
        REPO / "build" / f"audit-reconciliation-{ts}.tsv"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w") as f:
        f.write("package\tcategory\tissue\tdetail\n")
        for r in sorted(all_recon_rows):
            f.write("\t".join(str(x) for x in r) + "\n")
    print(f"Reconciliation report: {report_path} "
          f"({len(all_recon_rows)} flagged rows)")

    # Summary
    counts = {}
    for r in all_recon_rows:
        counts[r[1]] = counts.get(r[1], 0) + 1
    if counts:
        print("Breakdown by category:")
        for cat, n in sorted(counts.items()):
            print(f"  {cat:20s} {n:4d}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
