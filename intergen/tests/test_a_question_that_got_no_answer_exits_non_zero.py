# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Asking a question that is never answered exits non-zero and says so.

Measured on this project's own machine on 2026-09-19: with the assistant's
service stopped, `intergen ask` printed about twenty-six seconds of daemon log
lines, no answer at all, and exited 0; asked again seconds after a start, it
printed "InterGen is starting up, please wait." and exited 0. A zero exit code
is the machine saying the question was answered, so a script that asks and
checks the code cannot tell an answer from silence, and a person reading a
terminal log sees a successful command that produced nothing.

What is pinned here, on the real delivery path the daemon's reply travels
through:

  * an answer delivers, exits zero, and is kept for `intergen last`;
  * an empty reply exits non-zero and prints one plain line;
  * a reply the assistant says it did not handle — it is starting up, it is
    paused while a game runs, it hit an error — exits non-zero, with the
    assistant's own explanation printed first;
  * nothing unanswered is written into the cache `intergen last` reads.
"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from intergen import cli


def _deliver(payload: dict, cache_dir: Path):
    """Run one delivery with the cache pointed at a scratch directory."""
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(cli, "_last_answer_path",
                           return_value=cache_dir / "last-answer.json"):
        with redirect_stdout(out), redirect_stderr(err):
            delivered = cli._deliver_answer(payload)
    return delivered, out.getvalue(), err.getvalue()


def _ask(payload: dict | None, cache_dir: Path):
    """Run `intergen ask` against a daemon that owns the bus name and returns
    `payload`, and give back its exit code (None when it did not exit)."""
    out, err = io.StringIO(), io.StringIO()
    code = None
    with mock.patch.object(cli, "daemon_has_owner", return_value=True), \
            mock.patch.object(cli, "try_dbus",
                              return_value=json.dumps(payload)), \
            mock.patch.object(cli, "_last_answer_path",
                              return_value=cache_dir / "last-answer.json"), \
            mock.patch.object(cli, "_AskFillers", _NoFillers):
        with redirect_stdout(out), redirect_stderr(err):
            try:
                cli.cmd_ask("how much memory does this machine have?")
            except SystemExit as exc:
                code = exc.code
    return code, out.getvalue(), err.getvalue()


class _NoFillers:
    """The waiting animation, with nothing to animate."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class AnAnswerIsAnAnswerTests(unittest.TestCase):

    def test_an_answered_turn_delivers_and_exits_zero(self):
        with TemporaryDirectory() as d:
            payload = {"response": "RAM: 62 GB total.", "handled": True,
                       "full_output": "Mem: 62Gi"}
            delivered, out, err = _deliver(payload, Path(d))
            self.assertTrue(delivered)
            self.assertIn("RAM: 62 GB total.", out)
            code, out, err = _ask(payload, Path(d))
            self.assertIsNone(code, err)
            cached = json.loads((Path(d) / "last-answer.json").read_text())
            self.assertEqual(cached["response"], "RAM: 62 GB total.")


class NoAnswerTests(unittest.TestCase):

    def test_an_empty_reply_is_not_a_success(self):
        with TemporaryDirectory() as d:
            payload = {"response": "", "handled": True}
            delivered, out, err = _deliver(payload, Path(d))
            self.assertFalse(delivered)
            self.assertIn("no answer", err.lower())
            code, _out, _err = _ask(payload, Path(d))
            self.assertEqual(code, 2)

    def test_the_starting_up_reply_is_not_a_success(self):
        with TemporaryDirectory() as d:
            payload = {"response": "InterGen is starting up, please wait.",
                       "source": "startup", "handled": False}
            delivered, out, err = _deliver(payload, Path(d))
            self.assertFalse(delivered)
            # The assistant's own words are shown; the verdict is one line.
            self.assertIn("starting up", out)
            self.assertIn("did not answer", err.lower())
            code, _out, _err = _ask(payload, Path(d))
            self.assertEqual(code, 2)

    def test_the_paused_reply_is_not_a_success(self):
        with TemporaryDirectory() as d:
            payload = {"response": "InterGen is paused while a game is "
                                   "running, so no model is loaded right now.",
                       "source": "paused", "handled": False}
            code, out, err = _ask(payload, Path(d))
            self.assertEqual(code, 2)
            self.assertIn("paused", out)

    def test_an_error_reply_is_not_a_success(self):
        with TemporaryDirectory() as d:
            payload = {"response": "I encountered an error: boom",
                       "source": "error", "handled": False}
            code, _out, _err = _ask(payload, Path(d))
            self.assertEqual(code, 2)

    def test_an_unanswered_turn_does_not_overwrite_the_last_answer(self):
        with TemporaryDirectory() as d:
            cache = Path(d) / "last-answer.json"
            _deliver({"response": "The hostname is box-01.", "handled": True,
                      "full_output": "box-01"}, Path(d))
            self.assertTrue(cache.exists())
            _deliver({"response": "", "handled": True}, Path(d))
            kept = json.loads(cache.read_text())
            self.assertEqual(kept["response"], "The hostname is box-01.")


if __name__ == "__main__":
    unittest.main()
