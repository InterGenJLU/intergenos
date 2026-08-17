#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""pkm audit status — the programmatic query surface for the pkm findings registry.

Reads tests/pkm/audit/findings.yaml (the single source of truth) and reports
engineering status. A finding is only credible as resolved when its
regression_test exists and passes — this tool surfaces the state; the test
suite (tests/pkm/audit/test_findings_registry.py + the per-finding regression
tests) is what proves it.

Usage:
    pkm-audit-status.py                 # status table + tallies
    pkm-audit-status.py --gate          # exit 1 if any critical/high is still open (CI gate)
    pkm-audit-status.py --open          # list only not-yet-verified findings
    pkm-audit-status.py --id PKM-A01    # show one finding in full

Status lifecycle: open -> in_progress -> fixed -> verified.
"""
import argparse
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.stderr.write("pkm-audit-status: PyYAML is required (pip install pyyaml)\n")
    sys.exit(2)

REGISTRY = Path(__file__).resolve().parent.parent / "tests" / "pkm" / "audit" / "findings.yaml"

_SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_STATUS_ORDER = {"open": 0, "in_progress": 1, "fixed": 2, "verified": 3}
_RESOLVED = {"verified"}  # only a passing-test 'verified' counts as resolved


def load():
    if not REGISTRY.exists():
        sys.stderr.write(f"pkm-audit-status: registry not found: {REGISTRY}\n")
        sys.exit(2)
    with REGISTRY.open() as fh:
        doc = yaml.safe_load(fh)
    return doc.get("findings", []) or []


def _sort_key(f):
    return (_SEV_ORDER.get(f.get("severity"), 9), _STATUS_ORDER.get(f.get("status"), 9), f.get("id", ""))


def cmd_table(findings):
    findings = sorted(findings, key=_sort_key)
    print(f"  pkm audit — {len(findings)} findings  (registry: {REGISTRY.name})")
    print(f"  {'ID':<9} {'SEV':<9} {'STATUS':<12} {'TIER':<11} TITLE")
    print(f"  {'-'*9} {'-'*9} {'-'*12} {'-'*11} {'-'*40}")
    for f in findings:
        title = f.get("title", "")
        if len(title) > 64:
            title = title[:61] + "..."
        print(f"  {f.get('id',''):<9} {f.get('severity',''):<9} {f.get('status',''):<12} {f.get('tier',''):<11} {title}")
    # tallies
    by_status, by_sev = {}, {}
    for f in findings:
        by_status[f.get("status")] = by_status.get(f.get("status"), 0) + 1
        by_sev[f.get("severity")] = by_sev.get(f.get("severity"), 0) + 1
    resolved = sum(1 for f in findings if f.get("status") in _RESOLVED)
    print()
    print("  by status:  " + "  ".join(f"{k}={by_status[k]}" for k in sorted(by_status, key=lambda s: _STATUS_ORDER.get(s, 9))))
    print("  by severity:" + "  ".join(f" {k}={by_sev[k]}" for k in sorted(by_sev, key=lambda s: _SEV_ORDER.get(s, 9))))
    print(f"  resolved (verified): {resolved}/{len(findings)}")


def cmd_open(findings):
    rows = [f for f in sorted(findings, key=_sort_key) if f.get("status") not in _RESOLVED]
    for f in rows:
        print(f"  {f.get('id',''):<9} {f.get('severity',''):<9} {f.get('status',''):<12} {f.get('title','')}")
    print(f"\n  {len(rows)} not-yet-verified")


def cmd_show(findings, fid):
    for f in findings:
        if f.get("id") == fid:
            for k in ("id", "title", "module", "locations", "lens", "severity", "tier",
                      "status", "owner", "batch", "regression_test", "evidence", "fix"):
                print(f"  {k:16}: {f.get(k)}")
            return 0
    sys.stderr.write(f"pkm-audit-status: no finding {fid!r}\n")
    return 2


def cmd_gate(findings):
    """CI gate: fail if any critical/high finding is still open (not even in progress)."""
    stuck = [f for f in findings
             if f.get("severity") in ("critical", "high") and f.get("status") == "open"]
    if stuck:
        print("  GATE FAIL — critical/high findings still open:")
        for f in sorted(stuck, key=_sort_key):
            print(f"    {f.get('id'):<9} {f.get('severity'):<9} {f.get('title')}")
        return 1
    print("  GATE OK — no critical/high finding is still 'open'.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="pkm audit findings status")
    ap.add_argument("--gate", action="store_true", help="exit 1 if any critical/high is still open")
    ap.add_argument("--open", action="store_true", dest="only_open", help="list not-yet-verified findings")
    ap.add_argument("--id", dest="fid", help="show one finding by id")
    args = ap.parse_args(argv)
    findings = load()
    if args.fid:
        return cmd_show(findings, args.fid)
    if args.gate:
        return cmd_gate(findings)
    if args.only_open:
        cmd_open(findings)
        return 0
    cmd_table(findings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
