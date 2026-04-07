# Deferred Build System Features — 2026-04-02

## --skip flag (IMPLEMENTED)
- Implemented as `--skip-built` in Session 5. Checks manifest existence AND verifies
  all files present before skipping.

## --audit-logs (post-build log scanner)
- **What:** Add a `--audit-logs` flag (or standalone tool) that scans all per-package
  build logs for anomalies: `error:`, `FAIL`, `No such file`, `not found`, `segfault`,
  `warning:` (selective). Produces a summary report of packages with issues.
- **Why:** Test suite failures and build warnings are logged but not surfaced. Manually
  reading 80+ logs is impractical. A ninja build succeeded but its check() referenced
  a nonexistent test target (`ninja_test`) — this went unnoticed until manual review.
  With 458+ packages in the desktop tier, manual review is impossible.
- **Scope:** Scan `*.log` files in the build log directory, pattern-match known error
  signatures, classify as ERROR/WARNING/INFO, report per-package with line numbers.
- **When to implement:** Before the desktop tier build (312 packages).

## GitHub security tab items
- **What:** Review and address items flagged in the GitHub security tab at
  https://github.com/InterGenJLU/intergenos/security
- **Why:** Noted during Session 5 but deferred to avoid hitting session limits.
- **When to implement:** Next session.

## Known build.sh bugs found during Chapter 8
- **ninja:** `check()` references `ninja_test` target that doesn't exist in this version.
  Package builds correctly but test phase fails. Fix the check() function.
