# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The first-boot greeter renders the Qwen attribution where InterGen is shown.

The Welcomer is a GTK app and not part of the intergen package, so it asks the
CLI over a process boundary — the same rule `_model_offer` states for
`intergen setup --show-offer`. The attribution it renders therefore comes from
`intergen --version`, which is the one command that already produces the
sentence, so the greeter cannot drift into wording of its own.

GTK widget construction needs a display and is exercised by the local render
proof, not here. These cases are pure and run headless: they pin what the
greeter asks, what it does with each answer, and — the property that matters
most — that it renders NOTHING rather than a guess whenever the answer is
missing, malformed, slow or an error.
"""

import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WELCOME_PY = REPO_ROOT / "assets" / "intergen-welcome" / "intergen-welcome.py"

_spec = importlib.util.spec_from_file_location("intergen_welcome", WELCOME_PY)
welcome = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(welcome)

LINE = ("Powered by Qwen — Qwen3.5-9B, used under the "
        "Tongyi Qianwen License.")
VERSION_STDOUT = f"InterGen 0.1.0\n{LINE}\n"


def _run(stdout="", returncode=0, exc=None):
    def _fake(cmd, *a, **k):
        _fake.calls.append(cmd)
        if exc is not None:
            raise exc
        return subprocess.CompletedProcess(cmd, returncode, stdout, "")
    _fake.calls = []
    return _fake


class GreeterAttributionTests(unittest.TestCase):

    def test_it_asks_the_cli_that_already_produces_the_sentence(self):
        fake = _run(VERSION_STDOUT)
        with mock.patch.object(subprocess, "run", fake):
            welcome._qwen_attribution()
        self.assertEqual(fake.calls, [["intergen", "--version"]],
                         "the greeter must ask `intergen --version`, the one "
                         "command that renders this sentence")

    def test_it_returns_the_attribution_line_the_cli_printed(self):
        with mock.patch.object(subprocess, "run", _run(VERSION_STDOUT)):
            self.assertEqual(welcome._qwen_attribution(), LINE)

    def test_it_returns_nothing_when_the_cli_prints_no_attribution(self):
        """A Tier-1 box: the version prints, the attribution does not."""
        with mock.patch.object(subprocess, "run", _run("InterGen 0.1.0\n")):
            self.assertIsNone(welcome._qwen_attribution())

    def test_a_nonzero_exit_renders_nothing(self):
        with mock.patch.object(subprocess, "run",
                               _run(VERSION_STDOUT, returncode=1)):
            self.assertIsNone(welcome._qwen_attribution())

    def test_a_missing_intergen_renders_nothing(self):
        with mock.patch.object(subprocess, "run",
                               _run(exc=FileNotFoundError("intergen"))):
            self.assertIsNone(welcome._qwen_attribution())

    def test_a_slow_intergen_renders_nothing_and_does_not_hang_the_greeter(self):
        exc = subprocess.TimeoutExpired(cmd="intergen", timeout=20)
        with mock.patch.object(subprocess, "run", _run(exc=exc)):
            self.assertIsNone(welcome._qwen_attribution())

    def test_the_call_carries_a_timeout(self):
        """A first-boot greeter must never block on a child process."""
        captured = {}

        def _fake(cmd, *a, **k):
            captured.update(k)
            return subprocess.CompletedProcess(cmd, 0, VERSION_STDOUT, "")

        with mock.patch.object(subprocess, "run", _fake):
            welcome._qwen_attribution()
        self.assertIn("timeout", captured,
                      "the greeter called intergen with no timeout")
        self.assertLessEqual(captured["timeout"], 20)

    def test_unrelated_output_is_not_mistaken_for_an_attribution(self):
        """Only a line that IS the attribution counts. Matching loosely would
        let any future line of version output be rendered as a license
        statement."""
        with mock.patch.object(subprocess, "run",
                               _run("InterGen 0.1.0\nsomething else\n")):
            self.assertIsNone(welcome._qwen_attribution())


if __name__ == "__main__":
    unittest.main()
