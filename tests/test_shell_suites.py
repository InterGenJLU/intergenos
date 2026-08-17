# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Runs the repo's shell test suites inside the Python suite.

WHY THIS EXISTS. The shell tests under tests/ were reached by nothing
automatic — not by pytest, not by the git hooks — so they ran only when
somebody remembered to run them. A test nobody runs is not evidence; it is a
file that looks like evidence. tests/build-logging/test_progress_and_stream.py
wired exactly one of them and said the rest were worth adopting "once somebody
has confirmed they pass". They have now been confirmed, one by one, and this
is that adoption.

FAIL-CLOSED IN THE DIRECTION THAT ACTUALLY FAILS. Suites are DISCOVERED from
the filesystem, so a shell test added tomorrow is picked up and run without
anyone editing this file. That makes "a new suite goes unrun" impossible by
construction — and it also means an "is everything accounted for?" assertion
computed from the same discovery would be VACUOUS. The first version of this
module had exactly that: it subtracted a set derived from discovery from
discovery itself, could never fail, and was caught only by a control that
dropped a new shell test into the tree and watched the suite stay green.

The failure mode that CAN happen is the opposite one, and it is the one this
module guards: DISCOVERY ITSELF SILENTLY STOPS MATCHING. Move a suite one
directory deeper, rename a folder, and the glob quietly returns fewer files
while every test still passes — the same class as a package set defined by a
filename glob silently missing four packages. So the suites known to exist are
pinned by name below, and discovery must keep finding all of them.

Each wired suite is asserted on two properties, the same pair the build-logging
wrapper established: it exits zero, and it emits no warning noise. A warning
coming out of an instrument hides the signal the instrument exists to carry.
"""

import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "tests"

# Suites deliberately NOT run by this module, each with the reason. Anything
# here is a claim that must stay true; the completeness test below rejects a
# name that no longer exists, so a stale entry cannot linger unnoticed.
NOT_WIRED_HERE = {
    "tests/build-logging/test_progress_and_stream.sh":
        "already wired, in more detail, by its own wrapper "
        "tests/build-logging/test_progress_and_stream.py — running it twice "
        "would double its cost for no added coverage.",
}

# The noise words an instrument must not emit. Kept identical to the
# build-logging wrapper's list so both hold the same bar.
NOISE = ("warning", "stray", "syntax error", "unbound variable")

# The noise check reads STDOUT ONLY, and that narrowing is deliberate.
#
# Correctness is carried by the exit code, which is checked unconditionally
# and covers every failure a suite can report. The noise check is a second,
# softer signal: an instrument that has started emitting warnings is
# degrading even while it passes.
#
# Applied to stderr as well, it stops being a property of the SUITE and
# becomes a property of whatever the suite happens to shell out to. These
# suites drive gpg, sign-release.sh and friends, all of which write
# informational chatter to stderr, and how chatty they are varies by host
# configuration. A host whose gpg warns about homedir permissions would fail
# a wired suite that had itself exited 0 — turning a local environment
# difference into a canonical-suite failure on someone else's machine.
#
# That is exactly the failure this module was returned for once already:
# wiring a suite in must not promote a host-specific condition into a suite
# failure. Suites write their own PASS/SKIP/FAIL reporting to stdout, so
# stdout is where a degrading instrument shows up, and stderr belongs to the
# tools they call.
NOISE_STREAM_IS_STDOUT_ONLY = True


def discover_shell_suites():
    """Every *.sh directly under a tests/ subdirectory, repo-relative."""
    return sorted(
        p.relative_to(REPO_ROOT).as_posix()
        for p in TESTS_DIR.glob("*/*.sh")
    )


def run_suite(rel_path):
    return subprocess.run(
        ["bash", str(REPO_ROOT / rel_path)],
        capture_output=True, text=True, timeout=600, cwd=str(REPO_ROOT),
    )


# The suites known to exist when this module was written, pinned BY NAME.
# This is the floor discovery must keep clearing. It is deliberately NOT
# derived from the glob — a list computed from the thing it is checking cannot
# check it. Adding a suite does not require touching this; discovery runs it
# either way. Removing or MOVING one does, and that is the point.
KNOWN_SUITES = (
    "tests/build-logging/test_progress_and_stream.sh",
    "tests/check-aspirational-stubs/run-tests.sh",
    "tests/check-public-content/run-tests.sh",
    "tests/kernel-retention/test_fallback_quarantine.sh",
    "tests/kernel-retention/test_prune_old_kernels.sh",
    "tests/kernel-retention/test_single_kernel_gate.sh",
    "tests/kernel-retention/test_update_boot_menu.sh",
    "tests/manifest/test_manifest_phase.sh",
    "tests/pi12/test_pi12_gates.sh",
    "tests/sbat/test_check_sbat_generations.sh",
)


class TestShellSuiteInventory(unittest.TestCase):
    def test_discovery_still_finds_every_known_suite(self):
        """A suite that moves out of the glob's reach must fail loudly.

        Without this, relocating tests/foo/x.sh to tests/foo/bar/x.sh drops it
        from every run while the suite stays green.
        """
        discovered = set(discover_shell_suites())
        missing = [s for s in KNOWN_SUITES if s not in discovered]
        self.assertEqual(
            missing, [],
            "discovery no longer finds shell suite(s) that are pinned in "
            "KNOWN_SUITES — either they moved (fix the glob or the pin) or "
            "they were deleted (drop the pin deliberately):\n  "
            + "\n  ".join(missing),
        )

    def test_no_dispositioned_entry_has_gone_stale(self):
        """A named exclusion must still exist on disk."""
        discovered = set(discover_shell_suites())
        for name in NOT_WIRED_HERE:
            self.assertIn(
                name, discovered,
                f"NOT_WIRED_HERE names {name}, which no longer exists — "
                "remove the entry rather than leaving a stale claim.",
            )

    def test_the_wired_set_is_not_empty(self):
        # Guards against a refactor that leaves the discovery returning
        # nothing and every other test in this module passing vacuously.
        self.assertTrue(WIRED, "no shell suites are wired — discovery is broken")


# Discovered at import so each suite becomes its own test method: a failure
# names the suite that failed instead of one opaque aggregate.
WIRED = [p for p in discover_shell_suites() if p not in NOT_WIRED_HERE]


def _make_test(rel_path):
    def test(self):
        proc = run_suite(rel_path)
        output = proc.stdout + proc.stderr
        # The correctness gate. Both streams are shown on failure, because a
        # suite reports its FAIL lines on stderr and the reader needs them.
        self.assertEqual(
            proc.returncode, 0,
            f"{rel_path} failed (rc={proc.returncode}):\n{output}",
        )
        # The degradation signal — stdout only. See NOISE_STREAM_IS_STDOUT_ONLY.
        lowered = proc.stdout.lower()
        for noise in NOISE:
            self.assertNotIn(
                noise, lowered,
                f"{rel_path} exited 0 but emitted '{noise}' on stdout:\n{proc.stdout}",
            )
    test.__name__ = "test_" + rel_path.replace("/", "_").replace(".", "_")
    test.__doc__ = (
        f"{rel_path} exits zero and emits no warning noise on stdout."
    )
    return test


class TestShellSuites(unittest.TestCase):
    pass


for _p in WIRED:
    _t = _make_test(_p)
    setattr(TestShellSuites, _t.__name__, _t)
del _p, _t


if __name__ == "__main__":
    unittest.main()
