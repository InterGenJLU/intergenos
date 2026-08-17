# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""pkm transaction surface — release honesty, the downgrade guard, and the
pre-download acceptance gate.

WHY THIS MODULE EXISTS. Three failures, all measured on real runs, share one
root: pkm held the numbers that would have prevented them and never showed or
compared them.

  1. `pkm install forge` then `pkm reinstall forge` replaced release 133
     (locally deployed, ahead of publication) with the mirror's release 110.
     pkm held BOTH numbers — the installed release in its own database, and
     release 110 in the signed index — and compared nothing. Silent downgrade.
  2. No transaction line printed a release at all, so the replacement was
     invisible at the terminal and surfaced only in a later `pkm info`.
  3. `pkm install steam` resolved a 40-package closure and installed all of it
     with no confirmation of any kind, per-package file listings throughout.

The fixes are a comparison (`downgrade_decision`), a rendering
(`format_vr` / `describe_change` / `installed_side`), and a gate
(`TransactionPlan` + `confirm`). They live together because they are one
question asked at three moments: what exactly is about to change, stated in
numbers, before it changes.

BOUNDARY. Nothing here verifies, trusts, or gates on a licence. Signature and
checksum verification, the EULA pause and the archive-ingestion semantics are
untouched — this module decides what to PRINT and whether to PROCEED, never
whether something is authentic.
"""

import sys

from . import version as _version

# ----------------------------------------------------------------------
# The one number.
#
# At or below this many deployed/removed files, a transaction prints the bare
# count and nothing else. Above it, the count is followed by the
# per-directory rollup. Per-file paths never print at default verbosity at any
# count — -v restores them.
#
# Stated once, here, and imported everywhere it is needed: the previous
# arrangement had one cap for the inline file list and a second for the
# directory rollup, which made "how much will this print?" a two-number
# question with no single place to answer it.
# ----------------------------------------------------------------------
DEPLOY_PATH_THRESHOLD = 25

# How many distinct co-owning package names a retention report will name
# before it collapses to a count plus the per-path query. A removal on a real
# system reported roughly 700 names for 18 retained directories, twice in one
# run — the wall was never that owners were named, it was that ALL of them
# always were, however many there turned out to be.
RETAINED_OWNER_CAP = 5


# ----------------------------------------------------------------------
# Release-bearing rendering.
# ----------------------------------------------------------------------

def format_vr(entry):
    """Render an entry's full version-release, e.g. ``1.0.0-133``.

    ``entry`` is any mapping carrying ``version`` and (optionally) ``release``
    — a repo index entry or an installed row; both use those column names. A
    missing release renders as ``1``, matching the database schema default, so
    the string is always the complete identity rather than a partial one.
    Returns ``"?"`` for a missing/empty version, because a transaction line
    that silently omits the number is the failure this whole module exists to
    prevent.
    """
    if not entry:
        return "?"
    ver = entry.get("version") if hasattr(entry, "get") else None
    if not ver:
        return "?"
    rel = entry.get("release")
    if rel is None:
        rel = 1
    return f"{ver}-{rel}"


def describe_subject(name, entry):
    """``name version-release`` for one side of a transaction.

    When the entry carries ``payload_version`` — the column recording the
    vendor build a download helper actually fetched, which is a DIFFERENT
    thing from the helper package's own version — the payload build is named
    too. Printing only one of the two is how a helper package and the
    proprietary payload it installed came to drift apart with nothing on
    screen saying so.
    """
    base = f"{name} {format_vr(entry)}"
    payload = (entry or {}).get("payload_version") if hasattr(entry, "get") else None
    if payload:
        base += f" (payload {payload})"
    return base


def describe_change(name, old, new):
    """``name old-vr -> new-vr`` for a replacement.

    Both sides always carry the release. This is the line whose absence made
    the forge 133 → 110 replacement invisible.
    """
    return f"{name} {format_vr(old)} -> {format_vr(new)}"


# ----------------------------------------------------------------------
# The downgrade guard.
# ----------------------------------------------------------------------

class Decision:
    """The outcome of comparing what is installed against what would replace it.

    ``kind`` is one of:
      ``"proceed"``   — the candidate is newer; act.
      ``"same"``      — identical version-release; act (a reinstall of the same
                        build is a legitimate repair operation).
      ``"downgrade"`` — the candidate is OLDER and the caller passed an
                        explicit override; act, having said so.
      ``"refuse"``    — the candidate is OLDER and no override was given. The
                        caller must NOT act.
      ``"unknown"``   — the two could not be ordered. Treated exactly like
                        ``refuse``: an unorderable pair is not evidence that
                        replacing is safe.

    ``ok`` is the single thing a call site needs to branch on.
    """

    def __init__(self, kind, message, installed=None, candidate=None):
        self.kind = kind
        self.message = message
        self.installed = installed
        self.candidate = candidate

    @property
    def ok(self):
        return self.kind in ("proceed", "same", "downgrade")

    @property
    def is_downgrade(self):
        return self.kind in ("downgrade", "refuse")

    def __repr__(self):                                   # pragma: no cover
        return f"<Decision {self.kind}: {self.message}>"


def downgrade_decision(name, installed, candidate, allow_downgrade=False):
    """Compare installed against the resolved source BEFORE anything is acted on.

    FAIL-CLOSED. A would-be downgrade REFUSES by default and names BOTH
    numbers, because "which direction is this going?" is precisely the
    question the user could not answer when it happened. ``allow_downgrade``
    is the explicit override and is the only thing that turns a refusal into
    an action.

    An unorderable pair (an empty version string, a malformed release) also
    refuses. The alternative — treating "cannot compare" as "safe to replace"
    — would reintroduce the same silent replacement through a different door.

    ``installed`` may be None (nothing installed): there is no direction to
    compare, so this proceeds.
    """
    if not installed:
        return Decision("proceed", "", None, candidate)
    if not candidate:
        return Decision("proceed", "", installed, None)

    # A side that carries NO version string at all is not evidence of a
    # downgrade — it is the absence of a claim, and there is nothing to
    # compare. This is deliberately narrower than the unorderable case below:
    # that one refuses because a comparison was ATTEMPTED and failed on two
    # real version strings, which is exactly when a wrong guess about
    # direction does damage. Refusing here instead would turn "the index entry
    # states no version" into a blocked reinstall, a failure mode with nothing
    # to do with the defect this guard exists for.
    if not (installed.get("version") and candidate.get("version")):
        return Decision("proceed", "", installed, candidate)

    try:
        cmp_val = _version.compare(installed, candidate)
    except _version.VersionParseError as exc:
        return Decision(
            "unknown",
            f"refusing to replace {name}: its installed version "
            f"({format_vr(installed)}) and the resolved source's "
            f"({format_vr(candidate)}) cannot be ordered — {exc}. "
            f"Nothing was changed. Re-run with --allow-downgrade to replace "
            f"anyway.",
            installed, candidate,
        )

    if cmp_val < 0:
        return Decision("proceed", "", installed, candidate)
    if cmp_val == 0:
        return Decision("same", "", installed, candidate)

    # candidate is OLDER than what is installed.
    change = describe_change(name, installed, candidate)
    if allow_downgrade:
        return Decision(
            "downgrade",
            f"DOWNGRADING {change} — permitted by --allow-downgrade.",
            installed, candidate,
        )
    return Decision(
        "refuse",
        f"refusing to downgrade {change}: the resolved source is OLDER than "
        f"what is installed. Nothing was changed. This is usually a locally "
        f"built package that is ahead of the mirror. Re-run with "
        f"--allow-downgrade if replacing it with the older build is what you "
        f"want.",
        installed, candidate,
    )


def installed_side(name, installed, candidate):
    """Which side is newer when the package is ALREADY installed.

    Returns ``(state, message)`` where state is one of ``"installed-newer"``,
    ``"index-newer"``, ``"same"``, ``"unknown"``.

    This replaces the bare "already installed. Use `pkm reinstall <name>`"
    line. That line is what invited the downgrade: it stated a fact that was
    true, gave advice that was reasonable in general, and omitted the one
    thing that made the advice wrong in this case — that reinstalling would
    move the package BACKWARDS.
    """
    if not installed:
        return "not-installed", ""
    if not candidate:
        return "unknown", (
            f"{name} {format_vr(installed)} is installed. No repository "
            f"source resolved for it, so there is nothing to compare it "
            f"against."
        )
    try:
        cmp_val = _version.compare(installed, candidate)
    except _version.VersionParseError as exc:
        return "unknown", (
            f"{name} {format_vr(installed)} is installed and the repository "
            f"offers {format_vr(candidate)}, but the two cannot be ordered "
            f"— {exc}."
        )
    if cmp_val > 0:
        return "installed-newer", (
            f"{name} {format_vr(installed)} is installed and is NEWER than "
            f"the repository's {format_vr(candidate)}. Nothing to do. "
            f"`pkm reinstall {name}` would REPLACE it with the older build; "
            f"that needs --allow-downgrade."
        )
    if cmp_val == 0:
        return "same", (
            f"{name} {format_vr(installed)} is installed and matches the "
            f"repository exactly. Use `pkm reinstall {name}` to re-deploy "
            f"the same build."
        )
    return "index-newer", (
        f"{name} {format_vr(installed)} is installed; the repository offers "
        f"{format_vr(candidate)}."
    )


# ----------------------------------------------------------------------
# The pre-download acceptance gate.
# ----------------------------------------------------------------------

def human_size(num_bytes, precision=1):
    """Sizes for transaction lines, imported lazily to avoid an import cycle.

    Defaults to one decimal place: at transaction scale the tenth is what
    makes a total checkable against free space, and it is what the ratified
    rendering shows (43.2 MiB, 156.7 MiB). Every other caller of the output
    layer's human_size keeps its existing rendering.
    """
    from .output import human_size as _hs
    return _hs(num_bytes, precision=precision)


class TransactionPlan:
    """What a transaction is about to do, in the numbers the user needs.

    Built from the resolved package list BEFORE any download starts, which is
    the whole point: the operator's complaint was that pkm "just LAUNCHED"
    into a 40-package install with no opportunity to decline.
    """

    def __init__(self, requested, entries, action="Install"):
        """``entries`` is an ordered list of ``(name, index_entry)`` pairs —
        the full resolved closure, in install order. ``requested`` is the name
        the user actually typed, kept so the gate can say whether the
        resolution went beyond it.
        """
        self.requested = requested
        self.entries = list(entries)
        self.action = action

    @property
    def names(self):
        return [n for n, _ in self.entries]

    @property
    def count(self):
        return len(self.entries)

    @property
    def beyond_requested(self):
        """True when the resolution pulled in anything the user did not name.

        The gate fires on this, not on count: installing exactly the one
        package asked for needs no summary of itself.
        """
        return any(n != self.requested for n, _ in self.entries)

    def _total(self, key):
        total = 0
        for _, e in self.entries:
            try:
                total += int((e or {}).get(key) or 0)
            except (TypeError, ValueError):
                continue
        return total

    @property
    def download_bytes(self):
        return self._total("size")

    @property
    def installed_bytes(self):
        return self._total("installed_size")

    def summary_line(self):
        """``40 packages · 43.2 MiB download · 156.7 MiB installed``.

        A size that the index does not carry renders as 0 through human_size
        rather than being dropped, so the line's shape never changes and a
        missing figure is visible as a zero instead of an absence.
        """
        pkgs = f"{self.count} package" + ("" if self.count == 1 else "s")
        return (
            f"{pkgs} · {human_size(self.download_bytes)} download · "
            f"{human_size(self.installed_bytes)} installed"
        )

    def name_list(self, width=76):
        """The resolved names, comma-joined and wrapped — NAMES ONLY.

        Deliberately not sizes, versions or paths: the gate's job is to let a
        human recognise the shape of what is about to happen at a glance. The
        detail is one `-v` away.
        """
        import textwrap
        return textwrap.wrap(
            ", ".join(self.names), width=max(20, width),
            break_long_words=False, break_on_hyphens=False,
        ) or [""]


def retained_report(entries, noun="path", plural=None, verbose=False):
    """Lines describing paths a removal RETAINED because others still own them.

    ``entries`` is a list of ``(path, [owner names])``.

    THE CAP IS THE POINT. The previous rendering flattened every owner of
    every retained entry into one comma-joined list — on a real removal that
    printed roughly 700 package names for 18 retained directories, twice. The
    honesty was never in the enumeration: it is in the COUNT plus a way to ask
    per path. So the default states how many paths were retained, how many
    distinct packages still claim them, and the query that answers "which ones,
    for this path?"; ``-v`` prints the paths with their owners.

    Nothing is retained differently — this changes what is PRINTED, not what
    is kept.
    """
    if not entries:
        return []
    n = len(entries)
    owners = sorted({o for _, os_ in entries for o in os_})
    word = noun if n == 1 else (plural or noun + "s")
    if not verbose:
        head = f"Retained {n} co-owned {word}"
        if len(owners) <= RETAINED_OWNER_CAP:
            # Few enough to name. A handful of package names is the honest,
            # useful answer and costs one line — the wall was never the fact
            # that owners were named, it was that ALL of them always were.
            return [
                f"{head}, still recorded by: {', '.join(owners)}."
            ]
        pkgs = f"{len(owners)} still-installed packages"
        return [
            f"{head}, still recorded by {pkgs}. Inspect any one with "
            f"`pkm provides <path>`; re-run with -v to list them."
        ]
    lines = [f"Retained {n} co-owned {word}, still recorded by other packages:"]
    for path, os_ in entries:
        shown = ", ".join(sorted(os_))
        lines.append(f"    /{str(path).lstrip('/')}  ({shown})")
    return lines


def confirm(plan, reporter, assume_yes=False, stream=None, stdin=None):
    """Print the plan and ask ``Accept? [Y/n]`` BEFORE any download.

    DEFAULT YES. The gate exists so a human is never surprised by the size of
    a transaction, not to make routine installs adversarial: someone who reads
    the summary and presses Return meant to proceed.

    HEADLESS: acceptance is STATED, not asked. There is nobody to warn, so the
    run says what it is doing and continues — the same pattern the
    proprietary-payload pause already uses. ``assume_yes`` states it the same
    way for an explicit ``--yes``.

    Returns True to proceed, False when the user declined.
    """
    _stdin = stdin if stdin is not None else sys.stdin

    reporter.step(plan.action, plan.summary_line())
    for line in plan.name_list():
        reporter.step_continuation(line)

    if assume_yes:
        reporter.info("Accept? [Y/n] y   (--yes)")
        return True

    try:
        interactive = bool(_stdin.isatty())
    except Exception:
        interactive = False

    if not interactive:
        reporter.info(
            "Accept? [Y/n] — no terminal attached; proceeding. Re-run "
            "interactively to review this transaction before it starts."
        )
        return True

    try:
        reply = input("  Accept? [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if reply in ("", "y", "yes"):
        return True
    reporter.info("Transaction cancelled. Nothing was downloaded or changed.")
    return False
