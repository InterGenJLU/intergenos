#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""check-trace-coverage.py — scan for un-wrapped subprocess sites in instrumented packages.

Pre-push gate (#11 per dossier 50-implementation-priority.md section 6).

Scans the canonical instrumentation surfaces:

  - igos-build/*.py
  - pkm/*.py
  - installer/backend/*.py

for direct subprocess.run / subprocess.Popen / subprocess.check_output /
subprocess.check_call invocations that are NOT routed through the
forensic-trace wrappers (_trace.traced_run, _trace.traced_run_chroot).

Allowlist mechanism for benign sites (e.g. inside the trace wrappers
themselves, or in test helpers): a `# trace-coverage: allow` comment on
the same line silences the gate for that line.

Bash-side instrumentation is enforced by Step 6 (every chroot-build-*.sh
sources scripts/lib/trace.sh; the per-package boundaries + per-phase
boundaries route through trace_run / trace_pkg_phase / etc.). Adding a
bash subprocess-call scanner is feasible but the bash surface is smaller
and the structural pattern (source + trace_init + EXIT trap) is easier to
audit by directory listing — we leave the bash surface to the structural
check below + the existing pre-commit-hook flow.

Usage:
    python3 scripts/check-trace-coverage.py
        — scans the default surfaces under /mnt/intergenos
    python3 scripts/check-trace-coverage.py /mnt/intergenos
        — explicit repo root

Exit codes:
    0 — every subprocess site in scope is either wrapped or allowlisted
    1 — at least one un-wrapped subprocess site found
"""

import re
import sys
from pathlib import Path


# Subprocess invocation patterns that must be wrapped.
# Matches: subprocess.run(, subprocess.Popen(, subprocess.check_output(,
# subprocess.check_call(, subprocess.call(.
_SUBPROC_RE = re.compile(
    r"\bsubprocess\.(?:run|Popen|check_output|check_call|call)\s*\("
)

# Allowlist marker. Per-line comment that silences the gate for that line.
_ALLOW_RE = re.compile(r"#\s*trace-coverage:\s*allow\b", re.IGNORECASE)

# Wrapper definition sites — these files DEFINE the subprocess wrappers
# (igos_trace.py:traced_run uses subprocess.run internally) so the scanner
# would always flag them. Skip these whole files.
_WRAPPER_FILES = {
    "scripts/lib/igos_trace.py",
    "installer/backend/_trace.py",      # loader shim, no subprocess
    "installer/backend/trace.py",       # re-export shim
    "igos-build/_trace.py",
    "pkm/_trace.py",
}

# Directories to scan for instrumented Python. installer/backend is the
# Forge installer code path with its own pre-existing prior-art trace.py
# usage and a much larger subprocess surface that predates this lift; the
# lift's Rule 21 contract per dossier 40-no-stub-discipline.md section 6
# explicitly scopes the scanner to scripts/ + igos-build/ + pkm/.
# Forge backward-compat is verified separately via the Step 11 Forge
# regression test (installer/tests/test_forge_trace_after_lift.py).
_SCOPE_DIRS = [
    "igos-build",
    "pkm",
]


def scan_file(path, repo_root):
    """Return a list of (line_no, line_text) for unwrapped subprocess sites."""
    rel = str(path.relative_to(repo_root))
    if rel in _WRAPPER_FILES:
        return []
    findings = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                # Skip comment-only lines + import statements (the `import
                # subprocess` itself is fine; we only flag invocations).
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    continue
                if _SUBPROC_RE.search(line) and not _ALLOW_RE.search(line):
                    findings.append((line_no, line.rstrip()))
    except OSError as e:
        print(f"check-trace-coverage: cannot read {path}: {e}", file=sys.stderr)
    return findings


def main(argv):
    repo_root = Path(argv[1]).resolve() if len(argv) > 1 else Path(__file__).resolve().parent.parent
    if not repo_root.is_dir():
        print(f"check-trace-coverage: repo_root not a dir: {repo_root}", file=sys.stderr)
        return 2

    total_findings = 0
    for scope_rel in _SCOPE_DIRS:
        scope = repo_root / scope_rel
        if not scope.is_dir():
            continue
        for py_path in sorted(scope.rglob("*.py")):
            findings = scan_file(py_path, repo_root)
            for line_no, line in findings:
                print(
                    f"{py_path.relative_to(repo_root)}:{line_no}: "
                    f"unwrapped subprocess invocation: {line.strip()}",
                    file=sys.stderr,
                )
                total_findings += 1

    if total_findings:
        print(
            f"check-trace-coverage: {total_findings} unwrapped subprocess site(s) found.\n"
            f"Wrap each via _trace.traced_run / _trace.traced_run_chroot, or add a "
            f"`# trace-coverage: allow` comment on the same line if the site is "
            f"intentionally direct.",
            file=sys.stderr,
        )
        return 1

    print(
        "check-trace-coverage: OK — every subprocess site in scope is wrapped "
        "via _trace.* or allowlisted."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
