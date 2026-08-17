# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Tests for governance tamper-detection hash verification.

The baseline at GOVERNANCE_HASH_PATH is build-established (shipped read-only by
the intergen package). verify_hash() compares only — it never writes a baseline
(no trust-on-first-use). A missing OR mismatched baseline fails CLOSED.
"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class TestGovernanceHashVerify(unittest.TestCase):
    def _engine(self):
        from intergen.governance import GovernanceEngine
        return GovernanceEngine()

    def _good_hash(self):
        import intergen.governance as g
        return hashlib.sha256(Path(g.__file__).read_bytes()).hexdigest()

    def test_missing_baseline_fails_closed(self):
        import intergen.governance as g
        with mock.patch.object(
            g, "GOVERNANCE_HASH_PATH", Path("/nonexistent/governance.sha256")
        ):
            eng = self._engine()
            self.assertFalse(eng.verify_hash())
            self.assertFalse(eng.healthy)  # marked unhealthy on integrity fail

    def test_matching_baseline_verifies(self):
        import intergen.governance as g
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "governance.sha256"
            p.write_text(self._good_hash() + "\n")
            with mock.patch.object(g, "GOVERNANCE_HASH_PATH", p):
                eng = self._engine()
                self.assertTrue(eng.verify_hash())

    def test_mismatched_baseline_fails_closed(self):
        import intergen.governance as g
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "governance.sha256"
            p.write_text("deadbeef" * 8 + "\n")  # wrong hash
            with mock.patch.object(g, "GOVERNANCE_HASH_PATH", p):
                eng = self._engine()
                self.assertFalse(eng.verify_hash())
                self.assertFalse(eng.healthy)

    def test_no_tofu_write_on_missing(self):
        """A missing baseline must NOT be written by the daemon (no TOFU)."""
        import intergen.governance as g
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "governance.sha256"  # does not exist
            with mock.patch.object(g, "GOVERNANCE_HASH_PATH", p):
                eng = self._engine()
                eng.verify_hash()
                self.assertFalse(
                    p.exists(),
                    "verify_hash must not create a trust-on-first-use baseline",
                )


class TestTierPersistencePath(unittest.TestCase):
    """G3-15: the --user daemon must not crash persisting the autonomy tier to
    a read-only /etc, and must resolve a writable per-user location."""

    def test_resolve_root_uses_etc(self):
        import intergen.governance as g
        with mock.patch("os.geteuid", return_value=0):
            self.assertEqual(g._resolve_tier_config_path(), g.TIER_CONFIG_PATH)

    def test_resolve_nonroot_uses_xdg_state(self):
        import intergen.governance as g
        with mock.patch("os.geteuid", return_value=1000), \
             mock.patch.dict("os.environ", {"XDG_STATE_HOME": "/run/user/1000/state"}):
            p = g._resolve_tier_config_path()
            self.assertEqual(p, Path("/run/user/1000/state/intergen/governance.json"))

    def test_persist_round_trips_when_writable(self):
        import intergen.governance as g
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sub" / "governance.json"
            eng = g.GovernanceEngine(tier_config_path=path)
            ok = eng.set_tier(g.AutonomyTier.ADJUST, owner_confirmed=True)
            self.assertTrue(ok)
            self.assertTrue(path.exists())
            # A fresh engine on the same path loads the persisted tier back.
            eng2 = g.GovernanceEngine(tier_config_path=path)
            eng2.load_tier()
            self.assertEqual(eng2._autonomy_tier, g.AutonomyTier.ADJUST)

    def test_set_tier_does_not_crash_on_readonly_path(self):
        # The latent G3-15 crash: persisting to an unwritable location must NOT
        # propagate OSError out of the owner-confirmed set_tier(); the in-memory
        # tier still takes effect for the session.
        import intergen.governance as g
        eng = g.GovernanceEngine(
            tier_config_path=Path("/proc/cannot/write/governance.json"))
        ok = eng.set_tier(g.AutonomyTier.ADJUST, owner_confirmed=True)
        self.assertTrue(ok)
        self.assertEqual(eng._autonomy_tier, g.AutonomyTier.ADJUST)


if __name__ == "__main__":
    unittest.main()
