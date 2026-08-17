# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Unit tests for intergen.panel._resolve_mode — the window-mode resolver.

_resolve_mode decides which window the panel opens:
  - BASIC (decorated, native min/max/close) is the DEFAULT.
  - DOCK (frameless magnetic dock) is opt-in via --dock.

Precedence: CLI flag (--basic/--dock) wins over the ~/.config/intergen/panel.json
`mode` key, which wins over the basic default. Any value that is neither "basic"
nor "dock" — a garbage config value, a missing/unreadable file, or malformed
JSON — falls back to basic rather than erroring (a usable window beats a hard
failure). These tests pin each of those branches.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from intergen.panel import _resolve_mode


class ResolveModeTests(unittest.TestCase):
    """Cover flag precedence, config fallback, and garbage-value safety.

    Every case patches Path.home() to a throwaway directory so resolution never
    depends on (or mutates) the real ~/.config/intergen/panel.json on the host.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        patcher = mock.patch.object(Path, "home", return_value=self.home)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def _write_prefs(self, content: str) -> None:
        """Write panel.json under the fake home (raw string, so malformed JSON
        and non-dict shapes can be exercised too)."""
        cfg = self.home / ".config" / "intergen"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "panel.json").write_text(content)

    # -- default ----------------------------------------------------------
    def test_default_is_basic(self):
        # No flag, no config file at all -> basic.
        self.assertEqual(_resolve_mode([]), "basic")

    # -- explicit flags ---------------------------------------------------
    def test_basic_flag(self):
        self.assertEqual(_resolve_mode(["--basic"]), "basic")

    def test_dock_flag(self):
        self.assertEqual(_resolve_mode(["--dock"]), "dock")

    # -- config-key fallback ----------------------------------------------
    def test_config_dock_selects_dock(self):
        self._write_prefs(json.dumps({"mode": "dock"}))
        self.assertEqual(_resolve_mode([]), "dock")

    def test_config_basic_selects_basic(self):
        self._write_prefs(json.dumps({"mode": "basic"}))
        self.assertEqual(_resolve_mode([]), "basic")

    # -- flag-over-config precedence --------------------------------------
    def test_basic_flag_overrides_dock_config(self):
        self._write_prefs(json.dumps({"mode": "dock"}))
        self.assertEqual(_resolve_mode(["--basic"]), "basic")

    def test_dock_flag_overrides_basic_config(self):
        self._write_prefs(json.dumps({"mode": "basic"}))
        self.assertEqual(_resolve_mode(["--dock"]), "dock")

    # -- garbage-value safety ---------------------------------------------
    def test_unknown_config_value_falls_back_to_basic(self):
        self._write_prefs(json.dumps({"mode": "frameless"}))
        self.assertEqual(_resolve_mode([]), "basic")

    def test_missing_mode_key_falls_back_to_basic(self):
        self._write_prefs(json.dumps({"theme": "dark"}))
        self.assertEqual(_resolve_mode([]), "basic")

    def test_malformed_json_falls_back_to_basic(self):
        self._write_prefs("{ this is not valid json")
        self.assertEqual(_resolve_mode([]), "basic")

    def test_missing_file_falls_back_to_basic(self):
        # Fake home exists but no panel.json under it.
        self.assertEqual(_resolve_mode([]), "basic")


if __name__ == "__main__":
    unittest.main()
