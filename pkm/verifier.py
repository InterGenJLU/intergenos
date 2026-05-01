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


# Verifier exit codes per RFC §5b
EXIT_OK = 0          # all files present (and content-hashed when strict)
EXIT_MODIFIED = 1    # at least one missing or modified file
EXIT_SUPERSEDED = 2  # package was superseded; verify the successor instead


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
              - superseded_by — name of successor if retired, else None
              - superseded_at — ISO8601 timestamp when superseded, else None
              - exit_code — one of EXIT_OK / EXIT_MODIFIED / EXIT_SUPERSEDED
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
        result["exit_code"] = (
            EXIT_MODIFIED if (result["missing"] or result["modified"]) else EXIT_OK
        )
        return result

    def verify_all(self, mode="strict"):
        """Verify every installed package, skipping superseded records.

        Returns a list of (name, version, result_dict). Superseded packages
        are filtered out — verify their successors via single-package
        verify if you need to audit retired packages.
        """
        results = []
        for pkg in self.db.list_installed():
            full = self.db.get_installed(pkg["name"])
            if full and full.get("superseded_by"):
                continue
            result = self.verify(pkg["name"], mode=mode)
            if result:
                results.append((pkg["name"], pkg["version"], result))
        return results
