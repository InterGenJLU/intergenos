# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Model-resolution downloaded walk-down (resolve_for_detected).

The belt under detection, mirroring dispatch's shipped-lane walk-down: a
recommended model that is NOT on disk resolves to the largest DOWNLOADED
smaller-tier model instead of dead-ending at "No model downloaded" (the
detected-Tier-3, 9B-only box class). When nothing smaller is downloaded the
recommendation is returned unchanged — the same loud dead-end as before,
never a silent substitution; and a downloaded recommendation is NEVER
overridden.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from intergen.interfaces.types import HardwareTierLevel
from intergen.model_manager import MODEL_CATALOG, ModelManager

FILE_9B = MODEL_CATALOG[HardwareTierLevel.TIER_2].filename
NAME_9B = MODEL_CATALOG[HardwareTierLevel.TIER_2].name
FILE_35B = MODEL_CATALOG[HardwareTierLevel.TIER_3].filename
NAME_35B = MODEL_CATALOG[HardwareTierLevel.TIER_3].name
NAME_2B = MODEL_CATALOG[HardwareTierLevel.TIER_1].name


def _tier3() -> SimpleNamespace:
    return SimpleNamespace(tier=HardwareTierLevel.TIER_3,
                           recommended_model=NAME_35B)


class WalkDownTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Path(self._tmp.name) / "llm"
        self.store.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _manager(self) -> ModelManager:
        mm = ModelManager(
            model_dir=self.store,
            manifest_path=self.store / "manifest.json",
        )
        # Hermetic pin state (never the box's installed manifest): every
        # catalog tier pinned, so walk-down behavior alone is under test.
        mm._pins = {
            MODEL_CATALOG[level].filename: "a" * 64
            for level in MODEL_CATALOG
        }
        return mm

    def test_absent_recommendation_walks_down_to_downloaded_9b(self):
        (self.store / FILE_9B).write_bytes(b"stub")
        resolved = self._manager().resolve_for_detected(_tier3())
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.name, NAME_9B)
        self.assertTrue(resolved.downloaded)

    def test_nothing_downloaded_keeps_the_loud_dead_end(self):
        resolved = self._manager().resolve_for_detected(_tier3())
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.name, NAME_35B)
        self.assertFalse(resolved.downloaded)

    def test_downloaded_recommendation_is_never_overridden(self):
        (self.store / FILE_35B).write_bytes(b"stub")
        (self.store / FILE_9B).write_bytes(b"stub")
        resolved = self._manager().resolve_for_detected(_tier3())
        self.assertEqual(resolved.name, NAME_35B)
        self.assertTrue(resolved.downloaded)


if __name__ == "__main__":
    unittest.main()
