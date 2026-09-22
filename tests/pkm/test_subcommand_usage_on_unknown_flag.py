# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""An unknown flag on a subcommand prints THAT subcommand's usage.

WHY THIS EXISTS. `pkm remove example --dry-runn` answered with the TOP-LEVEL
usage block — `usage: pkm [-h] [--version] [--db DB] ... command ...` — and the
line "unrecognized arguments: --dry-runn". The one thing a person in that
position needs is the list of flags `remove` actually takes, and that is the
one thing the answer did not contain. It sent them to `pkm --help`, which shows
the commands, not the flags of the command they were already using.

WHAT IS PINNED HERE: the usage block shown names the subcommand, lists that
subcommand's own options, and still says which argument was not recognised. The
exit status stays 2, which is what a command-line error means, and a correct
invocation is untouched.
"""
from __future__ import annotations

import io
import contextlib
import unittest

from pkm import cli


def _run(argv):
    """Parse argv, returning (exit code, everything printed)."""
    err, out = io.StringIO(), io.StringIO()
    parser = cli.build_parser()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
        try:
            cli.parse_command_line(parser, argv)
            code = 0
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
    return code, out.getvalue() + err.getvalue()


class TestTheUsageBelongsToTheSubcommand(unittest.TestCase):

    def test_remove_shows_removes_own_usage(self):
        code, said = _run(["remove", "example", "--dry-runn"])
        self.assertEqual(code, 2, said)
        self.assertIn("usage: pkm remove", said,
                      "the answer showed the top-level usage, not the usage "
                      "of the command the person was using:\n" + said)

    def test_it_lists_the_flags_that_command_does_take(self):
        _code, said = _run(["remove", "example", "--dry-runn"])
        self.assertIn("--dry-run", said,
                      "the usage did not list the flags `remove` accepts, "
                      "which is the one thing the person needed:\n" + said)
        self.assertIn("--force", said, said)

    def test_it_still_says_what_was_not_recognised(self):
        _code, said = _run(["remove", "example", "--dry-runn"])
        self.assertIn("--dry-runn", said,
                      "the answer did not name the argument it rejected:\n"
                      + said)

    def test_install_shows_installs_own_usage(self):
        code, said = _run(["install", "example", "--no-such-flag"])
        self.assertEqual(code, 2, said)
        self.assertIn("usage: pkm install", said, said)

    def test_list_shows_lists_own_usage(self):
        code, said = _run(["list", "--nope"])
        self.assertEqual(code, 2, said)
        self.assertIn("usage: pkm list", said, said)


class TestNothingElseChanged(unittest.TestCase):
    """The non-masking controls."""

    def test_a_correct_invocation_still_parses(self):
        parser = cli.build_parser()
        args = cli.parse_command_line(parser, ["remove", "example", "--dry-run"])
        self.assertEqual(args.command, "remove")
        self.assertTrue(args.remove_dry_run)

    def test_an_unknown_command_still_reports_at_the_top_level(self):
        """No subcommand was chosen, so the top-level usage IS the answer."""
        code, said = _run(["no-such-command"])
        self.assertEqual(code, 2, said)
        self.assertIn("usage: pkm", said, said)

    def test_a_missing_required_argument_still_reports(self):
        code, said = _run(["remove"])
        self.assertEqual(code, 2, said)
        self.assertIn("usage: pkm remove", said, said)


if __name__ == "__main__":
    unittest.main()
