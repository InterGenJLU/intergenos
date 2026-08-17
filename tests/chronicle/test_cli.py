#!/usr/bin/env python3
"""CLI: GUI-CLI parity (every user action has a CLI verb backed by an engine
verb) and golden output under --json / -q (spec §11, §13)."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from chronicle import api as _api
from chronicle import cli as _cli


# The user-facing action -> the engine verb(s) that back it. This is the
# GUI-CLI parity contract: the GUI and CLI drive the SAME engine verbs.
_ACTION_TO_ENGINE_VERBS = {
    "status": ["status"],
    "list": ["list"],
    "capture": ["capture"],
    "diff": ["diff"],
    "restore": ["restore-plan", "restore"],
    "verify": ["verify", "scrub"],
    "target": ["target-scan", "target-adopt"],
    "pin": ["pin"],
    "unpin": ["unpin"],
    "queue": ["queue-status"],
}


class ParityTest(unittest.TestCase):
    def test_cli_commands_match_the_action_contract(self):
        self.assertEqual(set(_cli.COMMANDS), set(_ACTION_TO_ENGINE_VERBS),
                         "CLI verb set drifted from the GUI-CLI action contract")

    def test_every_action_is_backed_by_a_real_engine_verb(self):
        for action, verbs in _ACTION_TO_ENGINE_VERBS.items():
            for v in verbs:
                self.assertIn(v, _api._VERBS,
                              f"action {action!r} needs engine verb {v!r}")

    def test_parser_registers_every_command(self):
        ap = _cli.build_parser()
        sub = next(a for a in ap._actions if getattr(a, "choices", None)
                   and "status" in getattr(a, "choices", {}))
        self.assertEqual(set(sub.choices), set(_cli.COMMANDS))


class GoldenOutputTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chronicle-cli-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        # Force in-process backend (nonexistent socket) against a tmp store.
        full = ["--local-root", self.tmp, "--socket", "/nonexistent/sock"] + argv
        with redirect_stdout(out), redirect_stderr(err):
            rc = _cli.main(full)
        return rc, out.getvalue(), err.getvalue()

    def test_status_json_is_valid_json(self):
        rc, out, _err = self._run(["status", "--json"])
        self.assertEqual(rc, 0)
        parsed = json.loads(out)
        self.assertIn("target", parsed)

    def test_quiet_suppresses_info(self):
        rc, out, _err = self._run(["-q", "status"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "", "-q emits no info lines on a clean status")

    def test_verbose_status_prints_human_lines(self):
        rc, out, _err = self._run(["-v", "status"])
        self.assertEqual(rc, 0)
        self.assertIn("Chronicle status", out)

    def test_no_command_prints_help_rc2(self):
        rc, _out, _err = self._run([])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
