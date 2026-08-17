#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression: `sync` is the canonical index-refresh subcommand.

The wiki and the assistant both teach `pkm sync`; pkm's own user-facing strings
must match, or a user sees two names for one operation (pkm's own error said
`sudo pkm update` while guidance said `pkm sync`).

Fix: the parser primary is `sync`, with `update`/`refresh` kept as
backward-compatible aliases that normalize to the unchanged internal handler
(no dispatch/behavior change), and every user-facing index-refresh hint says
`pkm sync`. Source-level guard so a future edit cannot silently reintroduce the
divergence.
"""

import unittest
from pathlib import Path


class SyncCanonTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[2] / "pkm"
        cls.cli = (root / "cli.py").read_text()
        cls.repo = (root / "repo.py").read_text()
        cls.installer = (root / "installer.py").read_text()

    def test_sync_is_the_primary_subcommand(self):
        # `sync` registered as primary; `update`/`refresh` as aliases.
        self.assertRegex(
            self.cli,
            r'add_parser\(\s*"sync"\s*,\s*aliases=\[\s*"update"\s*,\s*"refresh"\s*\]',
        )
        # The old primary registration (update-primary) must be gone.
        self.assertNotRegex(self.cli, r'add_parser\(\s*"update"\s*,\s*aliases=')

    def test_aliases_still_normalize_to_the_internal_handler(self):
        # Backward compatibility: `update`/`refresh` resolve to the same internal
        # handler, so only the canonical name users SEE changes.
        self.assertIn('"sync": "update"', self.cli)
        self.assertIn('"refresh": "update"', self.cli)

    def test_user_facing_hints_use_pkm_sync(self):
        # The corrected canonical hints are present.
        self.assertIn("Run 'sudo pkm sync' to download", self.cli)
        self.assertIn("`pkm sync`, or check the package name", self.cli)
        self.assertIn("Run `pkm sync` first", self.cli)
        self.assertIn("`pkm sync` to refresh it", self.repo)
        self.assertIn("Run `pkm sync` then retry", self.installer)

    def test_old_user_facing_update_hints_are_gone(self):
        # The specific prior user-facing hint strings must not remain.
        self.assertNotIn("sudo pkm update' to download", self.cli)
        self.assertNotIn("`pkm update`, or check the package name", self.cli)
        self.assertNotIn("Run `pkm update` first", self.cli)
        self.assertNotIn("`pkm update` to refresh it", self.repo)
        self.assertNotIn("Run `pkm update` then retry", self.installer)


if __name__ == "__main__":
    unittest.main()
