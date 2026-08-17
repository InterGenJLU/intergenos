# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Every MODEL_CATALOG filename must have a row in models-manifest.json.

The defect class this pins, driven against the shipped code before writing
it: the tier-2 payload is selected by MODEL_CATALOG but PINNED by
intergen/data/models-manifest.json, and the overlay is filename-keyed — the
two must move in lockstep. The failure is asymmetric: a catalog left on an
old filename fails closed (the pin contract locks the tier out, loud), but a
MANIFEST left on an old filename makes the tier CAP DOWN silently — the
daemon logs the miss and serves the next tier's model, which a user never
sees. This test makes the quiet direction loud at suite time: a filename the
catalog serves without a manifest row fails here, before any box ever caps
down.

Both files ship; this reads the same shipped pair the daemon reads.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from intergen.model_manager import MODEL_CATALOG

_MANIFEST = Path(__file__).resolve().parents[1] / "data" / "models-manifest.json"


class CatalogManifestLockstepTests(unittest.TestCase):

    def _entries_by_filename(self) -> dict[str, dict]:
        manifest = json.loads(_MANIFEST.read_text())
        return {e["filename"]: e for e in manifest["entries"]
                if isinstance(e, dict) and e.get("filename")}

    def test_every_catalog_filename_has_a_manifest_row(self):
        entries = self._entries_by_filename()
        for tier, model in MODEL_CATALOG.items():
            with self.subTest(tier=tier.name, filename=model.filename):
                self.assertIn(
                    model.filename, entries,
                    f"{tier.name} serves {model.filename!r} but "
                    f"models-manifest.json has no row for it — that tier "
                    f"would silently cap down at provisioning time")

    def test_every_catalog_projector_matches_its_entry(self):
        """The projector pin rides INSIDE its model's entry; a catalog
        projector name absent from that entry is the same quiet hazard."""
        entries = self._entries_by_filename()
        for tier, model in MODEL_CATALOG.items():
            mmproj = getattr(model, "mmproj_filename", None)
            if not mmproj:
                continue
            entry = entries.get(model.filename)
            if entry is None:
                continue  # already reported by the filename test above
            with self.subTest(tier=tier.name, mmproj=mmproj):
                self.assertEqual(
                    mmproj, entry.get("mmproj_filename"),
                    f"{tier.name}'s catalog projector and its manifest "
                    f"entry disagree")


if __name__ == "__main__":
    unittest.main()
