# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A reply the question commands cannot read is still a question with no answer.

test_both_question_commands_answer_the_exit_code_the_same_way.py states the rule
this case holds the commands to: "a question that produced no answer exits
non-zero", both commands "hand their reply to the same delivery helper, so both
answer the exit code the same way", and every case in that file measures that
number as exactly 2. The reply is fed to those cases as ``json.dumps(payload)``,
so every one of them hands the command a JSON object.

Both commands read the reply with a bare ``json.loads`` — the assistant's
question command at cli.py:663 and again on the --direct path at 692, the
frontier command at 771 and 801 — and hand the result straight to
``_deliver_answer``, which reads it with ``.get``. Neither call is guarded, and
the reply is not always the assistant's: the no-reply report is written for the
case where the bus name is held by "an owner that is not the assistant"
(cli.py:223), and the call is accepted as long as the reply carries the ``(s)``
signature ``try_dbus`` asks for — any string at all. A reply that is not JSON, or
is JSON that is not an object, therefore leaves the command by way of an
exception instead of the delivery helper: the person reads a Python traceback
rather than a sentence, and a script reads the interpreter's own exit code 1
where the identical condition — no answer reached the person — is 2 everywhere
else in the contract.

What is pinned here:

  * a reply that is not JSON at all, and a reply that is a JSON list, leave both
    question commands through the exit code rather than through an exception;
  * that exit code is 2, the number the same "no answer" condition carries in
    every other case of the contract;
  * something is written to the error stream saying so, as every other
    no-answer path in these two commands does;
  * measured at process level too, because the exit code a script reads is the
    subject of the claim: the reply that cannot be read must not become the
    interpreter's own failure code.

Hermetic: no service, no bus, no model server, no network. Liveness and the
reply are supplied by the same two patches the contract's own cases use.
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


def _run(command, reply: str, cache_dir: Path):
    """Run one command against an owner of the bus name that answers with the
    raw string ``reply``.

    Returns the exit code (None when the command did not exit), the exception
    that escaped the command instead (None when none did), what the person saw
    and what went to the error stream. The escaping exception is caught rather
    than allowed to propagate so that this case reports the behaviour with an
    assertion instead of an error."""
    out, err = io.StringIO(), io.StringIO()
    code = None
    escaped: BaseException | None = None
    with mock.patch.object(cli, "daemon_has_owner", return_value=True), \
            mock.patch.object(cli, "try_dbus", return_value=reply), \
            mock.patch.object(cli, "_last_answer_path",
                              return_value=cache_dir / "last-answer.json"), \
            mock.patch.object(cli, "_AskFillers", _NoFillers, create=True):
        with redirect_stdout(out), redirect_stderr(err):
            try:
                command("is my disk encrypted?")
            except SystemExit as exc:
                code = exc.code
            except BaseException as exc:  # noqa: BLE001 — the defect itself
                escaped = exc
    return code, escaped, out.getvalue(), err.getvalue()


class _ReplyThatCannotBeRead:
    """The two shapes of unreadable reply, named once."""

    NOT_JSON = "not json at all"
    JSON_BUT_NOT_AN_OBJECT = "[]"


class AReplyThatCannotBeReadStillAnswersTheExitCode(unittest.TestCase):
    """Both commands, both shapes, in process."""

    def _assert_the_contract(self, command, reply: str) -> None:
        with TemporaryDirectory() as tmp:
            code, escaped, _shown, said = _run(command, reply, Path(tmp))
        self.assertIsNone(
            escaped,
            f"{command.__name__} let {type(escaped).__name__} out of the "
            f"command on a reply of {reply!r} ({escaped}); a reply that cannot "
            f"be read is a question with no answer and must leave through the "
            f"exit code, not through an exception")
        self.assertEqual(
            code, 2,
            f"{command.__name__} must exit 2 on a reply of {reply!r} — the "
            f"number the same no-answer condition carries in every other case "
            f"of this contract")
        self.assertTrue(
            said.strip(),
            f"{command.__name__} must say on the error stream why the question "
            f"went unanswered, as its other no-answer paths do")

    def test_a_reply_that_is_not_json_exits_two_and_says_so(self) -> None:
        self._assert_the_contract(cli.cmd_ask,
                                  _ReplyThatCannotBeRead.NOT_JSON)

    def test_the_frontier_command_does_the_same_with_that_reply(self) -> None:
        self._assert_the_contract(cli.cmd_ask_frontier,
                                  _ReplyThatCannotBeRead.NOT_JSON)

    def test_a_reply_that_is_json_but_not_an_object_exits_two(self) -> None:
        self._assert_the_contract(
            cli.cmd_ask, _ReplyThatCannotBeRead.JSON_BUT_NOT_AN_OBJECT)

    def test_the_frontier_command_does_the_same_with_that_one(self) -> None:
        self._assert_the_contract(
            cli.cmd_ask_frontier,
            _ReplyThatCannotBeRead.JSON_BUT_NOT_AN_OBJECT)

    def test_a_readable_reply_is_unaffected(self) -> None:
        """The control: the same harness, a reply the command can read.

        Without this the four cases above could be failing because the harness
        is wrong rather than because the commands are."""
        with TemporaryDirectory() as tmp:
            code, escaped, shown, _said = _run(
                cli.cmd_ask,
                json.dumps({"response": "Yes — the root filesystem is "
                                        "encrypted.", "handled": True}),
                Path(tmp))
        self.assertIsNone(escaped, f"the harness itself raised: {escaped}")
        self.assertIsNone(code, "an answered question must not exit at all")
        self.assertIn("root filesystem is encrypted", shown)


class TheExitCodeAScriptReadsIsTheSameNumber(unittest.TestCase):
    """Measured at process level, where the claim is about what a caller reads.

    A script, a timer unit or a shell conditional reads the process's exit
    status, and an exception that leaves the command makes that status the
    interpreter's own 1 — indistinguishable from any other crash, and a
    different number from the 2 the same condition produces when the reply is
    readable. The child runs the command directly rather than through main(),
    so it configures no logging and writes nothing outside its own temporary
    directory."""

    @staticmethod
    def _child(reply: str) -> tuple[int, str]:
        import subprocess
        import sys as _sys

        tree = str(Path(__file__).resolve().parents[2])
        program = (
            "import sys, tempfile, pathlib\n"
            "sys.path.insert(0, %r)\n"
            "from unittest import mock\n"
            "from intergen import cli\n"
            "tmp = pathlib.Path(tempfile.mkdtemp())\n"
            "with mock.patch.object(cli, 'daemon_has_owner', return_value=True), \\\n"
            "        mock.patch.object(cli, 'try_dbus', return_value=%r), \\\n"
            "        mock.patch.object(cli, '_last_answer_path',\n"
            "                          return_value=tmp / 'last-answer.json'):\n"
            "    cli.cmd_ask('is my disk encrypted?')\n" % (tree, reply))
        done = subprocess.run(
            [_sys.executable, "-c", program],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=120, check=False)
        return done.returncode, done.stdout.decode()

    def test_the_unreadable_reply_is_not_the_interpreters_failure_code(self) -> None:
        readable, said_readable = self._child(
            json.dumps({"response": "", "handled": True}))
        self.assertEqual(readable, 2,
                         "the control: a readable reply that answered nothing "
                         f"exits 2 — got {readable}: {said_readable}")
        unreadable, said = self._child(_ReplyThatCannotBeRead.NOT_JSON)
        self.assertNotIn("Traceback", said,
                         "a reply that cannot be read must not reach the "
                         f"person as a traceback: {said}")
        self.assertEqual(unreadable, readable,
                         "the same condition — no answer reached the person — "
                         "must carry the same exit code whether the reply "
                         f"could be read or not; got {unreadable} for the "
                         f"reply that could not be read and {readable} for the "
                         "one that could")


if __name__ == "__main__":
    unittest.main()
