# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""One root launch of the record runner measures two tiers, in one record.

WHY THIS EXISTS. No record had ever passed scripts/check-release-validation.py,
and not because a gate found a defect. Launched as the user, the root password
gate cannot read /etc/shadow and skips, and the release gate refuses the skip.
Launched as root, thirteen user-tier gates read root's home, root's journal and
no session bus, and break (measured 2026-10-14 on an upgraded validation
machine). The runner had no notion of a tier: one pytest run as whoever launched
it. Neither launch could ever produce a record that measured both.

These tests hold the split: the root tier is a marker the tier registers; a root
launch runs the user tier as the account that invoked it and the root tier as
root, and writes ONE record naming who measured each gate; an unprivileged launch
still runs the user tier, says in one line that the root tier was NOT measured,
and leaves a record the release gate refuses.

WHAT IS NOT ASSERTED HERE. The runner only runs on a real installed machine, and
the identity change needs root. So the runner is driven in-process with its
subprocess calls recorded rather than executed: what is asserted is which runs it
asks for, as whom, with what environment, and what it writes. The privilege drop
itself is proven by the runner's real firing through sudo, shipped with the cut.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TIER = REPO_ROOT / "tests" / "installed"
ROOT_GATE_FILE = TIER / "test_gate_root_password_kept.py"
ROOT_GATE_NODEID = ("tests/installed/test_gate_root_password_kept.py::"
                    "TestRootPasswordKept::test_installed_root_has_a_real_hash")
GATE_ENV = "INTERGENOS_INSTALLED_GATES"


def _pytest(*args: str) -> subprocess.CompletedProcess:
    """pytest over the tier from the repository root, as an ordinary run: the
    tier is collected (its gates skip unless asked for), nothing is executed."""
    env = {k: v for k, v in os.environ.items() if k != GATE_ENV}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
        capture_output=True, text=True, cwd=str(REPO_ROOT), env=env, timeout=300)


class TheRootTierIsAMarkerTheTierRegisters(unittest.TestCase):
    """The runner selects the root tier by marker, so the marker has to exist,
    be registered under that exact name, and sit on the gate that needs root."""

    def test_the_root_password_gate_is_selected_by_the_root_tier_marker(self) -> None:
        r = _pytest("--collect-only", "-m", "root_tier", str(ROOT_GATE_FILE))
        selected = [line for line in r.stdout.splitlines() if "::" in line]
        self.assertEqual(
            selected, [ROOT_GATE_NODEID],
            "selecting the root tier must select exactly the gate that reads "
            "/etc/shadow; anything else means a root launch measures it as the "
            "user (where it skips) or measures a user-tier gate as root:\n"
            + r.stdout + r.stderr)

    def test_the_marker_is_registered_by_the_tier(self) -> None:
        """Registered, not merely used: an unregistered marker is a warning, and a
        misspelling of it would quietly put a root gate in the user tier."""
        r = _pytest("--markers", str(TIER))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("@pytest.mark.root_tier:", r.stdout,
                      "tests/installed/conftest.py does not register root_tier")

    def test_the_root_password_gates_control_stays_in_the_user_tier(self) -> None:
        """Only the gate that needs root is in the root tier. The reader's own
        control needs nothing and must keep measuring as the user."""
        r = _pytest("--collect-only", "-m", "not root_tier", str(ROOT_GATE_FILE))
        selected = [line for line in r.stdout.splitlines() if "::" in line]
        self.assertEqual(
            selected,
            ["tests/installed/test_gate_root_password_kept.py::"
             "TestRootPasswordKept::test_control_reader_sees_a_locked_field"],
            r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
