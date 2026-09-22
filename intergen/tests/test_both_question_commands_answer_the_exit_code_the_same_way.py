# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Both question-asking commands say the same thing with their exit code.

The rule written down on 2026-09-22 — a question that produced no answer exits
non-zero — was stated in general terms and implemented for one of the two
commands that ask a question. Measured in a second reading of that change on
2026-09-22: the command that asks the configured frontier model returned 0 on
every reply that answered nothing, including a reply saying the attempt had
failed and a reply with no text in it at all. A script, a timer unit or a shell
conditional reading that exit code was told the question had been answered.

Both commands now hand their reply to the same delivery helper, so both answer
the exit code the same way. The two replies declare "this turn produced nothing"
with different words, and the helper reads both:

  * the assistant's own answers carry a ``handled`` field, false while it is
    starting up, while it is paused for a game, and on an error;
  * the frontier replies carry a ``sent`` field, false when no escalation
    manager exists, when no provider is configured, when the person declined
    the send, when the send raised past the escalation manager, and when the
    manager caught a failure of its own (the provider unreachable, or anything
    else raised during the send) — six ways, measured in the daemon and its
    manager on 2026-09-22. The last two are pinned end to end in
    test_a_frontier_send_that_failed_is_reported_as_not_sent.py.

Neither field is ever defaulted in the direction that would hide silence: a
reply that does not carry a field is not treated as declaring failure, and a
reply that carries one is believed.
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


class _NoFillers:
    """The waiting animation, with nothing to animate."""

    def __enter__(self):
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def _run(command, payload: dict, cache_dir: Path):
    """Run one command against a daemon that owns the bus name and answers with
    ``payload``. Returns the exit code (None when the command did not exit),
    what the person saw, and what went to the error stream."""
    out, err = io.StringIO(), io.StringIO()
    code = None
    with mock.patch.object(cli, "daemon_has_owner", return_value=True), \
            mock.patch.object(cli, "try_dbus",
                              return_value=json.dumps(payload)), \
            mock.patch.object(cli, "_last_answer_path",
                              return_value=cache_dir / "last-answer.json"), \
            mock.patch.object(cli, "_AskFillers", _NoFillers, create=True):
        with redirect_stdout(out), redirect_stderr(err):
            try:
                command("is my disk encrypted?")
            except SystemExit as exc:
                code = exc.code
    return code, out.getvalue(), err.getvalue()


class TheFrontierCommandAnswersTheExitCode(unittest.TestCase):
    """The command that asks the configured frontier model."""

    def test_a_reply_with_no_text_exits_non_zero(self) -> None:
        with TemporaryDirectory() as tmp:
            code, shown, said = _run(cli.cmd_ask_frontier,
                                     {"response": "", "source": "escalation",
                                      "sent": False},
                                     Path(tmp))
        self.assertEqual(code, 2, "a reply with no answer in it must not exit 0")
        self.assertIn("no answer", said.lower())

    def test_a_reply_that_says_nothing_was_sent_exits_non_zero(self) -> None:
        # The daemon's own words when no frontier model is configured. The
        # person must still read the sentence: it says what to do about it.
        reply = {"response": ("No frontier model is configured. Add a provider "
                              "to the human-only configuration to use "
                              "phone-a-friend."),
                 "source": "escalation", "sent": False}
        with TemporaryDirectory() as tmp:
            code, shown, said = _run(cli.cmd_ask_frontier, reply, Path(tmp))
        self.assertEqual(code, 2,
                         "a reply declaring nothing was sent must not exit 0")
        self.assertIn("No frontier model is configured", shown,
                      "the reply's own explanation must still reach the person")

    def test_a_declined_send_exits_non_zero(self) -> None:
        reply = {"response": "Cancelled — nothing was sent to the frontier model.",
                 "source": "escalation", "sent": False}
        with TemporaryDirectory() as tmp:
            code, shown, said = _run(cli.cmd_ask_frontier, reply, Path(tmp))
        self.assertEqual(code, 2)
        self.assertIn("Cancelled", shown)

    def test_a_real_frontier_answer_exits_zero_and_is_printed(self) -> None:
        reply = {"response": "Yes — the root filesystem is encrypted.",
                 "source": "frontier:example", "sent": True}
        with TemporaryDirectory() as tmp:
            code, shown, said = _run(cli.cmd_ask_frontier, reply, Path(tmp))
        self.assertIsNone(code, "an answered question must not exit at all")
        self.assertIn("root filesystem is encrypted", shown)

    def test_what_the_last_answer_command_is_left_holding(self) -> None:
        """The record the last-answer command reads tells the truth about THIS
        turn, and never repeats an older answer as though it were the reply.

        That is why a reply with nothing in it is not written — the previous
        answer would still be sitting there — while a reply that carries its own
        explanation IS written: "the send failed" is a true statement about the
        turn the person just took, and a stale answer is not. The exit code is
        what says the question went unanswered; the record is what says what
        happened. The same rule the assistant's own command follows.
        """
        # Nothing in the reply: nothing is written, so the stale answer that
        # would otherwise be repeated is not created either.
        with TemporaryDirectory() as tmp:
            cache = Path(tmp) / "last-answer.json"
            _run(cli.cmd_ask_frontier,
                 {"response": "", "source": "error", "sent": False},
                 Path(tmp))
            self.assertFalse(cache.exists())
        # The attempt failed and said so: that sentence is the truth about this
        # turn, so it is what the last-answer command finds.
        with TemporaryDirectory() as tmp:
            cache = Path(tmp) / "last-answer.json"
            _run(cli.cmd_ask_frontier,
                 {"response": "Escalation failed: TimeoutError",
                  "source": "error", "sent": False}, Path(tmp))
            self.assertTrue(cache.exists())
            self.assertIn("TimeoutError",
                          json.loads(cache.read_text())["response"])
        # And an answer the person actually received is kept, which is what
        # makes the two cases above a real check of the record rather than a
        # check of a command that writes nothing at all.
        with TemporaryDirectory() as tmp:
            cache = Path(tmp) / "last-answer.json"
            _run(cli.cmd_ask_frontier,
                 {"response": "Yes — the root filesystem is encrypted.",
                  "source": "frontier:example", "sent": True}, Path(tmp))
            self.assertTrue(cache.exists())
            self.assertIn("encrypted",
                          json.loads(cache.read_text())["response"])


class TheAssistantCommandStillAnswersTheExitCode(unittest.TestCase):
    """The rule that was already true stays true — the same two shapes."""

    def test_a_reply_with_no_text_exits_non_zero(self) -> None:
        with TemporaryDirectory() as tmp:
            code, shown, said = _run(cli.cmd_ask,
                                     {"response": "", "handled": True},
                                     Path(tmp))
        self.assertEqual(code, 2)

    def test_a_reply_the_assistant_did_not_handle_exits_non_zero(self) -> None:
        with TemporaryDirectory() as tmp:
            code, shown, said = _run(
                cli.cmd_ask,
                {"response": "InterGen is starting up, please wait.",
                 "handled": False}, Path(tmp))
        self.assertEqual(code, 2)
        self.assertIn("starting up", shown)


class NeitherFieldIsDefaultedIntoSilence(unittest.TestCase):
    """A reply that declares nothing is not read as declaring failure."""

    def test_a_reply_carrying_neither_field_with_text_delivers(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with TemporaryDirectory() as tmp:
            with mock.patch.object(
                    cli, "_last_answer_path",
                    return_value=Path(tmp) / "last-answer.json"):
                with redirect_stdout(out), redirect_stderr(err):
                    delivered = cli._deliver_answer({"response": "an answer"})
        self.assertTrue(delivered)

    def test_a_reply_saying_it_was_not_sent_does_not_deliver(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with TemporaryDirectory() as tmp:
            with mock.patch.object(
                    cli, "_last_answer_path",
                    return_value=Path(tmp) / "last-answer.json"):
                with redirect_stdout(out), redirect_stderr(err):
                    delivered = cli._deliver_answer(
                        {"response": "Cancelled — nothing was sent.",
                         "sent": False})
        self.assertFalse(delivered)


class TheTwoStreamsReachTheReaderInOrder(unittest.TestCase):
    """The reply's own sentence is read BEFORE the line about the exit code.

    The answer goes to standard output and the line about the question going
    unanswered goes to standard error. Both land in the same terminal and in the
    same redirected log, but standard output is block-buffered once it is
    redirected while standard error is not, so without a flush the explanation
    arrives after the complaint. Measured in a redirected capture on this
    project's machine on 2026-09-22, where the line "InterGen did not answer
    that question." was written above the sentence saying no frontier model is
    configured. This case runs a real process with both streams joined, which is
    the only arrangement in which the ordering can be observed at all.
    """

    def test_the_explanation_is_written_before_the_complaint(self) -> None:
        import subprocess
        import sys as _sys
        from pathlib import Path as _Path

        tree = str(_Path(__file__).resolve().parents[2])
        program = (
            "import sys, json, tempfile, pathlib\n"
            "sys.path.insert(0, %r)\n"
            "from unittest import mock\n"
            "from intergen import cli\n"
            "tmp = tempfile.mkdtemp()\n"
            "with mock.patch.object(cli, '_last_answer_path',\n"
            "                       return_value=pathlib.Path(tmp)/'a.json'):\n"
            "    cli._deliver_answer({'response': 'NO PROVIDER IS CONFIGURED',\n"
            "                         'sent': False})\n" % tree)
        joined = subprocess.run(
            [_sys.executable, "-c", program],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=120, check=False).stdout.decode()
        self.assertIn("NO PROVIDER IS CONFIGURED", joined)
        self.assertIn("did not answer", joined)
        self.assertLess(joined.index("NO PROVIDER IS CONFIGURED"),
                        joined.index("did not answer"),
                        "the reply's own explanation must be readable before "
                        "the line about the exit code, in a joined capture")


if __name__ == "__main__":
    unittest.main()
