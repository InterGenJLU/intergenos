#!/usr/bin/env python3
"""Fail-closed mint/publish preflight: the changelog must describe the release
being cut, and the site must be ready to announce it.

CHANGELOG.md was absent for R001 and its frame still read v1.0-Unreleased
afterwards. The accumulation gate (scripts/check-changelog-accumulation.py)
keeps the record moving as the shipped set moves; this one is the other end
of the same discipline — the check that runs when a release is actually
minted or published, and refuses if the document does not describe THIS
release.

WHAT IT CHECKS, in order:

  1. IDENTITY. The changelog's top release section names the release
     identity read from the PACKAGED os-release —
     packages/core/intergenos-base-files/files/etc/os-release, which the
     2026-08-19 identity landing made the single place the identity is
     authored. This gate reads that file. It does NOT carry a release
     literal of its own, because a second literal is the exact defect that
     landing removed: two hand-maintained copies and nothing comparing them.

  2. DATED. The top release section carries a real date, not a placeholder.
     The shipped changelog currently reads `## [R001.1] — 2026-08-XX` with a
     comment saying the date is set on publication day, so a publish
     preflight that accepted `2026-08-XX` would pass the exact state it
     exists to catch. Any date whose digits are not all digits is refused.

  3. NON-EMPTY. The top release section has content of its own — not just a
     heading, and not just an HTML comment. An empty section is a heading
     that claims a release was documented when it was not.

  4. SITE READINESS (publish preflight only, --require-site). The site
     repository must carry an Updates entry naming this release, marked
     READY. The site repository is NOT part of this repository and is not
     present on every machine, so its location is an ARGUMENT. Absent
     argument, unreadable path, or missing Updates file: REFUSED. A
     preflight that skipped this check when it could not find the site
     would certify a publish it never examined, which is worse than not
     checking at all.

WHAT IT DOES NOT DO. It does not deploy anything, does not read the live
site, and does not verify a published page. Deployment and live-page
verification belong to the release sequence, not to a preflight — this gate
answers "is the source of truth ready", not "did the publish work".

Exit codes: 0 = ready, 1 = refused (a failure, or any fail-closed condition).
"""

import argparse
import re
import sys
from pathlib import Path

OS_RELEASE_REL = Path("packages/core/intergenos-base-files/files/etc/"
                      "os-release")
CHANGELOG_REL = Path("CHANGELOG.md")

# `## [R001.1] — 2026-08-20` / `## [R001] - 2026-08-16`. The separator is an
# em dash in the shipped document and a hyphen is accepted too.
#
# The trailing `[^\n]*` is load-bearing and was added after the earlier
# `[^\S\n]*$` form was MEASURED failing open: a heading carrying anything
# after the date — `## [R001.1] — 2026-08-XX (date set on publication day)`
# — matched nothing, so the section splitter walked PAST the release being
# cut and evaluated the one below it. The gate printed READY on a changelog
# whose real top section carried the placeholder date it exists to refuse.
# A gate that can silently examine the wrong section is worse than no gate,
# because its green is believed.
SECTION_RE = re.compile(
    r'^##[^\S\n]*\[(?P<name>[^\]]+)\][^\S\n]*(?:[—-][^\S\n]*(?P<date>\S+))?'
    r'[^\n]*$', re.M)

# Every `## ` heading, section-shaped or not. The section walk below refuses
# on any heading it cannot parse that sits ABOVE the first release section,
# rather than reading past it — the same fail-open the trailing-text case
# proved, in its other spelling (`## R001.2 — 2026-09-01`, brackets omitted).
HEADING_RE = re.compile(r'^##[^\S\n]+(?P<text>\S[^\n]*)$', re.M)

UNRELEASED = "unreleased"
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
HTML_COMMENT_RE = re.compile(r'<!--.*?-->', re.S)


def read_release_identity(repo: Path):
    """The release identity from the packaged os-release.

    Returns (identity, problem). Prefers VERSION_ID, which is the
    machine-readable field; the os-release specification limits it to
    lowercase, so comparison against the changelog heading is
    case-insensitive and that is stated rather than silently assumed.
    """
    path = repo / OS_RELEASE_REL
    try:
        text = path.read_text()
    except OSError as e:
        return None, f"cannot read the packaged os-release at {path}: {e}"
    m = re.search(r'^VERSION_ID=(.+)$', text, re.M)
    if not m:
        return None, f"{path} carries no VERSION_ID line"
    value = m.group(1).strip().strip('"').strip("'")
    if not value:
        return None, f"{path} has an empty VERSION_ID"
    return value, None


def top_release_section(changelog_text: str):
    """(name, date, body, problem) for the first non-Unreleased section.

    The walk is over EVERY `## ` heading, not only the section-shaped ones,
    and it refuses on the first heading above the release section that it
    cannot parse. Skipping an unparseable heading instead would mean
    evaluating whichever section happened to sit below it — which is how
    this gate was measured printing READY on a placeholder-dated release.
    """
    headings = list(HEADING_RE.finditer(changelog_text))
    if not headings:
        return None, None, None, "the changelog carries no `## ` headings"
    for i, h in enumerate(headings):
        m = SECTION_RE.match(changelog_text, h.start())
        if not m:
            return None, None, None, (
                f"the heading `## {h.group('text').strip()}` sits above the "
                "first release section and is not in the "
                "`## [name] — YYYY-MM-DD` form this gate reads; refusing "
                "rather than reading past it to a section that may not be "
                "the release being cut")
        name = m.group("name").strip()
        if name.casefold() == UNRELEASED:
            continue
        start = m.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(
            changelog_text)
        return name, (m.group("date") or "").strip(), \
            changelog_text[start:end], None
    return None, None, None, (
        "the changelog has only an [Unreleased] section — nothing describes a "
        "release")


def section_is_empty(body: str) -> bool:
    """True when the section carries nothing but comments and rules."""
    stripped = HTML_COMMENT_RE.sub("", body or "")
    stripped = re.sub(r'^[-_*]{3,}$', "", stripped, flags=re.M)
    return not stripped.strip()


def check_site(site_root, identity):
    """The site repository carries a READY Updates entry for this release."""
    if not site_root:
        return ("--require-site was given but no --site-repo was: the site "
                "location is an argument and this gate refuses to guess it")
    root = Path(site_root)
    if not root.is_dir():
        return f"site repository path is not a readable directory: {root}"
    candidates = [p for p in root.rglob("*")
                  if p.is_file() and "update" in p.name.casefold()
                  and p.suffix.lower() in (".md", ".markdown", ".html", ".txt")]
    if not candidates:
        return (f"no Updates file found under {root} — a publish preflight "
                "cannot confirm the announcement exists")
    ident = identity.casefold()
    for path in candidates:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            low = line.casefold()
            if ident in low and "ready" in low:
                return None
    return (f"no Updates entry naming {identity} and marked READY was found "
            f"under {root} (looked in {len(candidates)} candidate file(s))")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=".", help="repository root")
    ap.add_argument("--site-repo", default=None,
                    help="path to the site repository (required with "
                         "--require-site; this gate never guesses it)")
    ap.add_argument("--require-site", action="store_true",
                    help="publish preflight: also require a READY Updates "
                         "entry naming this release in the site repository")
    args = ap.parse_args()

    repo = Path(args.repo)
    failures = []

    identity, problem = read_release_identity(repo)
    if problem:
        print("changelog release-lockstep preflight: REFUSED")
        print(f"  {problem}")
        return 1

    try:
        changelog = (repo / CHANGELOG_REL).read_text()
    except OSError as e:
        print("changelog release-lockstep preflight: REFUSED")
        print(f"  cannot read {repo / CHANGELOG_REL}: {e}")
        return 1

    name, date, body, problem = top_release_section(changelog)
    if problem:
        failures.append(problem)
    else:
        if name.casefold() != identity.casefold():
            failures.append(
                f"the top release section is [{name}] but the packaged "
                f"os-release says the release is {identity} — the changelog "
                "does not describe the release being cut")
        if not date:
            failures.append(
                f"the [{name}] section carries no date")
        elif not DATE_RE.match(date):
            failures.append(
                f"the [{name}] section's date is {date!r}, which is a "
                "placeholder rather than a date — it is set on publication day")
        if section_is_empty(body):
            failures.append(
                f"the [{name}] section is empty; a heading is not a record")

    if args.require_site:
        site_problem = check_site(args.site_repo, identity)
        if site_problem:
            failures.append(site_problem)

    if failures:
        print("changelog release-lockstep preflight: REFUSED")
        for f in failures:
            print(f"  {f}")
        print(f"  Release identity read from {OS_RELEASE_REL}: {identity}")
        return 1

    print(f"changelog release-lockstep preflight: READY ({identity})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
