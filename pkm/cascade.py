"""pkm cascade — Q5+SC5 reverse-dependency upgrade-impact warning.

Per the operator-greenlit O-033 Phase 1: at upgrade time, surface which
installed packages reverse-depend on the target so the user knows what
will be affected. Phase 2 (per-library SONAME tracking + refuse-upgrade
on SONAME bump without --allow-soname-break) is tracked as F-003 in
docs/v1.1-deferred-followups.md — it requires a SONAME column in the
files table plus build-time SONAME extraction, which is its own design
surface.

Phase 1 is informational: pkm reads the existing depends table (the
add_depends infrastructure landed at H-004 / dbdb9533) and renders a
human-readable warning that the upgrade orchestration prints before
proceeding. The user retains control — they see "upgrading glibc 2.39
→ 2.40 will affect 600 reverse-dependents" and can decide whether to
accept that scope.

This module provides format_reverse_dep_warning; the call site sits
in the upgrade orchestration (windows-host coordinator's lane) which
invokes it per-target before deploy.
"""

from collections import Counter


# Number of reverse-dependents above which we collapse the per-name list
# into a counted summary to avoid burying the user in 600+ package names.
MAX_LISTED_REVERSE_DEPS = 20


def format_reverse_dep_warning(db, package_name):
    """Render a multi-line warning about reverse-dependents of an upgrade target.

    Args:
        db: PackageDB instance.
        package_name: name of the package about to be upgraded.

    Returns:
        str — multi-line warning string, or empty string when there are
        zero reverse-dependents (nothing to warn about).
    """
    rdeps = db.get_reverse_depends(package_name)
    if not rdeps:
        return ""
    total = len(rdeps)
    if total <= MAX_LISTED_REVERSE_DEPS:
        names = sorted({r["name"] for r in rdeps})
        listing = ", ".join(names)
        return (
            f"  Note: upgrading {package_name} will affect {total} "
            f"reverse-dependent(s): {listing}"
        )
    # Collapse to a counted summary when the list would be unreadably long.
    # Group by dep_type to give the user a sense of which dependency class
    # is bearing the impact.
    types = Counter(r["type"] for r in rdeps)
    type_summary = ", ".join(f"{count} {tname}" for tname, count in types.most_common())
    return (
        f"  Note: upgrading {package_name} will affect {total} "
        f"reverse-dependent(s) ({type_summary}). Run "
        f"`pkm depends --reverse {package_name}` to list them."
    )
