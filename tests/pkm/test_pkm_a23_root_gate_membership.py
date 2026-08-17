#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""PKM-A23 regression: the root-gate command sets are EXHAUSTIVE.

PKM_MUTATING_COMMANDS drives the clean "must be run as root" advisory + the
flock; PKM_READONLY_COMMANDS opens the DB read-only so a non-root user can
inspect their own system. A command in NEITHER set got the default read-WRITE
open and no advisory, so a non-root run blew up with a raw PermissionError /
"attempt to write a readonly database" far downstream instead of the clean
early gate. refresh-baseline, iso-prep, verify and check-updates had fallen
through that gap.

Fixed by categorizing them. This test asserts the invariant holds for EVERY
dispatch command (extracted from the source), so a future command added without
categorization fails here rather than shipping the opaque-error regression.
"""

import pathlib
import re
import unittest

from pkm.cli import PKM_MUTATING_COMMANDS, PKM_READONLY_COMMANDS

CLI_SRC = pathlib.Path(__file__).resolve().parents[2] / "pkm" / "cli.py"


class RootGateMembershipTest(unittest.TestCase):

    def _dispatch_commands(self):
        # The dispatch table maps "<command>": cmd_<func>. Extract every key.
        src = CLI_SRC.read_text()
        return set(re.findall(r'"([a-z][a-z0-9-]*)":\s*cmd_\w+', src))

    def test_the_four_that_fell_through_are_now_categorized(self):
        self.assertIn("refresh-baseline", PKM_MUTATING_COMMANDS)
        self.assertIn("iso-prep", PKM_MUTATING_COMMANDS)
        self.assertIn("verify", PKM_READONLY_COMMANDS)
        self.assertIn("check-updates", PKM_READONLY_COMMANDS)

    def test_no_command_is_in_both_sets(self):
        self.assertEqual(
            PKM_MUTATING_COMMANDS & PKM_READONLY_COMMANDS, frozenset())

    def test_every_dispatch_command_is_categorized(self):
        cmds = self._dispatch_commands()
        self.assertTrue(cmds, "failed to extract dispatch commands from cli.py")
        categorized = PKM_MUTATING_COMMANDS | PKM_READONLY_COMMANDS
        uncategorized = cmds - categorized
        self.assertEqual(
            uncategorized, set(),
            f"dispatch command(s) in NEITHER root-gate set (PKM-A23): "
            f"{sorted(uncategorized)} — add to PKM_MUTATING_COMMANDS (mutates, "
            f"needs root) or PKM_READONLY_COMMANDS (pure-read DB).")


if __name__ == "__main__":
    unittest.main()
