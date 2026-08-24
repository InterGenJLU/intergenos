# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""A shipped test must not fail merely because the packaging tree is absent.

The recipe copies every top-level `intergen/tests/*.py` into the installed
package. On a user's machine that package sits under site-packages with no
repository beside it, so any shipped test that reaches for `packages/ai/...`
finds nothing. A test that treats that absence as a FAILURE turns a normal
installed system into a red suite — and a suite that is red for a known,
uninteresting reason is a suite whose real failures stop being read.

The established in-tree answer is to skip: `test_intergen_unit_scoping.py` and
`test_destructive_policy.py` both check for the recipe and skip when it is not
there. This gate holds every shipped test to that same standard, and it holds it
by EXECUTION rather than by reading the source: it builds a real installed
layout, with the package copied in and no repository anywhere above it, and runs
the shipped tests that mention the packaging tree inside it.

Collection alone would not catch this class. The failure the gate exists to
prevent happened in `setUp`, which `--collect-only` never reaches — an
instrument that cannot see the defect it is aimed at is not a gate.

The gate is DIFFERENTIAL. A test that fails in a bare temporary root has not
thereby shown anything about the packaging tree, so every candidate failure is
re-run in an identical layout that differs only by the repository being present.
Only a test that fails without it and passes with it is this gate's finding. A
test that fails in both is named in the output and left to whoever owns it.

The shipped set is DERIVED from the recipe, not listed here, so a newly shipped
offender is caught without anyone remembering to update this file.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
RECIPE = REPO / "packages" / "ai" / "intergen" / "build.sh"
PACKAGE = REPO / "intergen"

#: Marks a test file as reaching ABOVE the installed package — the only thing
#: that can make it depend on the repository.
#:
#: Measured 2026-08-24: a first version of this pattern also matched
#: `packages",`, which occurs in about thirty files as part of the TOOL NAME
#: `manage_packages`. Those files have nothing to do with the packaging tree,
#: and pulling them into the simulation turned a gate into most of a second
#: test suite. Breadth is not free here: every false inclusion is a whole test
#: file executed twice in a temporary root.
#:
#: What remains is the actual signature. `parents[2]` (and its os.path
#: equivalent) is a walk above the package root; `build.sh` and `packages/ai`
#: name the packaging tree outright. A file that reaches the repository
#: without doing one of these has not been seen, and if one appears the gate
#: should be re-derived rather than the pattern widened back to noise.
_REPO_DEPENDENCE = re.compile(
    r"parents\[2\]|build\.sh|packages/ai"
)

#: pytest's short summary names each failure. Parsed so leg B can re-run the
#: FAILING TESTS rather than their whole files: re-running a file to re-check
#: one test costs every other test in it a second time, and one shipped test
#: here starts a real daemon and takes about ninety seconds.
_FAILED_LINE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.M)


def _recipe_ships_top_level_tests() -> bool:
    """True while the recipe installs the top-level tests with a glob.

    If the recipe stops doing that, this gate's premise is gone and the gate
    must be re-derived rather than left passing on an assumption.
    """
    if not RECIPE.is_file():
        return False
    text = RECIPE.read_text(encoding="utf-8")
    return bool(re.search(r"intergen/tests/\*\.py", text))


def _shipped_repo_dependent_tests() -> list[Path]:
    """Top-level shipped test files that mention the packaging tree."""
    found = []
    for path in sorted(PACKAGE.joinpath("tests").glob("test_*.py")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _REPO_DEPENDENCE.search(text):
            found.append(path)
    return found


#: Repository-only directories a shipped test could reach for. They are what
#: leg B puts back, and nothing else changes between the two legs.
_REPO_ONLY_DIRS = ("packages", "scripts")


def _build_installed_layout(root: Path, *, with_repo: bool) -> Path:
    """Build a site-packages holding the package, optionally with the repo bits.

    A copy, not a symlink: `Path.resolve()` follows symlinks, so a symlinked
    package would resolve straight back into the repository and the simulation
    would quietly test nothing.

    `with_repo` is the ONE variable this gate manipulates. Everything else —
    environment, working directory, interpreter — is identical between the two
    legs, so a difference in outcome can only be the packaging tree's presence.
    """
    site = root / "usr" / "lib" / "python3.14" / "site-packages"
    site.mkdir(parents=True)
    shutil.copytree(
        PACKAGE, site / "intergen",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    if with_repo:
        # `parents[2]` from site-packages/intergen/tests/x.py IS site-packages,
        # so the repo-only directories go there for the test to find them.
        for name in _REPO_ONLY_DIRS:
            source = REPO / name
            if source.is_dir():
                # symlinks=True: the packaging tree carries deliberate symlinks,
                # including one that dangles by design (a base-files recipe
                # ships `sbin -> usr/sbin`). Following them makes the copy fail
                # on a tree that is perfectly correct.
                shutil.copytree(
                    source, site / name, symlinks=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                )
    return site


def _failed_node_ids(output: str) -> list[str]:
    return _FAILED_LINE.findall(output)


def _run_in_layout(site: Path, *target: str) -> subprocess.CompletedProcess:
    """Run ONE shipped test file inside the simulated installed system.

    One file per invocation, deliberately. Running the whole selection in a
    single pytest process was measured to make an unrelated D-Bus test fail
    that passes on its own, so a batch run reports interactions between tests
    as if they were this gate's finding. One file at a time costs wall-clock
    and buys an unambiguous attribution.
    """
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q",
         "--tb=no", *target],
        cwd=site, capture_output=True, text=True, timeout=600,
        env={"PATH": "/usr/bin:/bin", "HOME": str(site.parent),
             "PYTHONDONTWRITEBYTECODE": "1"},
    )


@pytest.mark.skipif(not RECIPE.is_file(), reason="packaging tree not in this checkout")
class TestShippedTestsSurviveInstalledLayout:

    def test_the_recipe_still_ships_the_top_level_tests(self):
        """The gate's premise, asserted rather than assumed."""
        assert _recipe_ships_top_level_tests(), (
            "the recipe no longer installs intergen/tests/*.py; this gate's "
            "premise changed and it must be re-derived, not relaxed"
        )

    def test_no_shipped_test_fails_for_want_of_the_repository(self, tmp_path):
        selected = _shipped_repo_dependent_tests()
        assert selected, (
            "no shipped test mentions the packaging tree — either the pattern "
            "stopped matching or the tests moved; re-derive this gate"
        )
        site = _build_installed_layout(tmp_path / "no-repo", with_repo=False)
        assert not (site / "packages").exists()
        assert not (site.parent / "packages").exists()

        # Leg A: no packaging tree. Anything that fails here is a CANDIDATE
        # only — a test can fail in a bare temporary root for reasons that have
        # nothing to do with the repository (one shipped test starts a real
        # daemon and needs a working environment). Attributing those to this
        # gate's property would be a false finding, so each candidate is
        # re-run in leg B, which differs by the packaging tree and NOTHING
        # else. A failure in both legs is somebody else's finding, not this
        # gate's, and is reported as such rather than counted.
        candidates = []
        for path in selected:
            target = f"intergen/tests/{path.name}"
            completed = _run_in_layout(site, target)
            if completed.returncode != 0:
                node_ids = _failed_node_ids(completed.stdout) or [target]
                candidates.append((target, node_ids, completed.stdout,
                                   completed.stderr))

        offenders, fail_in_both = [], []
        if candidates:
            site_b = _build_installed_layout(tmp_path / "with-repo", with_repo=True)
            assert (site_b / "packages").is_dir()
            for target, node_ids, out_a, err_a in candidates:
                # Only the failing tests are re-run, not their whole file.
                completed_b = _run_in_layout(site_b, *node_ids)
                if completed_b.returncode == 0:
                    offenders.append((target, out_a, err_a))
                else:
                    fail_in_both.append(target)

        if fail_in_both:
            print(
                "NOT this gate's finding — failed with AND without the "
                f"packaging tree, so the cause is elsewhere: {fail_in_both}"
            )

        assert not offenders, (
            "shipped test file(s) failed in an installed layout, where the "
            "packaging tree is absent by design. Skip on the absence, or move "
            "the check to tests/preflight/ where the repository is present.\n"
            + "\n".join(
                f"=== {t} ===\n--- stdout ---\n{o}\n--- stderr ---\n{e}"
                for t, o, e in offenders
            )
        )

    def test_the_simulation_detects_a_true_offender(self, tmp_path):
        """Positive control: prove the harness can FAIL before trusting a pass.

        A green run above means nothing unless this instrument is shown to
        report a real offender as a failure. The control plants a test with
        exactly the shape being banned — a hard assertion on a repository path —
        and requires the harness to come back non-zero.
        """
        site = _build_installed_layout(tmp_path / "sim", with_repo=False)
        offender = site / "intergen" / "tests" / "test_planted_control.py"
        offender.write_text(
            "from pathlib import Path\n"
            "def test_control():\n"
            "    root = Path(__file__).resolve().parents[2]\n"
            "    assert (root / 'packages' / 'ai' / 'intergen' / 'build.sh').is_file()\n",
            encoding="utf-8",
        )
        completed = _run_in_layout(site, "intergen/tests/test_planted_control.py")
        assert completed.returncode != 0, (
            "the installed-layout harness passed a test that hard-asserts a "
            "repository path; the harness is not measuring what it claims.\n"
            f"--- stdout ---\n{completed.stdout}"
        )
