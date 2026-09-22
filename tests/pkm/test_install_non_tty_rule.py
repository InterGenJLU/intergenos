# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""An install that reaches beyond what was asked for does not proceed unwatched.

WHY THIS EXISTS. pkm has three confirmation behaviours where it should have
one. `pkm upgrade` refuses on a non-tty without --yes and names the flag.
`pkm remove` now does the same. `pkm install` did the opposite: when the
resolution went BEYOND the package that was named — `pkm install steam`
resolving a forty-package closure — and there was no terminal attached, it
printed "no terminal attached; proceeding" and installed all forty.

That was written on purpose, and its reasoning was real: there is nobody there
to warn, so warning nobody and stopping helps no one. This reverses it, and the
reason is the asymmetry of the two mistakes. Refusing costs one re-run with
--yes. Proceeding installs a closure nobody approved onto a machine whose owner
is not present, and the record of what was added is the only way they will ever
find out.

The gate is unchanged in scope: it fires ONLY when the resolution goes beyond
the named package. Installing exactly what was asked for still needs no
summary of itself and no confirmation.
"""
from __future__ import annotations

import io
import unittest

from pkm import txn


class _Stdin:
    def __init__(self, tty):
        self._tty = tty

    def isatty(self):
        return self._tty


class _Reporter:
    """Records what the confirmation said, in the order it said it."""

    def __init__(self):
        self.lines = []

    def step(self, *a):
        self.lines.append(" ".join(str(x) for x in a))

    def step_continuation(self, line):
        self.lines.append(str(line))

    def info(self, line):
        self.lines.append(str(line))

    def error(self, line):
        self.lines.append("ERROR: " + str(line))

    def text(self):
        return "\n".join(self.lines)


class _Plan:
    action = "Install"
    beyond_requested = True

    def summary_line(self):
        return "3 packages, 40 MB to download"

    def name_list(self):
        return ["  example", "  libone", "  libtwo"]


class TestNoTerminalAndNoYes(unittest.TestCase):

    def test_it_does_not_proceed(self):
        rep = _Reporter()
        ok = txn.confirm(_Plan(), rep, stdin=_Stdin(False))
        self.assertFalse(
            ok,
            "an install that pulled in packages nobody named proceeded with "
            "no terminal attached and no --yes:\n" + rep.text())

    def test_it_names_the_flag_that_would_let_it_proceed(self):
        rep = _Reporter()
        txn.confirm(_Plan(), rep, stdin=_Stdin(False))
        self.assertIn("--yes", rep.text(),
                      "the refusal did not name the flag that would let an "
                      "unattended run proceed:\n" + rep.text())

    def test_it_still_prints_the_plan_it_is_refusing(self):
        rep = _Reporter()
        txn.confirm(_Plan(), rep, stdin=_Stdin(False))
        self.assertIn("3 packages", rep.text(),
                      "the refusal did not say what it was refusing:\n"
                      + rep.text())


class TestYesStillProceeds(unittest.TestCase):

    def test_with_yes_and_no_terminal_it_proceeds(self):
        rep = _Reporter()
        self.assertTrue(
            txn.confirm(_Plan(), rep, assume_yes=True, stdin=_Stdin(False)),
            rep.text())


class TestTheScopeOfTheGateIsUnchanged(unittest.TestCase):
    """The non-masking control: this gate was never about every install."""

    def test_a_plan_that_does_not_reach_beyond_never_asks(self):
        """cli only calls confirm() when the plan reaches beyond the request.

        Pinned here by reading the call site rather than by asserting on a
        behaviour confirm() does not own.
        """
        import ast
        import inspect
        from pkm import cli
        source = inspect.getsource(cli)
        tree = ast.parse(source)
        guarded = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            test = node.test
            if (isinstance(test, ast.Attribute)
                    and test.attr == "beyond_requested"):
                for c in ast.walk(node):
                    if (isinstance(c, ast.Call)
                            and isinstance(c.func, ast.Attribute)
                            and c.func.attr == "confirm"):
                        guarded.append(c)
        self.assertEqual(
            len(guarded), 1,
            "the confirmation is no longer guarded by `beyond_requested`, so "
            "it now fires for an install of exactly what was asked for")


if __name__ == "__main__":
    unittest.main()
