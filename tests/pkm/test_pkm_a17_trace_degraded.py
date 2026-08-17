#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""PKM-A17 regression: a failing trace emit warns ONCE, never silently passes.

pkm call sites wrapped every _trace.trace_event in `try: ... except Exception:
pass`, so a sink / lock / serialization fault dropped forensic events — incl.
the hook_fire/hook_done events bracketing privileged subprocesses — with zero
signal. The fix shadows the re-exported trace_event in pkm/_trace.py with a
fail-soft wrapper: it never raises into the caller (so a trace fault can't
break a package op) and never silently swallows every event — the FIRST emit
failure surfaces a one-time 'forensic trace degraded' WARN. 'Trace
unavailable' (shim not importable) stays an un-warned normal condition,
gated by each caller's _TRACE_AVAILABLE.
"""

import io
import unittest
from contextlib import redirect_stderr

import pkm._trace as t


class TraceDegradedWarnOnceTest(unittest.TestCase):

    def setUp(self):
        # warn-once flag is process-global; reset around each case.
        self._orig_raw = t._raw_trace_event
        t._trace_degraded_warned = False

    def tearDown(self):
        t._raw_trace_event = self._orig_raw
        t._trace_degraded_warned = False

    def test_shadow_is_installed(self):
        # pkm._trace.trace_event is the fail-soft wrapper, not the raw
        # shared-module emitter it wraps.
        self.assertIsNot(t.trace_event, t._raw_trace_event)
        self.assertEqual(t.trace_event.__module__, "pkm._trace")

    def test_emit_failure_warns_once_and_never_raises(self):
        calls = []

        def boom(*a, **k):
            calls.append((a, k))
            raise RuntimeError("sink lock fault")

        t._raw_trace_event = boom
        buf = io.StringIO()
        with redirect_stderr(buf):
            # Neither call may raise into the caller.
            t.trace_event("pkm_test_event", x=1)
            t.trace_event("pkm_test_event_2", y=2)
        out = buf.getvalue()
        self.assertEqual(len(calls), 2, "both emits attempted")
        # Surfaced — not a silent pass.
        self.assertIn("forensic trace degraded", out)
        # ...but exactly ONCE (no per-event spam).
        self.assertEqual(out.count("forensic trace degraded"), 1)
        self.assertIn("sink lock fault", out)
        self.assertIn("RuntimeError", out)

    def test_successful_emit_is_silent(self):
        seen = []
        t._raw_trace_event = lambda *a, **k: seen.append((a, k))
        buf = io.StringIO()
        with redirect_stderr(buf):
            t.trace_event("pkm_ok", a=1)
        self.assertEqual(len(seen), 1)
        self.assertEqual(buf.getvalue(), "", "happy path emits no noise")

    def test_note_trace_degraded_warn_once(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            t.note_trace_degraded(RuntimeError("first"))
            t.note_trace_degraded(RuntimeError("second"))
        out = buf.getvalue()
        self.assertEqual(out.count("forensic trace degraded"), 1)
        self.assertIn("first", out)
        self.assertNotIn("second", out)


if __name__ == "__main__":
    unittest.main()
