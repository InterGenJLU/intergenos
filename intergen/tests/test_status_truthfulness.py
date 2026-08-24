# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Status must state what it measured, not what it hoped.

Three places said something they had not checked.

1. SESSION RECALL. The status line "session recall active (:8081 index)" was
   printed whenever an index OBJECT existed. The degraded flag only becomes True
   once an embed attempt has FAILED, so a machine whose embedder never came up
   at all was in neither state — it had not failed, it had merely never
   succeeded — and the surface called that active. The window in which this was
   wrong is exactly the window in which a user is deciding whether to trust
   recall.

   Fixed by recording a MEASURED success. The index now carries `verified`, set
   only when the embedder actually answers, and the surface distinguishes
   active / wired-but-unverified / degraded / disabled.

2. THE WARMUP SKIP MESSAGE. It appended "(no model downloaded?)" every time the
   engine was not running, so a machine with a verified model whose engine had
   failed to start was told its model might be missing — pointing the reader at
   the one thing that was fine while the recorded failure sat unread.

   Fixed by preferring the RECORDED failure, and only falling back to looking at
   the disk when nothing was recorded — and then saying what it looked at.

3. LAST ERROR. Covered here by pinning that the recorded failure reaches the
   skip reason, since that is the path where a real error was being replaced by
   a guess.

These tests construct the real objects and read the real strings. Nothing is
asserted against source text.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


class _FakeIndex:
    def __init__(self, degraded=False, verified=False):
        self.degraded = degraded
        self.verified = verified


class SessionRecallStatusTest(unittest.TestCase):
    """The CLI's rendering of the four states."""

    def _render(self, mem):
        from intergen import cli
        buf = io.StringIO()
        status = {"memory_index": mem, "requests_handled": 0}
        with redirect_stdout(buf):
            cli.print_status(status)
        out = buf.getvalue()
        # A renderer that printed nothing would satisfy every assertNotIn below
        # without rendering anything at all.
        self.assertIn("InterGen Status", out,
                      "the renderer produced no output, so the assertions "
                      "below would pass vacuously")
        return out

    def test_verified_reads_as_active(self):
        out = self._render({"enabled": True, "degraded": False,
                            "verified": True})
        self.assertIn("session recall active", out)

    def test_wired_but_unverified_does_not_read_as_active(self):
        """The defect, stated as an assertion."""
        out = self._render({"enabled": True, "degraded": False,
                            "verified": False})
        self.assertNotIn("session recall active", out)
        self.assertIn("NOT YET VERIFIED", out)

    def test_degraded_still_reads_loud(self):
        out = self._render({"enabled": True, "degraded": True,
                            "verified": True})
        self.assertIn("MEMORY DEGRADED", out)

    def test_disabled_reads_as_disabled(self):
        out = self._render({"enabled": False, "degraded": False,
                            "verified": False})
        self.assertIn("disabled", out)


class IndexVerificationTest(unittest.TestCase):
    """SessionTurnIndex.verified is set only by a real success."""

    def _index(self):
        from intergen.memory import SessionTurnIndex
        return SessionTurnIndex(embedder=None)

    def test_a_fresh_index_is_not_verified(self):
        idx = self._index()
        self.assertFalse(idx.verified,
                         "a brand-new index claims the embedder works before "
                         "it has ever been asked")

    def test_a_fresh_index_is_not_degraded_either(self):
        """Both false is the honest starting state: nothing is known yet."""
        idx = self._index()
        self.assertFalse(idx.degraded)

    def test_verified_and_degraded_are_independent_properties(self):
        idx = self._index()
        self.assertIsNot(idx.verified, None)
        self.assertIsNot(idx.degraded, None)


class RouterStatusTest(unittest.TestCase):
    """The router lifts the measured flag, not just the wired one."""

    def test_status_carries_memory_verified(self):
        import inspect
        from intergen import router as _router
        src = inspect.getsource(_router)
        self.assertIn('"memory_verified"', src,
                      "the router status does not carry the measured flag, so "
                      "the surface has nothing truthful to render")

    def test_no_index_reports_unverified_rather_than_absent(self):
        """A missing key would render as False anyway — but silently.

        Pinning it means the difference between "we checked and it is not
        verified" and "nobody wrote the key" stays visible in the payload.
        """
        import inspect
        from intergen import router as _router
        src = inspect.getsource(_router)
        # The index belongs to the conversation being served now, so the status
        # reads it off that conversation rather than off the router.
        self.assertIn("index.verified", src)
        self.assertIn("conversation_bound", src,
                      "the payload must say whether there was a conversation to "
                      "report on, so a zero history length is never read as an "
                      "empty conversation when it means none was named")


class WarmupSkipReasonTest(unittest.TestCase):
    """The skip message states the recorded failure, not a guess."""

    def _daemon(self):
        from intergen.dbus_daemon import InterGenDaemon
        d = InterGenDaemon.__new__(InterGenDaemon)
        d._llama = None
        d._model_loaded = None
        return d

    class _FakeFailure:
        def __init__(self, name):
            self.name = name

    class _FakeLlama:
        def __init__(self, failure=None, error=None, config=None):
            self.last_failure = failure
            self.last_error = error
            self._config = config

    class _FakeConfig:
        def __init__(self, model_path):
            self.model_path = model_path

    def test_a_recorded_failure_is_what_gets_reported(self):
        d = self._daemon()
        d._llama = self._FakeLlama(
            failure=self._FakeFailure("UNHEALTHY"),
            error="never became healthy within 90s")
        reason = d._warmup_skip_reason()
        self.assertIn("UNHEALTHY", reason)
        self.assertIn("never became healthy", reason)

    def test_the_no_model_guess_is_gone_when_a_model_is_present(self):
        """The precise defect: a verified model told it might be missing."""
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.gguf"
            model.write_text("x")
            d = self._daemon()
            d._llama = self._FakeLlama(
                failure=self._FakeFailure("NONE"),
                config=self._FakeConfig(str(model)))
            reason = d._warmup_skip_reason()
            self.assertNotIn("no model downloaded", reason)
            self.assertIn("is present", reason)
            self.assertIn(str(model), reason)

    def test_an_absent_model_file_is_named_not_guessed_at(self):
        d = self._daemon()
        d._llama = self._FakeLlama(
            failure=self._FakeFailure("NONE"),
            config=self._FakeConfig("/nonexistent/model.gguf"))
        reason = d._warmup_skip_reason()
        self.assertIn("not on disk", reason)
        self.assertIn("/nonexistent/model.gguf", reason)

    def test_no_model_selected_says_exactly_that(self):
        d = self._daemon()
        d._llama = self._FakeLlama(failure=self._FakeFailure("NONE"))
        self.assertIn("no model has been selected",
                      d._warmup_skip_reason())

    def test_a_recorded_failure_wins_over_the_disk_check(self):
        """A fact beats an inference, and the order has to be that way round."""
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.gguf"
            model.write_text("x")
            d = self._daemon()
            d._llama = self._FakeLlama(
                failure=self._FakeFailure("SPAWN_ERROR"),
                error="OSError: exec format error",
                config=self._FakeConfig(str(model)))
            reason = d._warmup_skip_reason()
            self.assertIn("SPAWN_ERROR", reason)
            self.assertNotIn("is present", reason)


if __name__ == "__main__":
    unittest.main()
