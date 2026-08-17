# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""M8-1 leg 0 — remove the shell interpreter from the command executors.

DEFENSIVE change. The safety classifier and the executor previously disagreed on how many
command separators a string contains, because the executor ran strings through a shell
(subprocess shell=True) while the classifier does not (safety-review finding class F1).
This leg removes the shell from BOTH executor sites — intergen/tools/run_command.py
(caller-influenced) and intergen/state_cache.py (code-owned) — so the disagreement is
closed structurally: a string cannot run a second command on its own when nothing
interprets its separators.

The LEG-0 bar these tests prove: with the classifier stubbed to its most permissive verdict
(i.e. assumed absent), a string that joins two commands with a shell construct is DECLINED
before the executor is reached — so the second command cannot run, classifier or no
classifier. The classifier stays as an independent second layer (its own suite covers it).

Every probe string below is inert (two harmless `echo`s, or a temp path) — the tests assert
on STRUCTURE (a shell metacharacter is present -> the string is declined), not on any
runnable command.
"""

from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from intergen.interfaces.types import SafetyTier
from intergen.state_cache import (
    StateCache, _DYNAMIC_COMMANDS, _STATIC_COMMANDS, _pipeline_display,
)
from intergen.tools.run_command import RunCommandTool

# Structural probes for the leg-0 bar. Each joins a harmless leading command to a
# SECOND command via one of the shell constructs (&, &&, ;, newline, $() , backticks,
# redirection, pipe, ||) — the shape that, under a shell executor, would let the second
# command run on its own. All strings are inert; the shell-free executor declines each
# before any execution, so the second command can never run.
TWO_COMMAND_SHAPES = [
    "echo one & echo two",          # & separator
    "echo one && echo two",         # &&
    "echo one ; echo two",          # ;
    "echo one\necho two",           # newline
    "echo one $(echo two)",         # command substitution
    "echo one `echo two`",          # backtick substitution
    "echo one > /tmp/leg0-probe",   # redirection to a temp path
    "echo one | echo two",          # pipe
    "echo one || echo two",         # ||
]

_FAKE_OK = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok\n", stderr="")


class RunCommandShellFreeTest(unittest.TestCase):
    def setUp(self):
        self.tool = RunCommandTool()

    def test_two_command_shapes_declined_with_classifier_absent(self):
        """THE LEG-0 BAR: with the classifier forced to its most permissive verdict
        (assumed absent), every two-command shape is declined and NEVER reaches
        subprocess — the second command cannot run on its own."""
        with mock.patch("intergen.tools.run_command.classify_command",
                        return_value=SafetyTier.AUTO), \
             mock.patch("intergen.tools.run_command.subprocess.run") as mrun:
            for cmd in TWO_COMMAND_SHAPES:
                res = self.tool.execute({"command": cmd})
                self.assertFalse(res.success, f"must be declined: {cmd!r}")
                self.assertTrue(getattr(res, "blocked", False),
                                f"must signal blocked (no fabricated success): {cmd!r}")
            self.assertEqual(mrun.call_count, 0,
                             "no two-command shape may reach the executor")

    def test_safe_command_execs_argv_never_shell(self):
        """A plain read-only command executes via argv with shell=False."""
        with mock.patch("intergen.tools.run_command.subprocess.run",
                        return_value=_FAKE_OK) as mrun:
            res = self.tool.execute({"command": "df -h"})
        self.assertTrue(res.success)
        self.assertEqual(mrun.call_count, 1)
        args, kwargs = mrun.call_args
        self.assertIsInstance(args[0], list, "executor must receive an argv LIST")
        self.assertEqual(args[0], ["df", "-h"])
        self.assertNotEqual(kwargs.get("shell"), True, "shell must never be True")

    def test_real_read_only_command_runs(self):
        """End-to-end with the real classifier + real exec: a genuine read-only
        command returns real output (no mock)."""
        res = self.tool.execute({"command": "printf hello"})
        self.assertTrue(res.success)
        self.assertIn("hello", res.content)

    def test_unbalanced_quotes_declined(self):
        with mock.patch("intergen.tools.run_command.subprocess.run") as mrun:
            res = self.tool.execute({"command": 'echo "unterminated'})
        self.assertFalse(res.success)
        self.assertEqual(mrun.call_count, 0)

    def test_each_construct_char_triggers_decline(self):
        """Every enumerated shell construct is individually declined (all inert)."""
        for cmd in ["a | b", "a & b", "a ; b", "a < f", "a > f",
                    "a $(b)", "a `b`", "a && b", "a || b", "a\nb"]:
            with mock.patch("intergen.tools.run_command.classify_command",
                            return_value=SafetyTier.AUTO), \
                 mock.patch("intergen.tools.run_command.subprocess.run") as mrun:
                res = self.tool.execute({"command": cmd})
                self.assertFalse(res.success, f"{cmd!r} must be declined")
                self.assertEqual(mrun.call_count, 0, f"{cmd!r} must not exec")


class StateCacheShellFreeTest(unittest.TestCase):
    def test_command_dicts_are_argv_pipelines_not_strings(self):
        """Regression guard: no code-owned command may be a shell string again —
        every value is a list of argv stages (list[list[str]])."""
        for name, dct in (("_STATIC_COMMANDS", _STATIC_COMMANDS),
                          ("_DYNAMIC_COMMANDS", _DYNAMIC_COMMANDS)):
            for key, pipeline in dct.items():
                self.assertIsInstance(pipeline, list, f"{name}[{key}] not a list")
                self.assertTrue(pipeline, f"{name}[{key}] empty")
                for stage in pipeline:
                    self.assertIsInstance(stage, list, f"{name}[{key}] stage not argv")
                    self.assertTrue(all(isinstance(a, str) for a in stage))

    def test_run_pipeline_never_uses_shell(self):
        with mock.patch("intergen.state_cache.subprocess.run",
                        return_value=_FAKE_OK) as mrun:
            StateCache._run_pipeline([["lscpu"], ["head", "-20"]], timeout=5)
        self.assertEqual(mrun.call_count, 2)
        for call in mrun.call_args_list:
            args, kwargs = call
            self.assertIsInstance(args[0], list)
            self.assertNotEqual(kwargs.get("shell"), True)

    def test_pipeline_output_matches_shell_pipe(self):
        """Behaviour-preservation on a DETERMINISTIC pipeline: the argv-stage form
        produces the identical output the former `a | b` shell pipe would."""
        pipeline = [["printf", "a\nb\nc\nd\n"], ["head", "-2"]]
        new = StateCache._run_pipeline(pipeline, timeout=5).stdout
        old = subprocess.run("printf 'a\\nb\\nc\\nd\\n' | head -2",
                             shell=True, capture_output=True, text=True).stdout
        self.assertEqual(new, old)
        self.assertEqual(new, "a\nb\n")

    def test_single_stage_pipeline_runs(self):
        out = StateCache._run_pipeline([["printf", "hi"]], timeout=5).stdout
        self.assertEqual(out, "hi")

    def test_pipeline_display_is_readable(self):
        self.assertEqual(
            _pipeline_display([["lspci"], ["grep", "-i", "vga"]]),
            "lspci | grep -i vga")


if __name__ == "__main__":
    unittest.main()
