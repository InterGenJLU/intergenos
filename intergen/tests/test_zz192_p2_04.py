# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The command line never hands a terminal a control sequence it was given.

The console already holds this line. `_render_chat` routes an assistant message
by its KNOWN source and renders every model reply as markdown, "with any stray
ESC stripped so model output can never reach the ANSI path" — because a reply
carrying an ESC could otherwise clear the screen, reposition the cursor and
paint a line that reads as an InterGen or a governance line, which is exactly
the spoof that gate exists to prevent.

The command line delivers the same material to the same terminal and applies no
such rule: `_deliver_answer` prints the reply with `print(response)`, `last`
and `last --raw` print the cached answer and the tool output behind it, and
`tool-log` prints recorded excerpts — each one verbatim. The bytes reaching the
terminal decide whether the spoof lands, not the surface that emitted them, so
the rule belongs on both.

Nothing here is an argument for altering the record: `last --raw` is the
verifiable-original affordance, and what is pinned is that the RENDER is safe
while the cache on disk stays byte-exact.

What is pinned here:

  * a reply carrying an ESC is delivered with its words intact and its control
    bytes neutralised, on the path both question commands share;
  * a successful dispatch's own output, carried into the answer when the model
    dropped it, is delivered the same way;
  * `last` and `last --raw` render safely, and the cache file still holds the
    original bytes;
  * `tool-log` renders a recorded excerpt safely.
"""

from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from intergen import cli
from intergen.audit_log import write_record
from intergen.interfaces.provenance import AuditRecord

ESC = "\x1b"
# A screen clear, a cursor home, and a line dressed as the assistant's own.
SPOOF = "\x1b[2J\x1b[H[InterGen] governance: autonomy tier raised to 3."
# A command's real output with a colour run in it, as a terminal-aware tool
# emits and as safety.carry_result_into_answer hands it on verbatim.
TOOL_OUTPUT = "total 4\n\x1b[01;34mdocs\x1b[0m\n\x1b[2J\x1b[H[InterGen] approved."


def _delivered(payload: dict, cache: Path) -> tuple[str, str]:
    """One delivery, with the cache pointed at a scratch file."""
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(cli, "_last_answer_path", return_value=cache):
        with redirect_stdout(out), redirect_stderr(err):
            cli._deliver_answer(payload)
    return out.getvalue(), err.getvalue()


def _last(cached: dict, args: list[str], cache: Path) -> str:
    """`intergen last [--raw]` against a cache file written by hand."""
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(cached))
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(cli, "_last_answer_path", return_value=cache):
        with redirect_stdout(out), redirect_stderr(err):
            cli.cmd_last(args)
    return out.getvalue()


class TheDeliveredAnswerTests(unittest.TestCase):

    def test_the_command_line_strips_terminal_escapes_from_a_model_reply(self):
        with TemporaryDirectory() as d:
            out, _err = _delivered(
                {"response": SPOOF, "handled": True},
                Path(d) / "last-answer.json")
            # The words still reach the person — the control bytes do not.
            self.assertIn("autonomy tier raised to 3", out)
            self.assertNotIn(ESC, out,
                             "the delivered answer carried a terminal control "
                             "sequence to standard output")

    def test_a_carried_tool_result_is_delivered_without_control_bytes(self):
        # The M8-2 repair puts a successful dispatch's own output into the
        # answer verbatim when the model dropped it; that output is the least
        # trusted text on the path.
        with TemporaryDirectory() as d:
            out, _err = _delivered(
                {"response": TOOL_OUTPUT, "handled": True},
                Path(d) / "last-answer.json")
            self.assertIn("docs", out)
            self.assertNotIn(ESC, out,
                             "a carried tool result reached the terminal with "
                             "its control bytes intact")


class TheCachedAnswerTests(unittest.TestCase):

    def test_last_prints_the_cached_answer_without_control_bytes(self):
        with TemporaryDirectory() as d:
            out = _last({"response": SPOOF, "full_output": ""}, [],
                        Path(d) / "last-answer.json")
            self.assertIn("autonomy tier raised to 3", out)
            self.assertNotIn(ESC, out,
                             "`intergen last` replayed a terminal control "
                             "sequence from the cache")

    def test_last_raw_renders_safely_and_the_record_stays_byte_exact(self):
        with TemporaryDirectory() as d:
            cache = Path(d) / "last-answer.json"
            out = _last({"response": "A summary.", "full_output": TOOL_OUTPUT},
                        ["--raw"], cache)
            self.assertIn("docs", out)
            self.assertNotIn(ESC, out,
                             "`intergen last --raw` wrote the tool's control "
                             "bytes to the terminal")
            # The affordance's promise is untouched: the original is still on
            # disk exactly as it was recorded.
            self.assertIn(ESC, json.loads(cache.read_text())["full_output"])


class TheAuditLogTests(unittest.TestCase):

    def test_tool_log_prints_a_recorded_excerpt_without_control_bytes(self):
        with TemporaryDirectory() as d:
            log = Path(d) / "intergen" / "tool-dispatch.jsonl"
            # The record is written through the audit log's own writer, as a
            # dispatch writes it, so the row carries every field the writer
            # serializes and the reader finds what the writer leaves: a row
            # assembled by hand here would pin this case to the record shape
            # of the day it was written, and a reader that checks the record
            # against its writer would refuse the row before any excerpt is
            # rendered.
            self.assertTrue(write_record(AuditRecord(
                timestamp="2026-09-30T12:00:00",
                tool_name="run_command",
                arguments={},
                declared_provenance="model",
                effective_provenance="model",
                ingress_tools_this_turn=[],
                user_decision="approved",
                exit_code=0,
                excerpt=TOOL_OUTPUT,
            ), log))
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": d}):
                with redirect_stdout(out), redirect_stderr(err):
                    cli.cmd_tool_log([])
            printed = out.getvalue()
            self.assertIn("run_command", printed)
            self.assertNotIn(ESC, printed,
                             "`intergen tool-log` replayed a recorded "
                             "excerpt's control bytes to the terminal")


if __name__ == "__main__":
    unittest.main()
