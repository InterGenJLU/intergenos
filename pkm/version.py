# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Version comparison for pkm.

pkm manages a from-source distribution whose packages carry arbitrary upstream
version strings — ESR suffixes (``140.9.0esr``), patch letters (``10.2p1``,
``1.9.17p2``), build counters (``b8796``), date stamps (``2025-04-17``,
``2026_01_20.386b5f5``), and more. None of these are PEP 440, and they have no
reason to be: they are upstream's versions, not Python package versions.

Comparison therefore uses the Debian ``dpkg`` upstream-version algorithm
(Debian Policy 5.6.12) — a total order over ANY string — applied to the opaque
``version`` field, with pkm's separate integer ``release`` as the tie-break.
This is the standard, transparent, widely-understood distro-version semantics
(PRIME DIRECTIVE), and it never mis-orders or falsely flags a valid upstream
version as "corruption".

History (PKM-A01..A05): a prior implementation routed comparison through
``packaging.version.Version`` (PEP 440) and raised "build-system or repo-index
corruption" on every non-PEP-440 upstream version — 13 packages on a real
install tripped it, none corrupt, and the security-critical ``p``-suffixed
packages (openssh/openssl/sudo) were the most likely to trip it and be SILENTLY
skipped from ``pkm upgrade``. PEP 440 also folded an upstream ``-N`` suffix into
a postrelease (colliding with the separate ``release`` int) and treated ``1.0``
== ``1.0.0`` (masking a real bump). Treating ``version`` as opaque + ``release``
as orthogonal structurally eliminates all of those. ``VersionParseError`` is now
reserved for a genuinely empty/missing version — the only input that truly
cannot be ordered — and for a present-but-malformed ``release`` (which must
surface, not silently coerce to 1 and mis-decide the tie-break).
"""


class VersionParseError(ValueError):
    """Raised only when a version cannot be ordered: an empty/missing version
    string, or a present-but-non-integer ``release``. A non-empty upstream
    version string is ALWAYS orderable by the dpkg algorithm below and is never
    treated as corruption.
    """


def _order(c):
    """Ordering weight of a single character for the non-digit comparison
    (dpkg semantics): ``~`` sorts before everything (even the end of a part),
    then end-of-part, then letters (by ASCII), then all other non-digits.
    ``c`` is a one-character string, or ``""`` for end-of-string.
    """
    if c == "":
        return 0            # end of part
    if c.isdigit():
        return 0            # digits are handled by the numeric comparison
    if c.isalpha():
        return ord(c)       # letters sort by ASCII, all below the non-letters
    if c == "~":
        return -1           # sorts before everything, including end-of-part
    return ord(c) + 256     # other non-digits sort after the letters


def _verrevcmp(a, b):
    """Compare two version strings with dpkg's ``verrevcmp`` algorithm.

    Returns -1, 0, or 1. Faithful port: alternating non-digit and digit runs;
    non-digit runs compared via :func:`_order` (tilde-aware), digit runs
    compared numerically with leading zeros ignored. Operates on arbitrary
    strings and never raises.
    """
    ai, bi = 0, 0
    la, lb = len(a), len(b)
    while ai < la or bi < lb:
        # --- non-digit run (compare char-by-char with _order) ---
        while (ai < la and not a[ai].isdigit()) or (bi < lb and not b[bi].isdigit()):
            ac = _order(a[ai]) if ai < la else 0
            bc = _order(b[bi]) if bi < lb else 0
            if ac != bc:
                return -1 if ac < bc else 1
            ai += 1
            bi += 1
        # --- skip leading zeros so digit runs compare numerically ---
        while ai < la and a[ai] == "0":
            ai += 1
        while bi < lb and b[bi] == "0":
            bi += 1
        # --- digit run ---
        first_diff = 0
        while (ai < la and a[ai].isdigit()) and (bi < lb and b[bi].isdigit()):
            if first_diff == 0:
                first_diff = ord(a[ai]) - ord(b[bi])
            ai += 1
            bi += 1
        if ai < la and a[ai].isdigit():
            return 1        # a has a longer digit run -> a is newer
        if bi < lb and b[bi].isdigit():
            return -1       # b has a longer digit run -> b is newer
        if first_diff != 0:
            return -1 if first_diff < 0 else 1
    return 0


def _normalize(pkg):
    """Accept a dict with ``version``/``release`` keys or a ``(ver, rel)`` tuple.

    Returns ``(version_str, release_int)``. ``release`` defaults to 1 when
    ABSENT (matches the database.py schema default). A PRESENT-but-malformed
    ``release`` raises :class:`VersionParseError` (PKM-A05) rather than silently
    coercing to 1 — a corrupted release must not silently mis-decide the
    version-equal tie-break.
    """
    if isinstance(pkg, dict):
        ver = pkg.get("version", "")
        rel = pkg.get("release", None)
    else:
        ver = pkg[0]
        rel = pkg[1] if len(pkg) > 1 else None
    if rel is None:
        rel_int = 1
    else:
        try:
            rel_int = int(rel)
        except (TypeError, ValueError):
            raise VersionParseError(
                f"release {rel!r} for version {ver!r} is not an integer; "
                f"cannot order safely"
            )
    return ver, rel_int


def compare(a, b):
    """Compare two ``(version, release)``-bearing pkg entries.

    Returns -1 if ``a`` is older than ``b``, 0 if equal, 1 if ``a`` is newer.
    Each operand can be a dict with ``version``/``release`` keys or a
    ``(ver, rel)`` tuple. Raises :class:`VersionParseError` only when a version
    string is empty/missing or a release is malformed — never for a valid
    upstream version string.
    """
    av, ar = _normalize(a)
    bv, br = _normalize(b)
    if not av or not bv:
        raise VersionParseError(
            f"empty version string (a={av!r}, b={bv!r}); cannot compare"
        )
    c = _verrevcmp(str(av), str(bv))
    if c != 0:
        return c
    # versions are equal — the integer release is the tie-break
    if ar < br:
        return -1
    if ar > br:
        return 1
    return 0


def is_upgradable(installed, remote, allow_downgrade=False):
    """Return True when ``remote`` should replace ``installed``.

    Default: remote strictly newer than installed (compare > 0).
    ``allow_downgrade=True``: remote differs from installed (either direction),
    so ``pkm upgrade --allow-downgrade`` can roll back to an older repo entry
    after a bad release.
    """
    cmp_val = compare(installed, remote)
    if cmp_val < 0:
        return True
    if allow_downgrade and cmp_val != 0:
        return True
    return False
