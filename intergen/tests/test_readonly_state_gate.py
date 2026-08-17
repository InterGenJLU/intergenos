# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""M4(i) read-only-state requires[] on-PATH gate — regression pins.

_natural_language_to_command maps a natural system query to a read-only shell
command, but only DIRECT-EXECs it when every binary the command invokes is
installed (grounded in data/readonly-state-map.json). Dispatching a command whose
tool the box lacks would both fire a guaranteed failure AND imply a capability the
machine does not have (security-first: no unverified capability claim). When a
required binary is missing the turn falls through to the freeform path (returns
None) and a glass row records the suppression. When the map itself is
missing/unreadable the gate is LOUD-degraded (WARN + glass) to per-segment
leading-binary checks — never a silent trust-nothing no-op.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import intergen.glass as glass
import intergen.router as router
from intergen.router import ConversationRouter as R


def _glass_reset(tmp: str) -> None:
    os.environ["XDG_STATE_HOME"] = tmp
    os.environ.pop("INTERGEN_GLASS", None)
    glass._glass = None


def _glass_rows(tmp: str) -> list[dict]:
    p = Path(tmp) / "intergen" / "glass.jsonl"
    if not p.exists():
        return []
    with open(p) as f:
        return [json.loads(x) for x in f]


def _which_all_present(name: str) -> str | None:
    return f"/usr/bin/{name}"


def _which_missing(*missing: str):
    miss = set(missing)
    return lambda name: None if name in miss else f"/usr/bin/{name}"


class AllBinariesPresent(unittest.TestCase):
    """The mapping resolves, every tool on PATH → direct-exec command returned."""

    def setUp(self) -> None:
        # Real shipped map; drop the lru_cache so the real artifact loads.
        router._readonly_state_requires.cache_clear()

    def tearDown(self) -> None:
        router._readonly_state_requires.cache_clear()

    def test_disk_ram_cpu_map_to_readonly_commands(self) -> None:
        with mock.patch.object(router.shutil, "which", _which_all_present):
            self.assertEqual(
                R._natural_language_to_command("how much disk space is left"),
                "df -h")
            self.assertEqual(
                R._natural_language_to_command("how much ram do i have"),
                "free -h")
            self.assertEqual(
                R._natural_language_to_command("what cpu do i have"),
                "lscpu | head -20")


class RequiredBinaryMissing(unittest.TestCase):
    """A missing required binary suppresses direct-exec (None) + logs a glass row."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        _glass_reset(self.tmp)
        router._readonly_state_requires.cache_clear()

    def tearDown(self) -> None:
        router._readonly_state_requires.cache_clear()

    def test_missing_lpstat_suppresses_printer_query(self) -> None:
        with mock.patch.object(router.shutil, "which", _which_missing("lpstat")):
            with glass.turn(glass.new_turn_id(), "test"):
                out = R._natural_language_to_command("what printers are set up")
        self.assertIsNone(out)
        gate = [x for x in _glass_rows(self.tmp)
                if x.get("event") == "readonly_state_gate"]
        self.assertTrue(gate, "a suppression must emit a readonly_state_gate row")
        self.assertEqual(gate[-1]["detail"]["verdict"], "suppressed_missing_binary")
        self.assertEqual(gate[-1]["detail"]["missing"], ["lpstat"])


class MapMissingDegradesLoud(unittest.TestCase):
    """A missing/unreadable readonly-state-map is LOUD (WARN + glass) and degrades
    to leading-binary checks — still gates, never a silent no-op."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        _glass_reset(self.tmp)
        self._orig = router._READONLY_STATE_MAP_PATH
        router._READONLY_STATE_MAP_PATH = (
            Path(tempfile.gettempdir()) / "no-such-readonly-state-map.json")
        router._readonly_state_requires.cache_clear()

    def tearDown(self) -> None:
        router._READONLY_STATE_MAP_PATH = self._orig
        router._readonly_state_requires.cache_clear()

    def test_map_absent_warns_glasses_and_still_gates_via_leading_binary(self) -> None:
        with mock.patch.object(router.shutil, "which", _which_all_present):
            with glass.turn(glass.new_turn_id(), "test"):
                with self.assertLogs("intergen.router", level="WARNING") as logs:
                    out = R._natural_language_to_command(
                        "how much disk space is left")
        # Leading-binary fallback: df present → command still returned.
        self.assertEqual(out, "df -h")
        self.assertTrue(any("readonly-state-map.json" in m for m in logs.output))
        rows = [x for x in _glass_rows(self.tmp)
                if x.get("event") == "readonly_state_map"]
        self.assertTrue(rows, "map-absent must emit a readonly_state_map row")
        self.assertEqual(rows[-1]["detail"]["verdict"], "unavailable_no_map")

    def test_map_absent_still_suppresses_a_genuinely_missing_binary(self) -> None:
        # Degraded ≠ trust-nothing: with the map gone, a missing leading binary is
        # still suppressed (leading-binary fallback == the required check today).
        with mock.patch.object(router.shutil, "which", _which_missing("lpstat")):
            with glass.turn(glass.new_turn_id(), "test"):
                out = R._natural_language_to_command("what printers are set up")
        self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
