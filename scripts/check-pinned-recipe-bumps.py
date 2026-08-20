#!/usr/bin/env python3
"""Fail-closed gate: an UNTRACKED recipe whose build.sh changed must carry a hand bump.

THE HOLE THIS CLOSES, measured before it was written (2026-08-16).

`bump-changed-releases.py` is the machine that owns `release:` and `content_hash:`, and
the pre-push release checker (gate 9b) enforces it. But that machine only evaluates
packages `_is_trackable()` accepts: a package that (1) pins a sha256 upstream source,
(2) declares no `source_tree:`, and (3) ships no first-party sibling file beyond
build.sh / package.yml is never enumerated at all. Its build.sh is real, installed
bytes — `content_fingerprint` would happily hash it — but nothing ever asks.

Measured on the real tree at cf6fe6b05, pristine worktree, checker clean at baseline
(exit 0, zero drift lines, zero refusals):

    packages/compute/rocminfo   _is_trackable=False, recorded content_hash ABSENT
    edit build.sh (sha 2026e253... -> 0e8873be...), version/release untouched
    -> `bump-changed-releases.py --check` names NOTHING

with the positive control proving the instrument is not simply mute: the same edit to
packages/ai/intergen (which IS tracked) produced
`intergen: content changed but release not bumped (recorded 1090327c1ad7 != now ...)`.

That is the rocminfo class recorded in the project's release ledger, and it reached the
publish preflight before anything refused it. Worse than a late refusal: mirror-only
delivery means an unbumped fix reaches no machine at all. The recipe's own r2 note,
hand-written 2026-08-13, says the same thing in the author's words — "no instrument
catches this class here. Any future content change to this recipe needs the same hand
bump." This gate is that instrument.

WHAT IT DOES, and the boundary it does not cross: it VERIFIES, it never bumps. The
machine owns the numbers. For every package whose build.sh changed across the push
range and which the machine does NOT track, the pushed content must advance `release:`
or `version:`. Tracked packages are skipped entirely — gate 9b already covers them, and
firing on both would double-report and fight the branch/co-bump flow.

The trackability predicate is IMPORTED from bump-changed-releases.py rather than
restated, so the two can never disagree about which packages the machine watches. A
second copy of that rule would drift, and the drift would be silent.
"""

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "igos-build"))

PKG_BUILD_RE = "packages/*/*/build.sh"


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          check=True, capture_output=True, text=True).stdout


def _load_bump_module(repo):
    """Import bump-changed-releases.py from the tree under test.

    Imported by path because the filename carries hyphens. Anchored to the tree being
    evaluated so the predicate matches the code actually being pushed, not whatever
    happens to sit in the checkout this gate was launched from.
    """
    path = Path(repo) / "scripts" / "bump-changed-releases.py"
    spec = importlib.util.spec_from_file_location("_bump_changed_releases", path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(Path(repo) / "igos-build"))
    spec.loader.exec_module(mod)
    return mod


def _release_and_version(text):
    """(release, version) parsed out of package.yml text, each None when absent."""
    rel = ver = None
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith("release:") and rel is None:
            frag = s[len("release:"):].split("#", 1)[0].strip()
            try:
                rel = int(frag)
            except ValueError:
                rel = None
        elif s.startswith("version:") and ver is None:
            ver = s[len("version:"):].split("#", 1)[0].strip().strip('"\'')
    return rel, ver


def _show(repo, sha, path):
    try:
        return _git(repo, "show", f"{sha}:{path}")
    except subprocess.CalledProcessError:
        return None


def check(repo, base, head, worktree):
    """Violations across base..head. `worktree` is a checkout AT head, used to parse
    recipes with the real parser (parse_template needs files on disk)."""
    bump = _load_bump_module(worktree)
    from parser import parse_template  # noqa: E402  (resolved via igos-build on sys.path)

    changed = _git(repo, "diff", "--name-only", f"{base}..{head}", "--",
                   PKG_BUILD_RE).split()
    violations = []
    for build_sh in changed:
        pkg_dir = str(Path(build_sh).parent)
        yml_rel = f"{pkg_dir}/package.yml"
        yml_head = _show(repo, head, yml_rel)
        if yml_head is None:
            continue  # recipe deleted in this range: nothing published to bump

        # NO-GATE honored the same way every other pre-push gate honors it: if any
        # commit in the range that touched this build.sh declares it, the package is
        # exempt for this push.
        log = _git(repo, "log", "--format=%B%x00", f"{base}..{head}", "--", build_sh)
        if any(body.lstrip().startswith("NO-GATE:") or "\nNO-GATE:" in body
               for body in log.split("\x00")):
            continue

        yml_path = Path(worktree) / yml_rel
        if not yml_path.exists():
            continue
        try:
            pkg = parse_template(yml_path)
        except Exception as e:                       # noqa: BLE001
            # Fail CLOSED: a recipe this gate could not parse has been checked by
            # nothing, and reporting it as clean is exactly the silent pass the gate
            # exists to remove.
            violations.append(f"{pkg_dir}: refused — package.yml could not be parsed "
                              f"({e}); a recipe that cannot be read is never reported "
                              f"as in sync")
            continue

        if bump._is_trackable(pkg):
            continue  # the machine watches this one; gate 9b enforces it

        old_rel, old_ver = _release_and_version(_show(repo, base, yml_rel))
        new_rel, new_ver = _release_and_version(yml_head)
        if old_rel is None and new_rel is None:
            continue  # no release line at all: preflight-tier territory, not ours
        advanced = (
            (old_ver is not None and new_ver is not None and new_ver != old_ver)
            or (old_rel is not None and new_rel is not None and new_rel > old_rel)
            or (old_rel is None and new_rel is not None)
        )
        if not advanced:
            violations.append(
                f"{pkg_dir}: build.sh changed but (version,release) did not advance "
                f"— version {old_ver!r}->{new_ver!r}, release {old_rel}->{new_rel}. "
                f"This recipe pins an upstream source and ships no first-party sibling "
                f"files, so bump-changed-releases.py does NOT track it and will never "
                f"name it. Measured: a build.sh edit here moves a COUPLED tracked "
                f"package's fingerprint instead, so the release checker refuses the "
                f"push while pointing at a package you did not touch — and its resolve "
                f"steps bump that one, leaving this recipe unbumped.")
    return violations


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=".")
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--worktree", required=True,
                    help="a checkout at --head used to parse recipes")
    args = ap.parse_args()
    violations = check(args.repo, args.base, args.head, args.worktree)
    if violations:
        print("pinned-recipe bump gate: FAIL")
        for v in violations:
            print(f"  {v}")
        print("  The machine owns release: for the packages it tracks; these are the "
              "ones it does not.")
        print("  Hand-bump release: (and write the matching `# rNN:` note at the chain "
              "head in the same commit), or NO-GATE: <reason>.")
        return 1
    print("pinned-recipe bump gate: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
