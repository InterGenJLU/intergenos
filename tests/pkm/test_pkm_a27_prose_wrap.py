#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""PKM-A27/A28 regression: prose wraps with a hanging indent + -q/-v honored.

A27: only 4 of ~22 commands built a Reporter; the rest printed prose via raw
print(), which soft-wraps to column 0 (the "wall of text" the operator hit).
Fix: module-level emit_* helpers (output.py) route prose through _wrap_prose so
every command's prose wraps at WRAP_WIDTH with a 2-space hanging indent.
Columnar output (Reporter.step / file lists / aligned table rows) stays RAW — it
is alignment-sensitive and must NOT be wrapped.

A28: -q/-v were silently ignored by every command except the 4 Reporter ones.
Fix: the CLI sets a process-wide verbosity ONCE (output.set_process_level) and
the emit_* helpers honor it — info/note suppressed at QUIET; done/warn/error
always shown (a scripted caller still gets errors + the one-line outcome).
"""

import io
import unittest

from pkm import output
from pkm.output import (
    QUIET, NORMAL, WRAP_WIDTH,
    set_process_level, emit, emit_info, emit_note, emit_done, emit_warn,
    emit_error, _wrap_prose, Reporter,
)


LONG = ("This is a long prose sentence that very definitely exceeds the wrap "
        "width because it just keeps going and going with many ordinary words "
        "so the wrapping logic must split it across several lines instead of "
        "letting the terminal soft-wrap it to column zero and ruin the indent.")


class _Capture:
    """Swap the module-level process reporter's streams for capture, restore on
    exit. The emit_* helpers all route through output._process_reporter."""

    def __enter__(self):
        self.out = io.StringIO()
        self.err = io.StringIO()
        r = output._process_reporter
        # Save the raw backing attrs (not the resolved properties) so lazy-None
        # is preserved on restore.
        self._saved = (r._stream, r._err_stream, r.level)
        r.stream, r.err_stream = self.out, self.err
        return self

    def __exit__(self, *a):
        r = output._process_reporter
        r._stream, r._err_stream, r.level = self._saved


class WrapProseTest(unittest.TestCase):
    def test_long_prose_wraps_under_width_with_hanging_indent(self):
        lines = _wrap_prose(LONG).split("\n")
        self.assertGreater(len(lines), 1, "long prose should wrap to >1 line")
        for ln in lines:
            self.assertLessEqual(len(ln), WRAP_WIDTH)
            self.assertTrue(ln.startswith("  "), "base 2-space indent on every line")
        # Continuation lines hang two further (base + 2).
        self.assertTrue(lines[1].startswith("    "))

    def test_embedded_newlines_kept_as_separate_blocks(self):
        wrapped = _wrap_prose("first logical line\nsecond logical line")
        nonblank = [l for l in wrapped.split("\n") if l.strip()]
        self.assertEqual(len(nonblank), 2)
        self.assertIn("first logical line", wrapped)
        self.assertIn("second logical line", wrapped)

    def test_long_unbreakable_token_not_split(self):
        url = "https://example.com/" + "a" * 120
        self.assertIn(url, _wrap_prose(f"see {url} for details"))


class EmitLevelTest(unittest.TestCase):
    def test_emit_info_wraps_to_stdout_only(self):
        with _Capture() as cap:
            set_process_level(NORMAL)
            emit_info(LONG)
        out = cap.out.getvalue()
        self.assertTrue(out)
        self.assertEqual(cap.err.getvalue(), "")
        for ln in out.rstrip("\n").split("\n"):
            self.assertLessEqual(len(ln), WRAP_WIDTH)

    def test_quiet_suppresses_info_note_but_keeps_done_warn_error(self):
        with _Capture() as cap:
            set_process_level(QUIET)
            emit_info("info should vanish")
            emit_note("note should vanish")
            emit_done("done stays")
            emit_warn("warn stays")
            emit_error("error stays")
        out, err = cap.out.getvalue(), cap.err.getvalue()
        self.assertNotIn("info should vanish", out)
        self.assertNotIn("note should vanish", out)
        self.assertIn("done stays", out)
        self.assertIn("warn stays", err)
        self.assertIn("error stays", err)

    def test_normal_shows_info(self):
        with _Capture() as cap:
            set_process_level(NORMAL)
            emit_info("visible at normal")
        self.assertIn("visible at normal", cap.out.getvalue())

    def test_warn_and_error_carry_unified_prefixes(self):
        with _Capture() as cap:
            set_process_level(NORMAL)
            emit_warn("bad")
            emit_error("worse")
        err = cap.err.getvalue()
        self.assertIn("warning:", err)
        self.assertIn("error:", err)

    def test_emit_no_prefix_to_stderr(self):
        with _Capture() as cap:
            set_process_level(NORMAL)
            emit("CRITICAL: custom severity", err=True)
        err = cap.err.getvalue()
        self.assertIn("CRITICAL: custom severity", err)
        self.assertNotIn("error:", err)
        self.assertNotIn("warning:", err)


class ColumnarNotWrappedTest(unittest.TestCase):
    """A27 keeps columnar output RAW: Reporter.step (phase/table lines) emits a
    long detail on ONE line — proving tables are not routed through the wrapper."""

    def test_step_detail_is_not_wrapped(self):
        buf = io.StringIO()
        r = Reporter(level=NORMAL, stream=buf)
        long_detail = "x" * 200
        r.step("Deploy", long_detail)
        lines = [l for l in buf.getvalue().split("\n") if l]
        self.assertEqual(len(lines), 1, "columnar step line must not wrap")
        self.assertIn(long_detail, lines[0])


if __name__ == "__main__":
    unittest.main()
