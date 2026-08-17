# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Interactive-pager / paginating shell-escape guard + watch exec-wrapper.

- less/more/most/top/htop are interactive programs: on a controlling terminal a
  user can `!cmd` (or less `v`->$EDITOR) to spawn a child shell classify_command
  never sees. So a BARE pager is a shell-escape vector -> CONFIRM, while a PIPED
  pager (`du | less`) is a pure data-sink -> AUTO.
- journalctl / git log / git show / systemctl status paginate through $PAGER by
  default -> same class: bare interactive form is CONFIRM, non-interactive
  (piped, or explicit --no-pager/--no-paginate) is AUTO.
- watch is an exec wrapper like env: classified by its WRAPPED command, so
  `watch 'rm -rf x'` is BLOCKED and `watch free` is AUTO, never a flat pass.

(Sibling to the sort/uniq/find/env write-form guards in test_safety_write_form.)
"""

from __future__ import annotations

import unittest

from intergen.safety import classify_command
from intergen.interfaces.types import SafetyTier


class BarePagerIsConfirm(unittest.TestCase):
    def test_bare_interactive_pager_or_paginator_is_confirm(self):
        for cmd in (
            "less /var/log/syslog",
            "more /etc/fstab",
            "most f",
            "top",
            "htop",
            "journalctl",
            "git log",
            "git show",
            "systemctl status nginx",
        ):
            self.assertEqual(classify_command(cmd), SafetyTier.CONFIRM, cmd)


class PipedOrNonInteractiveIsAuto(unittest.TestCase):
    def test_piped_or_noninteractive_is_auto(self):
        for cmd in (
            "du -sh /var | less",
            "ps aux | less",
            "journalctl --no-pager",
            "journalctl -p err -n 5 --no-pager -o cat",
            "journalctl -p err | head",
            "git log --no-pager",
            "git --no-pager log",
            "git log | head",
        ):
            self.assertEqual(classify_command(cmd), SafetyTier.AUTO, cmd)


class WatchExecWrapper(unittest.TestCase):
    def test_watch_wraps_destructive_is_blocked(self):
        for cmd in (
            "watch 'rm -rf /tmp/x'",
            "watch -n 5 rm -rf x",
        ):
            self.assertEqual(classify_command(cmd), SafetyTier.BLOCKED, cmd)

    def test_watch_wraps_read_is_auto(self):
        for cmd in (
            "watch free",
            "watch -n 2 ls",
        ):
            self.assertEqual(classify_command(cmd), SafetyTier.AUTO, cmd)

    def test_watch_wraps_confirm_is_confirm(self):
        self.assertEqual(
            classify_command("watch systemctl restart nginx"),
            SafetyTier.CONFIRM)


class PaginatingGitSubcommands(unittest.TestCase):
    # git diff/branch/tag default-paginate exactly like log/show (the reviewer
    # fold-in closing the twin): bare is escapable -> CONFIRM, non-interactive
    # (piped or --no-pager/--no-paginate) is a data-sink -> AUTO. git status /
    # remote / stash list do NOT paginate and stay AUTO regardless.
    def test_bare_paginating_git_is_confirm(self):
        for cmd in ("git diff", "git diff --stat", "git branch", "git tag"):
            self.assertEqual(classify_command(cmd), SafetyTier.CONFIRM, cmd)

    def test_noninteractive_paginating_git_is_auto(self):
        for cmd in (
            "git diff | cat", "git --no-pager diff", "git diff --no-pager",
            "git branch | head", "git --no-pager branch", "git tag | cat",
        ):
            self.assertEqual(classify_command(cmd), SafetyTier.AUTO, cmd)

    def test_nonpaginating_git_reads_stay_auto(self):
        for cmd in ("git status", "git remote"):
            self.assertEqual(classify_command(cmd), SafetyTier.AUTO, cmd)


class UnchangedReadsStayAuto(unittest.TestCase):
    def test_plain_reads_stay_auto(self):
        for cmd in ("ps", "df -h", "free", "git status"):
            self.assertEqual(classify_command(cmd), SafetyTier.AUTO, cmd)


class CuratedCpuCommandRegression(unittest.TestCase):
    def test_curated_cpu_command_stays_auto(self):
        # The curated system-state cpu command uses ps + a piped head; the pager
        # guard must not regress its AUTO.
        self.assertEqual(
            classify_command(
                "ps -eo pcpu,pmem,comm --sort=-pcpu --no-headers | head -8"),
            SafetyTier.AUTO)


if __name__ == "__main__":
    unittest.main()
