"""Every ownership-allowlist row is well-formed, unique, and still needed.

config/squashfs-ownership-allowlist.txt is the only sanctioned exception
surface for the shipping-tree ownership gate: a path that matches neither a
package manifest nor a row here fails the squashfs build. Nothing tied a row
to the tree, so a row outlived the defect that justified it — seventeen rows
were removed on 2026-08-05 after their recipes had ALREADY been fixed to stage
those files into DESTDIR, in some cases weeks earlier. The exception surface
could only be swept by a human noticing, and between 2026-07-22 and then,
nobody did.

Three things are checked here, in rising order of what they would have caught:

  1. Shape — every active row carries the mandatory reason column. This was a
     hand check during the paydown, which is exactly the kind of check that
     should not be a hand check.
  2. Uniqueness — no pattern appears twice. A duplicate row is two independent
     justifications for one exception, and removing one leaves the exception
     silently in place.
  3. Currency — a row whose path the tree's recipes now stage into DESTDIR is
     reported. This is the one that would have caught all seventeen: the
     recipe fix landed, the manifest started recording the file, and the row
     became a standing exception for a problem that no longer existed.

The currency check reads the recipes and reports; it does not delete anything.
An exception that is merely SUSPECT is a thing for a person to look at, not
for a test to remove — but it must not be invisible, which is what it was.

Nothing here builds, needs privilege, or reads the network.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
ALLOWLIST = REPO / "config" / "squashfs-ownership-allowlist.txt"
PACKAGES = REPO / "packages"

# Rows whose path a recipe stages into DESTDIR but which must STAY, each with
# the reason the currency check cannot decide on its own. An entry here without
# a reason is a bug.
CURRENCY_EXPECTED = {
    # The dictionary is compiled ON THE TARGET at first use, so no build-time
    # artifact can ever be manifest-recorded; do_install stages the empty
    # directory only. Permanent state, not debt.
    "usr/lib/cracklib/**",
}


def _rows() -> list[tuple[int, str, str]]:
    """(line number, pattern, reason) for every active row."""
    out = []
    for lineno, raw in enumerate(ALLOWLIST.read_text().splitlines(), 1):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = re.split(r"\t+|\s{2,}", line.strip(), maxsplit=1)
        pattern = parts[0]
        reason = parts[1].strip() if len(parts) > 1 else ""
        out.append((lineno, pattern, reason))
    return out


def test_allowlist_exists_and_is_not_empty() -> None:
    assert ALLOWLIST.is_file(), f"{ALLOWLIST} is missing"
    assert _rows(), "the allowlist has no active rows — the gate would have no exceptions at all"


def test_every_row_states_why() -> None:
    """A row without a reason is an exception nobody can review."""
    missing = [(n, p) for n, p, r in _rows() if not r]
    assert not missing, (
        "allowlist rows with no reason column:\n"
        + "\n".join(f"  line {n}: {p}" for n, p in missing))


def test_reasons_are_more_than_a_restatement_of_the_path() -> None:
    """"usr/share/foo — usr/share/foo" is a reason column that says nothing."""
    empty = []
    for n, pattern, reason in _rows():
        stem = pattern.replace("**", "").replace("*", "").strip("/")
        if reason and stem and reason.strip().strip("—- ") == stem:
            empty.append((n, pattern))
    assert not empty, (
        "rows whose reason merely repeats the path:\n"
        + "\n".join(f"  line {n}: {p}" for n, p in empty))


def test_no_pattern_appears_twice() -> None:
    seen: dict[str, int] = {}
    dupes = []
    for n, pattern, _ in _rows():
        if pattern in seen:
            dupes.append((pattern, seen[pattern], n))
        else:
            seen[pattern] = n
    assert not dupes, (
        "duplicate allowlist patterns (two justifications for one exception):\n"
        + "\n".join(f"  {p}: lines {a} and {b}" for p, a, b in dupes))


def _destdir_staged_paths() -> dict[str, list[str]]:
    """Paths a recipe WRITES AS A FILE into DESTDIR, mapped to the recipes.

    Only file writes count. `install -d "$DESTDIR/etc/environment.d"` creates a
    directory in the staging tree, and that does NOT prove the directory is
    owned: the package manifest carries a known directory-row gap, which is
    precisely why the drop-in-directory rows in this allowlist exist. Treating
    a staged directory as proof of ownership would report every one of those
    rows as stale and the check would be wrong twenty-one times over.

    Deliberately conservative in the other direction too: it reads literal
    paths off a line that mentions DESTDIR and a file-writing verb, so a path
    assembled from variables is invisible to it. A miss is silence, not a false
    clean — the check reports what it can prove, and
    test_currency_check_detects_a_planted_stale_row proves it can still prove
    something.
    """
    staged: dict[str, list[str]] = {}
    path_re = re.compile(r"DESTDIR[^\n]*?((?:etc|usr|opt|var|boot)/[A-Za-z0-9._/*+-]+)")
    writes_a_file = re.compile(
        r"(?:cat\s*>|printf[^|]*>|tee\s|install\s+-[A-Za-z]*m|install\s+-m|"
        r"\bcp\s|\bmv\s|>\s*[\"\']?\$\{?DESTDIR)")
    makes_a_dir = re.compile(r"install\s+-d|\bmkdir\b")
    for build in sorted(PACKAGES.glob("*/*/build.sh")):
        try:
            text = build.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if "DESTDIR" not in line:
                continue
            if makes_a_dir.search(line) or not writes_a_file.search(line):
                continue
            for m in path_re.finditer(line):
                path = m.group(1).rstrip("/")
                staged.setdefault(path, []).append(str(build.relative_to(REPO)))
    return staged


def _row_covers(pattern: str, path: str) -> bool:
    """Does an allowlist pattern claim ownership of a staged path?"""
    if pattern.endswith("/**"):
        prefix = pattern[:-3].rstrip("/")
        return path == prefix or path.startswith(prefix + "/")
    if pattern.endswith("**"):
        return path.startswith(pattern[:-2])
    if "*" in pattern:
        return False        # a glob row needs a person, not a substring guess
    return path == pattern


def test_no_row_is_owed_to_an_already_fixed_recipe() -> None:
    """The check that would have caught the seventeen stale rows.

    A row whose path a recipe now stages into DESTDIR is an exception for a
    problem that has been fixed: the builder's manifest records the file, so
    the ownership gate would accept it with no exception at all.
    """
    staged = _destdir_staged_paths()
    assert staged, (
        "no DESTDIR-staged paths were found in any recipe — the currency check "
        "read nothing, so its silence would mean nothing")

    stale = []
    for lineno, pattern, _reason in _rows():
        if pattern in CURRENCY_EXPECTED:
            continue
        for path, recipes in staged.items():
            if _row_covers(pattern, path):
                stale.append((lineno, pattern, path, recipes[0]))
                break

    assert not stale, (
        "allowlist rows whose recipe fix has already landed — the path is "
        "staged into DESTDIR, so the manifest records it and the exception is "
        "no longer needed:\n"
        + "\n".join(
            f"  line {n}: {p}\n      staged as {path} by {recipe}"
            for n, p, path, recipe in stale))


def test_currency_check_detects_a_planted_stale_row() -> None:
    """Prove the instrument before trusting the zero it reports.

    A check that has never been shown to fire cannot certify that nothing is
    stale. This plants a row against a path the recipes really do stage and
    requires the matcher to catch it.
    """
    staged = _destdir_staged_paths()
    known_staged = sorted(staged)[0]
    assert _row_covers(known_staged, known_staged), (
        "the matcher did not flag an exact-path row against a path the recipes "
        "stage into DESTDIR — it cannot be trusted to have found nothing")

    subtree_row = known_staged.rsplit("/", 1)[0] + "/**"
    assert _row_covers(subtree_row, known_staged), (
        "the matcher did not flag a subtree row covering a staged path")

    assert not _row_covers("etc/definitely-not-staged-anywhere", known_staged)


def test_every_currency_exception_is_still_a_real_row() -> None:
    """An exception for a row that no longer exists hides the next one that
    takes its place."""
    patterns = {p for _, p, _ in _rows()}
    for expected in CURRENCY_EXPECTED:
        assert expected in patterns, (
            f"CURRENCY_EXPECTED names {expected}, which is not an allowlist row")
