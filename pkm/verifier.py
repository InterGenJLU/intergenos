# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""pkm verifier — Package integrity checking.

Two modes per RFC §5a:
  - strict (default): existence + SHA-256 content hash check. Catches both
    missing files and tampered/stale content. Roughly 10-15s for a
    full-system verify.
  - fast: existence (lexists) only. Sub-second per package; matches the
    pre-RFC behavior. Reserved for cases where speed matters and content
    integrity is checked elsewhere.

Superseded packages are surfaced explicitly per RFC §5b: queries against a
retired package return a {superseded_by, superseded_at, message} payload
and a distinct exit code so scripting can route to the active owner.
"""

from .database import PackageDB

# Forensic-trace shim — defensive import.
try:
    from . import _trace
    _TRACE_AVAILABLE = True
except ImportError:
    _trace = None
    _TRACE_AVAILABLE = False


# Verifier exit codes per RFC §5b
EXIT_OK = 0            # every owned file was checked and passed
EXIT_MODIFIED = 1      # at least one file is missing or modified — a failure
EXIT_SUPERSEDED = 2    # package was superseded; verify the successor instead
EXIT_UNDETERMINED = 3  # nothing failed, but at least one check could not run

# Three outcomes, three codes. A verification answers one of: it passed, it
# failed, or it could not be carried out — and the caller must be able to tell
# the third from the first two. EXIT_UNDETERMINED covers both ways a check can
# fail to run: the file's bytes or even its existence could not be read
# (undeterminable), and no reference hash was ever recorded to compare against
# (unverifiable). A real failure outranks an unknown, so a result carrying both
# a missing file and an unreadable one reports EXIT_MODIFIED.


class PackageVerifier:
    """Verify package integrity against the database."""

    def __init__(self, db: PackageDB):
        self.db = db

    def verify(self, name, mode="strict"):
        """Verify a single package.

        Args:
            name: Package name.
            mode: "strict" (default; SHA-256 content check) or "fast"
                  (lexists only).

        Returns:
            dict with keys:
              - total, missing, modified — file accounting
              - undeterminable — owned paths whose state could not be
                established because this process may not read them
              - superseded_by — name of successor if retired, else None
              - superseded_at — ISO8601 timestamp when superseded, else None
              - exit_code — EXIT_OK / EXIT_MODIFIED / EXIT_SUPERSEDED /
                EXIT_UNDETERMINED
              - message — human-readable summary string
            Returns None if package is not installed.
        """
        pkg = self.db.get_installed(name)
        if pkg is None:
            return None

        if pkg.get("superseded_by"):
            return {
                "total": 0,
                "missing": [],
                "modified": [],
                "unverifiable": [],
                "undeterminable": [],
                "expected_absent": [],
                "expected_absent_by_class": {},
                "generated": [],
                "superseded_by": pkg["superseded_by"],
                "superseded_at": pkg.get("superseded_at"),
                "exit_code": EXIT_SUPERSEDED,
                "message": (
                    f"{name} {pkg['version']} was superseded by "
                    f"{pkg['superseded_by']} on {pkg.get('superseded_at')}. "
                    f"Run 'pkm verify {pkg['superseded_by']}' to verify the "
                    f"active state."
                ),
            }

        result = self.db.verify_package(name, strict=(mode == "strict"))
        if result is None:
            return None
        result["superseded_by"] = result.get("superseded_by")  # already set by DB layer
        result["superseded_at"] = pkg.get("superseded_at")
        if result["missing"] or result["modified"]:
            result["exit_code"] = EXIT_MODIFIED
        elif result.get("undeterminable") or result.get("unverifiable"):
            result["exit_code"] = EXIT_UNDETERMINED
        else:
            result["exit_code"] = EXIT_OK
        if _TRACE_AVAILABLE:
            try:
                _trace.trace_event(
                    "pkm_verify_result",
                    pkg=name, mode=mode,
                    total=result.get("total", 0),
                    missing=len(result.get("missing", [])),
                    modified=len(result.get("modified", [])),
                    unverifiable=len(result.get("unverifiable", [])),
                    undeterminable=len(result.get("undeterminable", [])),
                    expected_absent=len(result.get("expected_absent", [])),
                    generated=len(result.get("generated", [])),
                    exit_code=result["exit_code"],
                )
            except Exception:
                pass
        return result

    def verify_all(self, mode="strict", on_package=None):
        """Verify every installed package, skipping superseded records.

        Returns a list of (name, version, result_dict). Superseded packages
        are filtered out — verify their successors via single-package
        verify if you need to audit retired packages.

        ``on_package`` is an optional callback invoked as
        ``on_package(index, total, name)`` BEFORE each package is checked.
        A whole-system strict verify reads and hashes every owned file on
        the machine and was measured taking over forty seconds while
        printing nothing at all, which is the silence the per-part progress
        standard exists to close (pkm.progress). The callback is the seam:
        this method stays a library call with no console of its own, and
        the CLI decides what a person sees. A callback that raises is not
        allowed to abort the verification — reporting must never be able to
        stop the work it describes.
        """
        installed = self.db.list_installed()
        total = len(installed)
        results = []
        for index, pkg in enumerate(installed, start=1):
            if on_package is not None:
                try:
                    on_package(index, total, pkg["name"])
                except Exception:
                    pass
            full = self.db.get_installed(pkg["name"])
            if full and full.get("superseded_by"):
                continue
            result = self.verify(pkg["name"], mode=mode)
            if result:
                results.append((pkg["name"], pkg["version"], result))
        return results
