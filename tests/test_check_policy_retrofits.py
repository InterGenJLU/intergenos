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


def check_invocation(pkg_rel: str) -> str:
    """The pkg_run_tests call in check(), comments stripped.

    Scoped deliberately: a recipe comment may legitimately name a command it
    NO LONGER runs, in order to record why it was replaced. Asserting on the
    whole body would read that explanation as the invocation."""
    lines = []
    for line in check_body(pkg_rel).split("\n"):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines)


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
        assert "refuses to run as root" in r.stdout, "the reason must reach the log"

    def test_the_reason_names_the_cause_the_build_log_shows(self):
        """The reason used to blame a session bus, a display and address-in-use
        conflicts. The build log shows none of those: the interface tests run
        and the scheduler test plan refuses to run as root. A waiver that names
        the wrong cause is the unverified claim this module exists to catch.

        The reason still mentions the retired explanation, deliberately, to
        record that it was replaced — so this asserts which cause is stated as
        the operative one rather than which words appear."""
        r = run_policy(self.PKG, 1)
        assert "run-stp-tests.sh" in r.stdout
        assert "the measured cause is the root refusal" in r.stdout
        assert "The unit tests do run" in r.stdout

    def test_the_reason_names_the_consequence_of_waiving_the_whole_target(self):
        """known_failures here covers make check entirely, so a new unit-test
        failure is absorbed too. That is a real limit and the reason states it."""
        r = run_policy(self.PKG, 1)
        assert "would also be absorbed" in r.stdout

    def test_a_passing_suite_still_reads_as_passing(self):
        r = run_policy(self.PKG, 0)
        assert "POLICY_RC=0" in r.stdout
        assert "PASSED" in r.stdout

    def test_the_suite_output_is_not_discarded(self):
        r = run_policy(self.PKG, 1)
        assert "stub test output" in r.stdout


class TestSamba:
    """desktop/samba — the suite is declared NOT RUN, because it cannot run
    against a build configured for shipping.

    quicktest calls waf test, which refuses unless the build was configured
    with --enable-selftest. That flag is a compile-time define consumed by
    production code, not test code: it puts an on-demand sleep-message handler
    inside smbd and lets an environment variable replace the DNS resolver's
    configuration path. Turning it on to make a suite runnable would change the
    shipped daemon, so it stays off and the suite stays unrun. The earlier
    policy here declared known_failures and described a suite that ran; it
    never ran, and these tests pin the corrected declaration."""

    PKG = "desktop/samba"

    def test_the_blanket_mask_is_gone(self):
        assert "|| true" not in check_body(self.PKG)

    def test_check_routes_through_the_policy_wrapper(self):
        assert "pkg_run_tests" in check_body(self.PKG)

    def test_the_suite_is_declared_not_run(self):
        """Measured through pkg_run_tests' own parser: the file could say one
        thing and the parser read another."""
        r = run_policy(self.PKG, 1)
        assert "phase skipped (enabled=false)" in r.stdout, r.stdout + r.stderr
        assert "POLICY_RC=0" in r.stdout

    def test_the_declaration_does_not_claim_a_waived_failure(self):
        """A suite that never started must not be reported as a suite whose
        failures were expected — that is the claim the correction removed."""
        r = run_policy(self.PKG, 1)
        assert "allowed by failure_policy=known_failures" not in r.stdout
        assert "self-test environment" not in r.stdout

    def test_the_reason_names_why_the_flag_is_not_passed(self):
        """The reason has to carry the shipped-binary consequence, or a later
        reader sees only a missing configure flag and adds it."""
        r = run_policy(self.PKG, 1)
        assert "--enable-selftest" in r.stdout
        assert "MSG_SMB_SLEEP" in r.stdout
        assert "RESOLV_CONF" in r.stdout

    def test_the_command_stays_recorded(self):
        """Not running the suite is a decision, not an erasure: the invocation
        remains in check() as the record of what would run."""
        assert "make quicktest" in check_body(self.PKG)


class TestSpidermonkey:
    """desktop/spidermonkey — Python lane. The failures follow from the
    deliberate --with-system-icu choice, so they are declared, not masked."""

    PKG = "desktop/spidermonkey"

    def test_the_blanket_mask_is_gone(self):
        assert "|| true" not in check_body(self.PKG)

    def test_check_routes_through_the_policy_wrapper(self):
        assert "pkg_run_tests" in check_body(self.PKG)

    def test_the_suite_arguments_are_unchanged(self):
        body = check_body(self.PKG)
        assert "check-jstests" in body
        assert '--timeout 300 --wpt=disabled' in body

    def test_a_failing_suite_is_waived_loudly_not_silently(self):
        r = run_policy(self.PKG, 1)
        assert "POLICY_RC=0" in r.stdout, r.stdout + r.stderr
        assert "allowed by failure_policy=known_failures" in r.stdout
        assert "system-icu" in r.stdout

    def test_a_passing_suite_still_reads_as_passing(self):
        r = run_policy(self.PKG, 0)
        assert "POLICY_RC=0" in r.stdout
        assert "PASSED" in r.stdout


class TestNodejs:
    """core/nodejs — Chapter-8 lane, where a check failure is informational by
    design. The waiver is still declared, because "informational" is a
    property of the driver and the reason belongs with the package."""

    PKG = "core/nodejs"

    def test_the_blanket_mask_is_gone(self):
        assert "|| true" not in check_body(self.PKG)

    def test_check_routes_through_the_policy_wrapper(self):
        assert "pkg_run_tests" in check_body(self.PKG)

    def test_the_stale_failure_count_is_gone(self):
        """The retired comment claimed ~10 of 4600+; the book says 3 of over
        4400. A number in a recipe comment is a claim like any other."""
        assert "~10 of 4600" not in build_sh(self.PKG)

    def test_the_invocation_is_not_the_one_that_never_reached_a_test(self):
        """`make test-only` runs build-addons first, whose stamp chain ends in
        an npm fetch for documentation tooling. The build chroot is offline, so
        make aborted before a single test ran while the recipe declared a
        policy for the failures of a suite that had not started."""
        assert "make test-only" not in check_invocation(self.PKG)

    def test_the_suite_runs_the_offline_set_upstream_designates(self):
        """tools/test.py expands `default` to every suite except the ones
        upstream excludes for needing a network or addon compilation."""
        invocation = check_invocation(self.PKG)
        assert "tools/test.py" in invocation
        assert "default" in invocation

    def test_a_failing_suite_is_waived_loudly_not_silently(self):
        r = run_policy(self.PKG, 1)
        assert "POLICY_RC=0" in r.stdout, r.stdout + r.stderr
        assert "allowed by failure_policy=known_failures" in r.stdout
        assert "parallel suite" in r.stdout

    def test_a_passing_suite_still_reads_as_passing(self):
        r = run_policy(self.PKG, 0)
        assert "POLICY_RC=0" in r.stdout
        assert "PASSED" in r.stdout


class TestMitkrb:
    """core/mitkrb — the one that does NOT get a waiver.

    The trace audit of the first release recorded an uncharacterized
    segmentation fault in this suite. A known-failures waiver would cover
    that crash along with the documented environmental cases, which is the
    same unverified claim the mask was. Strict reports the real status; the
    Chapter-8 driver logs and traces it and the build continues, because a
    check failure is informational on that lane by design.
    """

    PKG = "core/mitkrb"

    def test_the_blanket_mask_is_gone(self):
        assert "|| true" not in check_body(self.PKG)

    def test_check_routes_through_the_policy_wrapper(self):
        assert "pkg_run_tests" in check_body(self.PKG)

    def test_the_policy_the_wrapper_actually_reads_is_strict(self):
        """Measured through pkg_run_tests' own parser, not by reading the
        file — the file could say one thing and the parser see another."""
        r = run_policy(self.PKG, 0)
        assert "policy=strict" in r.stdout, r.stdout

    def test_a_failing_suite_is_reported_not_waived(self):
        r = run_policy(self.PKG, 1)
        assert "POLICY_RC=1" in r.stdout, r.stdout + r.stderr
        assert "allowed by failure_policy" not in r.stdout
        assert "FAILED" in r.stdout + r.stderr

    def test_a_passing_suite_still_reads_as_passing(self):
        r = run_policy(self.PKG, 0)
        assert "POLICY_RC=0" in r.stdout
        assert "PASSED" in r.stdout

    def test_the_suite_output_is_not_discarded(self):
        r = run_policy(self.PKG, 1)
        assert "stub test output" in r.stdout
