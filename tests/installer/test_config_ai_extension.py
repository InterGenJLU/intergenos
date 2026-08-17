# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Forge opt-in AI panel extension enablement — gschema list parse/merge."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from installer.backend import config  # noqa: E402

UUID = config.AI_PANEL_EXTENSION_UUID
SRC = ("[org.gnome.shell]\n"
       "enabled-extensions=['a@x', 'b@y', 'pkm-notifier@intergenos.org']\n")


class TestAugmentEnabledExtensions(unittest.TestCase):
    def test_appends_uuid_preserving_originals(self):
        out = config._augment_enabled_extensions_override(SRC, UUID)
        self.assertIsNotNone(out)
        self.assertTrue(out.startswith("[org.gnome.shell]"))
        import ast
        new = ast.literal_eval(out.splitlines()[1].split("=", 1)[1].strip())
        self.assertEqual(new, ['a@x', 'b@y', 'pkm-notifier@intergenos.org', UUID])

    def test_idempotent_when_already_present(self):
        out = config._augment_enabled_extensions_override(SRC, UUID)
        # feeding the augmented text back in -> nothing to do
        self.assertIsNone(config._augment_enabled_extensions_override(out, UUID))

    def test_missing_line_returns_none(self):
        self.assertIsNone(
            config._augment_enabled_extensions_override("[org.gnome.shell]\n", UUID))

    def test_malformed_value_returns_none(self):
        self.assertIsNone(config._augment_enabled_extensions_override(
            "enabled-extensions=not-a-list\n", UUID))

    def test_matches_real_shipped_override(self):
        override = (Path(__file__).resolve().parents[2]
                    / "config/gsettings/91_intergenos-extensions.gschema.override")
        if not override.exists():
            self.skipTest("shipped override not present")
        out = config._augment_enabled_extensions_override(override.read_text(), UUID)
        self.assertIsNotNone(out)
        self.assertIn(UUID, out)


if __name__ == "__main__":
    unittest.main()
