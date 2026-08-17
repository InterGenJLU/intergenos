# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""AUTO mutate/exec guard — a command is AUTO only if it cannot mutate state OR exec
an arbitrary command REGARDLESS of args, never safe-only-while-an-invariant-holds.

- sort/uniq/cut are AUTO read-only stream filters (so `du | sort -rh | head`
  auto-runs), but `sort -o FILE` / `--output=FILE` and `uniq INPUT OUTPUT` WRITE a
  file -> CONFIRM. cut has no write form.
- find is AUTO read-only, but the mutate/exec primaries -delete / -exec / -execdir /
  -ok / -okdir and the write-to-file -fprint / -fprintf / -fls -> CONFIRM.
- env is an exec wrapper, so it is classified by its WRAPPED command (transparent):
  `env ls` AUTO, `env touch x` CONFIRM, `env <unknown>` CONFIRM (fail-safe); bare
  env (print/set environment) AUTO. This closes the laundering hole flat env-in-AUTO
  had while keeping env-of-a-read AUTO.

(A red-team raised the sort/uniq class; find/env folded in by the same rule. The
interactive-pager shell-escape class — less/more/top/htop — is tracked separately.)
"""

from __future__ import annotations

import unittest

from intergen.safety import SafetyTier, classify_command


class SortWriteFormGuard(unittest.TestCase):
    def test_sort_output_flag_is_confirm(self):
        for cmd in (
            "sort -o /tmp/out.txt /tmp/in.txt",
            "sort --output /tmp/out.txt /tmp/in.txt",
            "sort --output=/tmp/out.txt /tmp/in.txt",
            "sort -ro /tmp/out.txt /tmp/in.txt",   # clustered short flags
        ):
            self.assertEqual(classify_command(cmd), SafetyTier.CONFIRM, cmd)

    def test_sort_read_only_stays_auto(self):
        for cmd in ("sort /tmp/in.txt", "sort -rh", "sort -n -k2", "sort -u in.txt"):
            self.assertEqual(classify_command(cmd), SafetyTier.AUTO, cmd)


class UniqWriteFormGuard(unittest.TestCase):
    def test_uniq_output_operand_is_confirm(self):
        for cmd in (
            "uniq /tmp/in.txt /tmp/out.txt",
            "uniq -c /tmp/in.txt /tmp/out.txt",
            "uniq -f 2 /tmp/in.txt /tmp/out.txt",   # -f consumes the numeric value
            "uniq - /tmp/out.txt",                   # stdin -> output file
        ):
            self.assertEqual(classify_command(cmd), SafetyTier.CONFIRM, cmd)

    def test_uniq_read_only_stays_auto(self):
        for cmd in ("uniq /tmp/in.txt", "uniq -c /tmp/in.txt",
                    "uniq -f 2 /tmp/in.txt", "uniq -cd in.txt", "uniq"):
            self.assertEqual(classify_command(cmd), SafetyTier.AUTO, cmd)


class CutAndPipelineUnaffected(unittest.TestCase):
    def test_cut_stays_auto(self):
        for cmd in ("cut -d: -f1 /etc/passwd", "cut -c1-10 file.txt"):
            self.assertEqual(classify_command(cmd), SafetyTier.AUTO, cmd)

    def test_disk_usage_pipeline_stays_auto(self):
        # The exact selector command for "what's eating my disk" — read-only, AUTO.
        self.assertEqual(
            classify_command("du -ah ~ 2>/dev/null | sort -rh | head -20"),
            SafetyTier.AUTO)

    def test_write_form_in_pipeline_degrades_to_confirm(self):
        # A write-form sort anywhere in a pipeline pulls the compound to CONFIRM.
        self.assertEqual(
            classify_command("cat in.txt | sort -o /tmp/out.txt"),
            SafetyTier.CONFIRM)


class FindMutateExecGuard(unittest.TestCase):
    def test_find_mutate_exec_forms_are_confirm(self):
        for cmd in (
            "find . -delete",
            "find /tmp -name '*.tmp' -delete",
            "find . -exec ls {} +",
            "find . -execdir touch {} +",
            "find . -ok rm {} +",
            "find . -okdir rm {} +",
            "find . -fprint /tmp/list",
            "find . -fprintf /tmp/list '%p'",
            "find . -fls /tmp/list",
        ):
            self.assertEqual(classify_command(cmd), SafetyTier.CONFIRM, cmd)

    def test_read_only_find_stays_auto(self):
        for cmd in ("find . -name '*.py'", "find /etc -type f",
                    "find . -mtime -1 -print", "find /var/log -size +10M"):
            self.assertEqual(classify_command(cmd), SafetyTier.AUTO, cmd)


class EnvWrappedClassifier(unittest.TestCase):
    def test_env_wrapping_read_stays_auto(self):
        for cmd in ("env", "env FOO=bar", "env ls", "env HOME=/tmp ls",
                    "env -i ls -la", "env -u PATH cat /etc/hostname",
                    "env env ls"):   # nested env
            self.assertEqual(classify_command(cmd), SafetyTier.AUTO, cmd)

    def test_env_wrapping_write_is_confirm(self):
        for cmd in ("env touch /tmp/x", "env FOO=bar cp a b",
                    "env python3 script.py", "env mv a b"):
            self.assertEqual(classify_command(cmd), SafetyTier.CONFIRM, cmd)

    def test_env_wrapping_unknown_is_confirm_failsafe(self):
        self.assertEqual(classify_command("env some-unknown-binary"),
                         SafetyTier.CONFIRM)

    def test_env_wrapping_dangerous_is_blocked(self):
        for cmd in ("env rm -rf /tmp/x", "env FOO=bar mkfs.ext4 /dev/sda1"):
            self.assertEqual(classify_command(cmd), SafetyTier.BLOCKED, cmd)


if __name__ == "__main__":
    unittest.main()
