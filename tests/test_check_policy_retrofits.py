# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Recipes whose check() masked every test failure with `|| true`.

WHY THIS EXISTS. `make check || true` accepts every failure the suite can
produce, including one that has never been seen before. The result is a build
log that says nothing and a package that claims a passing suite it never had —
the unverified-claim shape, and the reason the tree's own driver comment
already calls bare `|| true` in check() forbidden.

The replacement is per package, never a batch: each one either declares the
environmental reason its failures are expected (failure_policy: known_failures,
which prints a loud waiver naming that reason) or declares nothing and lets
the real status reach the driver, which logs it and records it in the trace.
Which of the two is right depends on the lane: on the Python-built tiers a
non-zero check phase FAILS the build, so a package with documented
environmental failures must carry the waiver or the build stops; on the
Chapter-8 and base drivers a check failure is informational by design.

Each test below drives the real pkg_run_tests against the real package.yml,
with the test command stubbed to fail — so it measures the policy that would
actually apply during a build, not a description of it.
"""

import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG_FUNCS = REPO_ROOT / "scripts" / "pkg-functions.sh"


def run_policy(pkg_rel: str, exit_code: int):
    """Run pkg_run_tests against a package.yml with a stub command."""
    yml = REPO_ROOT / "packages" / pkg_rel / "package.yml"
    script = textwrap.dedent(f"""
        set -e
        source "{PKG_FUNCS}"
        stub() {{ echo "stub test output"; return {exit_code}; }}
        set +e
        pkg_run_tests "{yml}" stub
        echo "POLICY_RC=$?"
    """)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def build_sh(pkg_rel: str) -> str:
    return (REPO_ROOT / "packages" / pkg_rel / "build.sh").read_text()


def check_body(pkg_rel: str) -> str:
    """Just the check() function. Scoped deliberately: other hooks in these
    recipes carry their own masked calls (`systemctl enable ... || true` is a
    corpus-wide idiom in post_install), and those are a separate question
    from the test-suite policy this module is about."""
    text = build_sh(pkg_rel)
    start = text.index("check()")
    end = text.index("\n}\n", start)
    return text[start:end]


class TestCups:
    """desktop/cups — Python lane: a non-zero check phase fails the build, so
    the documented environmental failures must be declared."""

    PKG = "desktop/cups"

    def test_the_blanket_mask_is_gone(self):
        assert "|| true" not in check_body(self.PKG)

    def test_check_routes_through_the_policy_wrapper(self):
        assert "pkg_run_tests" in check_body(self.PKG)

    def test_a_failing_suite_is_waived_loudly_not_silently(self):
        r = run_policy(self.PKG, 1)
        assert "POLICY_RC=0" in r.stdout, r.stdout + r.stderr
        assert "allowed by failure_policy=known_failures" in r.stdout
        assert "graphical session" in r.stdout, "the reason must reach the log"

    def test_a_passing_suite_still_reads_as_passing(self):
        r = run_policy(self.PKG, 0)
        assert "POLICY_RC=0" in r.stdout
        assert "PASSED" in r.stdout

    def test_the_suite_output_is_not_discarded(self):
        r = run_policy(self.PKG, 1)
        assert "stub test output" in r.stdout
