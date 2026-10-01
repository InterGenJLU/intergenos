# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""`intergen last` must not report an unreadable cache as an absent one.

`cmd_last` reads the cache with a single bare `except Exception` around
`json.loads(path.read_text())` and answers every failure with one sentence:

    No recent InterGen answer is cached yet — ask something first: intergen ask "..."

That sentence is a claim about the machine's state — that no record exists —
but the code established only that the record could not be READ. A record
truncated or zeroed by an unclean shutdown, one whose mode no longer permits a
read, and a directory standing where the file should be all reach the same
branch, so a person holding a damaged record is told to ask a question while
the fault sits in the record they already have, and the file is never named.

The same module states the distinction correctly everywhere else it reads a
file it does not own the lifetime of — "its command line could not be read",
"Model file: could not be read", "The package record for InterGen could not be
read" — and checks `exists()` before speaking about presence.

What is pinned here:

  * a cache whose contents cannot be parsed is reported as unreadable, by path;
  * a directory standing in for the cache file is reported the same way;
  * a genuinely absent cache still says no answer is cached yet (the control:
    the distinction must be made, not merely reworded).
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from intergen import cli


class TheLastCommandSaysWhichFailureItHitTests(unittest.TestCase):

    def _last_against(self, prepare) -> tuple[Path, str, int]:
        """Run `cmd_last([])` against a cache path `prepare` has set up.

        Returns the cache path, everything written to the error stream, and the
        exit status. No service, socket, model or network is involved: the only
        collaborator is the cache file the command reads.
        """
        with TemporaryDirectory() as d:
            cache = Path(d) / "last-answer.json"
            prepare(cache)
            err, out = io.StringIO(), io.StringIO()
            with mock.patch.object(cli, "_last_answer_path",
                                   return_value=cache):
                with redirect_stdout(out), redirect_stderr(err):
                    with self.assertRaises(SystemExit) as raised:
                        cli.cmd_last([])
            return cache, err.getvalue(), raised.exception.code

    def test_an_unreadable_last_answer_is_reported_as_unreadable(self):
        cache, err, code = self._last_against(
            lambda p: p.write_text("{ truncated"))
        self.assertNotEqual(code, 0, "a failed read must not report success")
        self.assertNotIn(
            "No recent InterGen answer is cached yet", err,
            "the record exists and could not be parsed; saying none is cached "
            "yet states a fact the command never established")
        self.assertIn(
            "could not be read", err,
            "the line must say what actually happened: the cached answer "
            "could not be read")
        self.assertIn(
            str(cache), err,
            "the unreadable file must be named so it can be inspected or "
            "removed")

    def test_a_directory_in_place_of_the_cache_is_reported_as_unreadable(self):
        cache, err, code = self._last_against(lambda p: p.mkdir())
        self.assertNotEqual(code, 0, "a failed read must not report success")
        self.assertNotIn(
            "No recent InterGen answer is cached yet", err,
            "a directory standing in for the cache file is a broken cache, "
            "not an empty one")
        self.assertIn("could not be read", err)
        self.assertIn(str(cache), err)

    def test_an_absent_cache_still_says_nothing_is_cached_yet(self):
        # The control. This is the one input the present sentence describes
        # truthfully, and it must keep saying so: the fix is a distinction,
        # not a reworded catch-all. Its passing also shows the case reads the
        # error stream the command actually writes to.
        _cache, err, code = self._last_against(lambda p: None)
        self.assertNotEqual(code, 0)
        self.assertIn("No recent InterGen answer is cached yet", err)
        self.assertNotIn("could not be read", err)


if __name__ == "__main__":
    unittest.main()
