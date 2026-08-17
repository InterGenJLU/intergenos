"""Every first-party package's recorded content_hash equals its computed one.

WHY THIS TEST HAD TO EXIST. `scripts/bump-changed-releases.py --check` compares
the `content_hash:` recorded in each package.yml against the fingerprint
computed from the package's real content, and that comparison is the only thing
standing between "somebody edited a shipped file" and "the release number still
claims the previous bytes". It ran in two places, and NEITHER of them covers the
ordinary path a change takes into the tree:

  * the validate phase of a build — which a branch does not run;
  * a release-checker pass — from which branch pushes are exempt by design.

So a commit could move a first-party package's content, leave the recorded
baseline behind, and reach a reviewer with nothing having compared the two. The
suite is where that comparison belongs, because the suite runs on the change
itself. This file is that comparison.

It reads the REAL packages tree — not a fixture — because the claim under test
is about the real recorded baselines. The fixture-based test at the bottom is
the negative control: it proves the comparison detects a planted mismatch, so a
green run above means "the baselines agree" rather than "the test found nothing
to look at".

Nothing here writes to the tree, reads the network, or needs privilege.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BUMP_SCRIPT = REPO_ROOT / "scripts" / "bump-changed-releases.py"
PACKAGES_DIR = REPO_ROOT / "packages"
SOURCES_DIR = REPO_ROOT / "build" / "sources"


def _load_bump_module():
    """Load the bump helper by path — scripts/ is not an importable package.

    The test drives the tool's OWN helpers rather than a re-implementation of
    them. A second copy of the fingerprint rule in a test file would pass while
    the tool it is supposed to be guarding diverged underneath it.
    """
    sys.path.insert(0, str(REPO_ROOT / "igos-build"))
    spec = importlib.util.spec_from_file_location("bump_changed_releases", BUMP_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bump = _load_bump_module()


def _first_party_packages():
    """Yield (name, package.yml path, parsed template) for trackable packages."""
    for yml in bump.discover_templates(PACKAGES_DIR):
        try:
            pkg = bump.parse_template(yml)
        except bump.TemplateError:
            continue          # parse errors are the tier-validator's finding, not this one
        if bump._is_trackable(pkg):
            yield pkg.name, yml, pkg


def test_recorded_content_hash_matches_computed():
    """Every first-party package: recorded baseline == fingerprint of its content."""
    checked, skipped, mismatched, unbaselined = [], [], [], []

    for name, yml, pkg in _first_party_packages():
        # A package whose generated tarball is not staged cannot be fingerprinted
        # honestly (the tool refuses for the same reason). Skipping is correct —
        # but skips are counted and asserted on below, because a run that skipped
        # everything would otherwise report success.
        if bump._missing_generated_tarball(pkg, SOURCES_DIR):
            skipped.append(name)
            continue
        if bump._missing_source_tree_path(pkg):
            skipped.append(name)
            continue

        computed = bump.content_fingerprint(pkg, SOURCES_DIR)
        if not computed:
            skipped.append(name)
            continue

        recorded = bump._read_recorded(yml.read_text())
        if recorded is None:
            unbaselined.append(name)
            continue
        checked.append(name)
        if recorded != computed:
            mismatched.append(f"{name}: recorded {recorded[:16]} != computed {computed[:16]}")

    # A test that checked nothing must never read as a pass. The figure is a
    # floor, not the exact count, so adding or retiring a package does not make
    # this test fail for the wrong reason.
    assert len(checked) >= 50, (
        f"only {len(checked)} first-party package(s) were actually compared "
        f"({len(skipped)} skipped) — this run proves almost nothing; check that "
        f"the packages tree and {SOURCES_DIR} are intact"
    )

    assert not unbaselined, (
        "first-party package(s) carry no content_hash baseline, so nothing "
        "compares their shipped bytes against their release number: "
        + ", ".join(sorted(unbaselined))
        + "\n  Run: python3 scripts/bump-changed-releases.py"
    )

    assert not mismatched, (
        "first-party package content changed without the recorded baseline "
        "following it — the release number describes different bytes than the "
        "package now contains:\n  " + "\n  ".join(sorted(mismatched))
        + "\n  Run: python3 scripts/bump-changed-releases.py  (bumps release and "
          "records the new baseline)"
    )


def test_the_comparison_detects_a_planted_mismatch(tmp_path):
    """NEGATIVE CONTROL: corrupt one recorded baseline in a throwaway copy of a
    real package and prove the same comparison reports it.

    Without this, `test_recorded_content_hash_matches_computed` above could pass
    on a tree where the fingerprint had quietly become a constant, and nobody
    would know the difference between "in sync" and "not looking".
    """
    # A package that declares source_tree: points at paths OUTSIDE its own
    # recipe directory, which a one-package copy does not carry — the tool would
    # then refuse for that reason and the control would prove nothing about
    # mismatch detection. Take one that fingerprints from its own directory.
    victim = None
    for name, yml, pkg in _first_party_packages():
        if getattr(pkg, "source_tree", None):
            continue
        if bump._missing_generated_tarball(pkg, SOURCES_DIR):
            continue
        if bump.content_fingerprint(pkg, SOURCES_DIR):
            victim = (name, yml)
            break
    assert victim, "no self-contained first-party package available to build the control on"
    name, yml = victim

    # Rebuild the minimum repo shape the fingerprint needs: <root>/packages/<tier>/<name>.
    root = tmp_path / "repo"
    tier = yml.parent.parent.name
    dest = root / "packages" / tier / yml.parent.name
    dest.parent.mkdir(parents=True)
    shutil.copytree(yml.parent, dest)

    def run(*extra):
        return subprocess.run(
            [sys.executable, str(BUMP_SCRIPT),
             "--packages-dir", str(root / "packages"),
             "--sources-dir", str(SOURCES_DIR), *extra],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )

    # The fingerprint folds a whole-tree component, so the copy's own digest is
    # NOT the real tree's. Drop the inherited baseline and let a plain run record
    # the digest that belongs to this context — otherwise the check below would
    # report drift no matter what was planted, and pass for the wrong reason.
    text = (dest / "package.yml").read_text()
    inherited = bump._read_recorded(text)
    assert inherited, "the copied package lost its baseline"
    (dest / "package.yml").write_text(
        "\n".join(l for l in text.splitlines() if not l.startswith("content_hash:")) + "\n")

    established = run()
    assert established.returncode == 0, f"{established.stdout}\n{established.stderr}"

    # CONTROL, CLEAN DIRECTION: with the baseline matching, --check must be quiet.
    clean = run("--check")
    assert clean.returncode == 0, (
        "the copied package does not check clean before anything is planted, so "
        f"a drift report below would prove nothing:\n{clean.stdout}\n{clean.stderr}")

    # CONTROL, DIRTY DIRECTION: plant a syntactically valid but wrong digest.
    text = (dest / "package.yml").read_text()
    recorded = bump._read_recorded(text)
    corrupt = ("0" * len(recorded)) if not recorded.startswith("0") else ("1" * len(recorded))
    (dest / "package.yml").write_text(text.replace(recorded, corrupt, 1))

    res = run("--check")
    assert res.returncode == 1, (
        f"a planted baseline mismatch on {name} was NOT reported "
        f"(exit {res.returncode})\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
    assert "CONTENT DRIFT" in res.stderr, res.stderr


def test_check_and_rebaseline_are_refused_together():
    """--check writes nothing; --rebaseline exists to write. Together they used
    to be accepted, and the re-baseline branch runs first — so the run WROTE
    baselines and then printed the summary --check prints when it has found no
    drift. The refusal is at argument-parse time, which is the only place it can
    happen before a package.yml is opened."""
    res = subprocess.run(
        [sys.executable, str(BUMP_SCRIPT), "--check", "--rebaseline",
         "--packages-dir", str(PACKAGES_DIR)],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert res.returncode == 2, (
        f"the flag combination was not refused (exit {res.returncode})\n"
        f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
    assert "cannot be combined" in res.stderr, res.stderr
    # argparse errors before main() does any work; nothing may have been written.
    assert "RE-BASELINED" not in res.stdout
    assert "in sync" not in res.stdout


def test_each_flag_alone_still_works():
    """The refusal must not have cost either flag its own behaviour — a guard
    that also breaks the legitimate single-flag paths is not a fix."""
    res = subprocess.run(
        [sys.executable, str(BUMP_SCRIPT), "--check",
         "--packages-dir", str(PACKAGES_DIR), "--sources-dir", str(SOURCES_DIR)],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert res.returncode == 0, f"--check alone failed:\n{res.stdout}\n{res.stderr}"
    assert "in sync" in res.stdout
