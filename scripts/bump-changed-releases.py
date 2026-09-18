#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Auto-bump `release:` on first-party content change.

Eliminates the manual release bump (and the "I rebuilt it but forgot to bump,
so the mirror/pkm never sees the new build" footgun). For every FIRST-PARTY
package — one whose content is NOT a sha-pinned upstream tarball (i.e.
`source: []` or all-`generated: true`) — it computes a content fingerprint
(build.sh + own-dir + declared source_tree + generated-tarball bytes ONLY when
no source_tree declares the inputs, via the SAME hashing the builder's
skip-built check uses) and compares it to the
`content_hash:` baseline recorded in package.yml:

  baseline absent  -> record it (establish), do NOT bump (first sighting).
  baseline == now  -> no-op.
  baseline != now  -> content changed: bump `release:` by 1 and record the new
                      baseline.

The fingerprint EXCLUDES package.yml, so writing the bump + baseline back into
package.yml never re-triggers a bump.

Modes:
  (default, apply)  rewrite package.yml in place (bump + record). Run in
                    phase_verify_sources so a content change always advances
                    the release, and standalone before a commit.
  --check           never writes; exits 1 if any first-party package's content
                    changed without a matching release bump (or has no
                    baseline yet). The fail-closed validate-phase gate.

Requires generated source tarballs to already exist in --sources-dir (run the
generators first — phase_verify_sources does) — EXCEPT for generated-source
packages that declare `source_tree:`: those fingerprint their canonical INPUTS
(asset files + generator script; item-8 design) and never read the tarball, so
they check clean on a bare tree. A generated-source package WITHOUT source_tree
still errors on a missing tarball, so its byte-fingerprint is never computed
against a half-staged source set; a declared-but-missing source_tree path also
errors (fail-closed — a typo'd declaration would hash nothing).
"""
import argparse
import importlib.util
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "igos-build"))

from parser import parse_template, discover_templates, TemplateError  # noqa: E402
from content_hash import (  # noqa: E402
    content_fingerprint, sibling_shipped_bytes, url_basename, repo_root_of,
)

CONTENT_HASH_REL = "igos-build/content_hash.py"


def _git(*args) -> tuple[int, str]:
    """Run git in the repository and return (exit status, stdout)."""
    p = subprocess.run(["git", *args], cwd=str(REPO_ROOT),
                       capture_output=True, text=True)
    return p.returncode, p.stdout


def previous_definition_text(git=_git) -> tuple[str | None, str]:
    """The COMMITTED content_hash.py that the current definition replaces.

    Returns (source text, provenance). The text is None when the previous
    definition cannot be derived, and the provenance then says why, so the
    caller can refuse with a reason instead of guessing.

    Why this is read from git rather than from a flag. --rebaseline used to
    compute "the previous definition" as
    `content_fingerprint(..., include_siblings=False)` — one hardcoded
    expression of the one definition change it was written for (the 2026-08-05
    sibling-files fold). Any LATER definition change therefore made it answer
    about a definition that was already two changes old: on 2026-09-18, with
    `gpu_targets` folded in, it refused all six trackable ROCm packages with
    "content also changed under the previous fingerprint definition" while
    --check, in the same tree, showed their content had not changed at all.
    It failed closed, which is right, but its reason was false, and a tool that
    refuses for a false reason teaches its user to reach past it.

    The definition IS a file in this repository, so the previous one is a fact
    git already holds:

      * working tree copy modified against HEAD -> HEAD's copy is the previous
        definition (the change being absorbed is the uncommitted one);
      * working tree copy clean and HEAD is itself the commit that changed the
        file -> the previous definition is HEAD's parent copy (the change being
        absorbed is that commit);
      * working tree copy clean and HEAD did not change the file -> there is no
        definition change here to absorb, and the caller must refuse.

    THE THIRD RULE IS NOT A TECHNICALITY. Without it the mode would keep
    reaching back to the last definition change however old, which would leave
    it permanently absorbing that one: a later, real change to a field the OLD
    definition ignores would fingerprint identically under it, be read as
    "explained by the definition widening", and be re-baselined with the
    release standing still — the exact silent delivery loss this tool exists to
    stop, wearing the tool's own approval.

    LIMIT, stated rather than hidden: only this one file's definition is read
    back. A definition change that also edits another module is not fully
    reconstructed by this, and such a change must not be absorbed with
    --rebaseline without re-reading it here first.
    """
    rc, _ = git("diff", "--quiet", "HEAD", "--", CONTENT_HASH_REL)
    if rc == 1:
        rc2, text = git("show", f"HEAD:{CONTENT_HASH_REL}")
        if rc2 != 0:
            return None, (f"git could not read HEAD:{CONTENT_HASH_REL}")
        return text, (f"HEAD:{CONTENT_HASH_REL} — the working tree's copy is "
                      f"modified, so HEAD holds the definition it replaces")
    if rc != 0:
        return None, (f"git could not compare {CONTENT_HASH_REL} against HEAD "
                      f"(is this a git worktree?)")
    rc3, out = git("log", "-1", "--format=%H", "--", CONTENT_HASH_REL)
    last = out.strip()
    if rc3 != 0 or not last:
        return None, (f"no commit in this history changes {CONTENT_HASH_REL}, "
                      f"so there is no previous definition to compare against")
    rc5, head = git("rev-parse", "HEAD")
    head = head.strip()
    if rc5 != 0 or not head:
        return None, "git could not resolve HEAD"
    if last != head:
        return None, (f"{CONTENT_HASH_REL} is unchanged in the working tree and "
                      f"was last changed by {last[:12]}, which is not HEAD "
                      f"({head[:12]}), so no definition change is being made here")
    rc4, text = git("show", f"{last}^:{CONTENT_HASH_REL}")
    if rc4 != 0:
        return None, (f"{last[:12]} is the commit that introduced "
                      f"{CONTENT_HASH_REL} and it has no parent, so there is no "
                      f"previous definition to compare against")
    return text, (f"{last[:12]}^:{CONTENT_HASH_REL} — the copy before "
                  f"{last[:12]}, the last commit that changed the definition")


def load_fingerprint_fn(text: str, label: str):
    """Load a content_hash.py source text and hand back its content_fingerprint.

    The module is stdlib-only and duck-types its `pkg`, which is what makes a
    historical copy loadable at all; it is executed from a temporary file that
    is removed immediately, the loaded module object outliving it.
    """
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / f"content_hash_{label}.py"
        path.write_text(text)
        spec = importlib.util.spec_from_file_location(
            f"_content_hash_{label}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    fn = getattr(module, "content_fingerprint", None)
    if fn is None:
        raise AttributeError(
            "the previous definition has no content_fingerprint() to call")
    return fn


# `release:` is an int; capture the prefix + value + any trailing inline
# comment (our release lines conventionally carry a why-comment, e.g.
# `release: 9  # PI-Z6 ...` — the old end-anchored regex failed to match
# those, erroring "no release: line to bump" on exactly the packages we
# annotate; found on the PI-Z15 pkm bump, 2026-07-06). Do NOT consume the
# newline. The bump preserves the comment verbatim.
RELEASE_RE = re.compile(r'^(release:[^\S\n]*)(\d+)([^\S\n]*(?:#[^\n]*)?)$', re.M)
CONTENT_HASH_RE = re.compile(r'^content_hash:[^\S\n]*([0-9a-f]+)[^\S\n]*$', re.M)


def _is_trackable(pkg) -> bool:
    """Track a package for auto-bump if it carries first-party content:
    no sha-pinned upstream source (source:[] or all-generated), OR a declared
    source_tree (a pinned upstream package that nonetheless bakes in a
    first-party asset, e.g. gnome-shell's greeter background), OR any other
    file of our own in its recipe directory.

    THE THIRD CLAUSE, added 2026-08-05, and why it had to exist. The rule used
    to be that a pinned upstream package "is bumped the normal way" — meaning
    by hand, meaning by nothing. A package can pin an upstream tarball and
    still ship files we wrote: install hooks, helper programs, whole files/
    trees, apparmor profiles, systemd units, patches. Those are installed
    bytes and they were watched by nothing.

    What that cost, measured: 68 packages were in exactly that position. A
    probe line planted in one package's shipped hook script left the drift
    check reporting "in sync", and a probe in that same package's own build.sh
    made the check name a DIFFERENT package — because the installer-hooks
    fingerprint couples to every recipe, so the one thing that did move was
    somebody else's. A maintainer editing a hook was told either nothing or
    the wrong name, and changed bytes reached no installed system.

    Having no baseline recorded must never again be the same thing as being
    exempt.
    """
    pinned = any(getattr(s, "sha256", None) for s in (pkg.source or []))
    if (not pinned) or bool(getattr(pkg, "source_tree", None)):
        return True
    return bool(sibling_shipped_bytes(pkg))


def _missing_generated_tarball(pkg, sources_dir: Path):
    """Return the name of a generated source tarball absent from sources_dir,
    or None. Used to refuse hashing against a half-staged source set.

    A package that declares `source_tree:` fingerprints its canonical INPUTS
    (content_hash.py item-8 design) — the tarball bytes are never read — so
    the staging requirement does not apply and --check runs on a bare tree."""
    if getattr(pkg, "source_tree", None):
        return None
    for s in (pkg.source or []):
        if getattr(s, "generated", False):
            name = getattr(s, "filename", None) or url_basename(s.url)
            if not (sources_dir / name).exists():
                return name
    return None


def _missing_source_tree_path(pkg):
    """Return a declared `source_tree:` path absent from the repo, or None.

    Fail-closed (item-8 hardening): iter_tree_files silently no-ops on a
    missing root, so a typo'd declaration would contribute NOTHING to the
    fingerprint — a stub-shaped hole where a real source edit ships stale
    with no bump. Refuse to fingerprint instead."""
    root = repo_root_of(pkg)
    if root is None:
        return None
    for rel in getattr(pkg, "source_tree", None) or []:
        if not (root / rel).exists():
            return rel
    return None


def _read_recorded(text: str):
    m = CONTENT_HASH_RE.search(text)
    return m.group(1) if m else None


def _bump_release(text: str):
    m = RELEASE_RE.search(text)
    if not m:
        return None, None
    old = int(m.group(2))
    new = old + 1
    text = text[:m.start()] + f"{m.group(1)}{new}{m.group(3)}" + text[m.end():]
    return text, (old, new)


def _set_content_hash(text: str, digest: str) -> str:
    if CONTENT_HASH_RE.search(text):
        return CONTENT_HASH_RE.sub(f"content_hash: {digest}", text, count=1)
    # Insert on its own line right after `release:` (stable, human-readable).
    m = RELEASE_RE.search(text)
    if not m:
        raise ValueError("no release: line to anchor content_hash insertion")
    return text[:m.end()] + f"\ncontent_hash: {digest}" + text[m.end():]


def main() -> int:
    ap = argparse.ArgumentParser(description="Auto-bump release on first-party content change")
    ap.add_argument("--packages-dir", default=str(REPO_ROOT / "packages"))
    ap.add_argument("--sources-dir", default=str(REPO_ROOT / "build" / "sources"))
    ap.add_argument("--check", action="store_true",
                    help="report-only gate: exit 1 on any unbumped content change (no writes)")
    ap.add_argument("--rebaseline", action="store_true",
                    help="record the current fingerprint WITHOUT bumping, for a change "
                         "to the fingerprint DEFINITION itself. The previous definition is "
                         "read from the committed igos-build/content_hash.py, so any "
                         "definition change is expressible, not only the one this flag was "
                         "written for. Refuses the whole run when no definition changed, and "
                         "refuses any package whose content also changed under the previous "
                         "definition — that one is a real change and must bump. Cannot be "
                         "combined with --check.")
    args = ap.parse_args()

    # --check promises to write nothing. --rebaseline exists to write. Passed
    # together they used to be accepted, and the re-baseline branch runs before
    # the --check branch is reached, so the run WROTE baselines and then printed
    # the in-sync summary --check prints when it has found no drift: a report of
    # zero work issued after doing work.
    #
    # Refused here, at argument-parse time, so the refusal happens before a
    # single package.yml is opened. A guard placed inside the per-package loop
    # would still be correct for the packages it reached and useless for any it
    # had already rewritten.
    if args.rebaseline and args.check:
        ap.error("--rebaseline and --check cannot be combined: --check writes "
                 "nothing and --rebaseline exists to write. Run --check to see "
                 "what would move, then --rebaseline on its own to record it.")

    # --rebaseline absorbs a change to the fingerprint DEFINITION, so it must
    # first HAVE the previous definition. Derived once, before a single
    # package.yml is opened, and the run refuses as a whole if it cannot be
    # derived or if nothing about the definition actually changed — a
    # re-baseline of a tree whose definition stands still would record real
    # content changes as if they were free, which is the one thing this mode
    # must never do.
    previous_fingerprint = None
    provenance = ""
    if args.rebaseline:
        prev_text, provenance = previous_definition_text()
        if prev_text is None:
            print(f"REFUSED: --rebaseline cannot derive the previous "
                  f"fingerprint definition: {provenance}.", file=sys.stderr)
            return 2
        if prev_text == (REPO_ROOT / CONTENT_HASH_REL).read_text():
            print(f"REFUSED: --rebaseline is for a change to the fingerprint "
                  f"definition, and {CONTENT_HASH_REL} is identical to "
                  f"{provenance}. Nothing here is a definition change, so any "
                  f"drift is real content: run without --rebaseline so the "
                  f"releases bump.", file=sys.stderr)
            return 2
        try:
            previous_fingerprint = load_fingerprint_fn(prev_text, "previous")
        except Exception as e:
            print(f"REFUSED: the previous fingerprint definition "
                  f"({provenance}) could not be loaded: {e}", file=sys.stderr)
            return 2
        print(f"Previous fingerprint definition read from {provenance}.",
              flush=True)

    packages_dir = Path(args.packages_dir)
    sources_dir = Path(args.sources_dir)

    bumped, established, unchanged, drift, errors = [], [], [], [], []
    rebaselined = []
    # A --rebaseline run writes ALL OR NOTHING. Before this, a run that
    # refused two packages had already rewritten the five it reached first,
    # so a person who read the refusal and stopped was left with a tree half
    # re-baselined and no line of output saying so. Every write of that mode
    # is staged here and applied only if the whole run is clean.
    pending: list[tuple[Path, str]] = []

    for yml in discover_templates(packages_dir):
        try:
            pkg = parse_template(yml)
        except TemplateError as e:
            errors.append(f"{yml}: refused — the package template did not parse: {e}")
            continue
        if not _is_trackable(pkg):
            continue

        missing = _missing_generated_tarball(pkg, sources_dir)
        if missing:
            errors.append(
                f"{pkg.name}: refused — generated source '{missing}' is not in "
                f"{sources_dir}. Run the source-tarball generators first, so the "
                f"fingerprint is not computed against a partially staged source set.")
            continue

        missing_tree = _missing_source_tree_path(pkg)
        if missing_tree:
            errors.append(
                f"{pkg.name}: refused — declared source_tree path '{missing_tree}' does "
                f"not exist in the repository. An absent declared path contributes "
                f"nothing to the fingerprint, so the check would report in sync while "
                f"real source edits shipped unbumped. Correct the package.yml or "
                f"restore the path.")
            continue

        fp = content_fingerprint(pkg, sources_dir)
        if not fp:
            continue  # nothing trackable (no build.sh, no out-of-recipe source)

        text = yml.read_text()
        recorded = _read_recorded(text)

        if recorded is None:
            # First sighting — establish baseline, never bump.
            if args.check:
                drift.append(f"{pkg.name}: no content_hash baseline recorded yet")
                continue
            text = _set_content_hash(text, fp)
            if args.rebaseline:
                pending.append((yml, text))
            else:
                yml.write_text(text)
            established.append(pkg.name)
        elif recorded == fp:
            unchanged.append(pkg.name)
        elif args.rebaseline:
            # A fingerprint-DEFINITION change moves every affected package's
            # digest without a single shipped byte moving. Bumping those
            # releases would tell every installed system to re-fetch packages
            # that did not change, and would make the release number a claim
            # about content that is not true.
            #
            # The mode is not a bypass, because it proves what it is absorbing:
            # the package's fingerprint under the OLD definition must still
            # equal the recorded baseline. If it does not, that package really
            # did change, and it is refused here rather than quietly
            # re-baselined — which is the one way this flag could ever have
            # hidden something.
            old_fp = previous_fingerprint(pkg, sources_dir)
            if old_fp != recorded:
                errors.append(
                    f"{pkg.name}: refused — content also changed under the previous "
                    f"fingerprint definition (recorded {recorded[:12]}, "
                    f"previous-definition {old_fp[:12]}, read from {provenance}). "
                    f"That is a content change rather than a definition change; run "
                    f"without --rebaseline so the release bumps.")
                continue
            text = _set_content_hash(text, fp)
            pending.append((yml, text))
            rebaselined.append(f"{pkg.name} {recorded[:12]} -> {fp[:12]}")
        else:
            # Content changed since the recorded release.
            if args.check:
                drift.append(f"{pkg.name}: content changed but release not bumped "
                             f"(recorded {recorded[:12]} != now {fp[:12]})")
                continue
            text2, change = _bump_release(text)
            if text2 is None:
                errors.append(f"{pkg.name}: refused — the package template carries no "
                              f"`release:` line to bump.")
                continue
            text2 = _set_content_hash(text2, fp)
            yml.write_text(text2)
            bumped.append(f"{pkg.name} {change[0]}->{change[1]}")

    # A refusal anywhere in a --rebaseline run discards every staged write.
    # The mode's whole claim is that it absorbed a definition change and
    # nothing else; a tree carrying half of that claim cannot be read back to
    # tell which half.
    if args.rebaseline:
        if errors:
            print(f"REFUSED: nothing was written — {len(errors)} package(s) "
                  f"below are content changes, not definition changes, and a "
                  f"re-baseline run applies all or nothing. "
                  f"{len(pending)} staged re-baseline(s) discarded.",
                  file=sys.stderr, flush=True)
            rebaselined, established, pending = [], [], []
        else:
            for path, text in pending:
                path.write_text(text)

    # --- report ---
    if rebaselined:
        print("RE-BASELINED (fingerprint definition changed; no shipped byte moved, "
              "no release bumped):")
        for r in rebaselined:
            print(f"  {r}")
    if bumped:
        print("BUMPED (content changed):")
        for b in bumped:
            print(f"  release {b}")
        print("  ^ each bump needs its `# rNN:` note at the chain head, in the"
              " same commit — the pre-push release-note gate enforces"
              " (scripts/check-release-notes.py).")
    if established:
        print(f"ESTABLISHED baseline (no bump): {len(established)} package(s)")
        for e in sorted(established):
            print(f"  {e}")
    if args.check and drift:
        print("CONTENT DRIFT — release not bumped for:", file=sys.stderr)
        for d in drift:
            print(f"  {d}", file=sys.stderr)
    if errors:
        # Every message under this heading names what was refused, why, and the
        # correction, in the same shape — so a reader of a failing gate does not
        # have to work out which of the five conditions produced it.
        print("REFUSED:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        return 2
    if args.check and drift:
        return 1
    if not args.check:
        print(f"OK — {len(bumped)} bumped, {len(established)} established, "
              f"{len(rebaselined)} re-baselined, {len(unchanged)} unchanged.")
    else:
        print(f"OK — {len(unchanged) + len(established)} first-party packages in sync.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
