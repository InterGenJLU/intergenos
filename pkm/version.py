"""Version comparison for pkm.

O-010 audit row closure: prior implementation used `remote["version"] !=
pkg["version"]` string equality in cmd_upgrade and cmd_list to decide
"upgradable", which mis-orders semver (`1.10` vs `1.9`), flags
downgrades as upgrades, and silently ignores `release` (the integer
package-build counter that bumps independent of upstream version).

This module routes both comparisons through packaging.version.Version
(PEP 440) and falls open with a clear error when a version string is
non-compliant rather than silently lex-comparing.
"""

from packaging.version import Version, InvalidVersion


class VersionParseError(ValueError):
    """Raised when a pkm version string isn't PEP 440-compliant.

    pkm packages are built by InterGenOS tooling under our control;
    every version string emitted by the builder pipeline is expected
    to parse cleanly via packaging.version.Version. A non-compliant
    version surfaces a build-system or repo-corruption defect, not
    user input — surface the failure rather than fall back to lexical
    compare that would silently mis-order semver.
    """


def _parse(v):
    try:
        return Version(v)
    except InvalidVersion as exc:
        raise VersionParseError(
            f"version {v!r} is not PEP 440-compliant; cannot compare "
            f"safely. Source: build-system or repo-index corruption."
        ) from exc


def _normalize(pkg):
    """Accept dict with 'version'/'release' keys or a (ver, rel) tuple.

    Returns (version_str, release_int). release defaults to 1 when
    absent (matches database.py schema default).
    """
    if isinstance(pkg, dict):
        ver = pkg.get("version", "")
        rel = pkg.get("release", 1)
    else:
        ver = pkg[0]
        rel = pkg[1] if len(pkg) > 1 else 1
    try:
        rel_int = int(rel) if rel is not None else 1
    except (TypeError, ValueError):
        rel_int = 1
    return ver, rel_int


def compare(a, b):
    """Compare two (version, release)-bearing pkg entries.

    Returns -1 if a is older than b, 0 if equal, 1 if a is newer.
    Each operand can be a dict with 'version'/'release' keys or a
    (ver, rel) tuple. Raises VersionParseError on non-PEP 440 input.
    """
    av_str, ar = _normalize(a)
    bv_str, br = _normalize(b)
    av = _parse(av_str)
    bv = _parse(bv_str)
    if av < bv:
        return -1
    if av > bv:
        return 1
    if ar < br:
        return -1
    if ar > br:
        return 1
    return 0


def is_upgradable(installed, remote, allow_downgrade=False):
    """Return True when remote should replace installed.

    Default: remote strictly newer than installed (compare > 0).
    allow_downgrade=True: remote differs from installed (either
    direction). Used by `pkm upgrade --allow-downgrade` so operators
    can roll back to an older repo entry after a bad release.
    """
    cmp_val = compare(installed, remote)
    if cmp_val < 0:
        return True
    if allow_downgrade and cmp_val != 0:
        return True
    return False
