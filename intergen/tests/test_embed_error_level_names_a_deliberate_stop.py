# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A deliberate stop of the embedding server is not an embedding failure.

Measured on an installed machine 2026-09-19, during a restart taken while the
background documentation-embedding pass had a request in flight:

    intergen.llama_manager ERROR embed() request failed: Remote end closed
    connection without response
    intergen.wiki_retrieval WARNING wiki-retrieval: embedding request for
    passages 32-63 returned nothing usable; ...

Nothing had gone wrong. The daemon had stopped its own embedding server on
purpose, and the request in flight died with it. An ERROR line that is routine
teardown noise teaches a reader to skim ERROR lines, and that is how a real
embedding failure becomes invisible.

A FIRST FIX WAS NOT ENOUGH, and the real daemon said so. Marking the manager
inside its own ``stop()`` covers a stop this code begins, and stopping the
SERVICE is not one: the service manager signals every process in the group, so
the embedding server is gone long before the daemon's own shutdown path runs.
Reproduced on 2026-09-20 under the daemon on this machine — the request's
30-second client timeout expired at 04:08:39,286 and the daemon serviced its
shutdown signal at 04:08:39,659, 373 ms LATER. No flag inside the process could
have been true at the moment the line was written. What was true, and
measurable, is that the child had already exited on SIGTERM.

What is pinned here:

  * a request that fails while there is a server meant to answer it is still
    reported at ERROR — the control, so the level is never merely lowered;
  * a request that fails after ``stop()`` has begun is reported at INFO and the
    message says the server was stopped on purpose;
  * ``stop()`` marks the manager BEFORE it signals the child, because the
    request this covers is already in flight when the stop begins;
  * a request whose server had already exited ON SIGTERM is reported at INFO
    and says so — the measured case, which the marker alone does not reach;
  * a server that died any other way — a crash, a SIGKILL, a non-zero status —
    keeps its ERROR, because that level is exactly for a server that went away
    without being asked;
  * a manager that becomes ready again reports the next failure at ERROR, so
    one deliberate stop cannot silence every later failure;
  * a manager that never carried the mark reports at ERROR — an unknown state
    must never silence a real failure.
"""

from __future__ import annotations

import logging
import signal
import unittest
from unittest import mock

from intergen.llama_manager import LlamaManager


class _Child:
    """The least subprocess.Popen that ``stop()`` can drive.

    ``signalled_while_marked`` records what the stop marker read at the moment
    the child was signalled — the instant a request in flight learns its fate.
    """

    def __init__(self, manager, pid=4242):
        self.pid = pid
        self.stdout = None
        self.stderr = None
        self._manager = manager
        self.signalled_while_marked = None

        self.status = None          # what poll() reports: None = still running

    def send_signal(self, _sig):
        self.signalled_while_marked = getattr(self._manager, "_stopping", None)

    def wait(self, timeout=None):
        return 0

    def kill(self):
        return None

    def poll(self):
        return self.status


class EmbedFailureLevelTests(unittest.TestCase):

    def setUp(self):
        self.mgr = LlamaManager()

    def _failing_request(self):
        """Drive one embedding request whose transport dies, and return the
        records the module logged for it."""
        records = []
        logger = logging.getLogger("intergen.llama_manager")
        handler = logging.Handler()
        handler.emit = records.append
        logger.addHandler(handler)
        old_level = logger.level
        logger.setLevel(logging.DEBUG)
        boom = Exception("Remote end closed connection without response")
        try:
            with mock.patch("urllib.request.urlopen", side_effect=boom):
                self.assertIsNone(
                    self.mgr._embed_one_request(["a passage"], 5.0))
        finally:
            logger.setLevel(old_level)
            logger.removeHandler(handler)
        return records

    def _one_line_about_the_failure(self, records):
        lines = [r for r in records
                 if "Remote end closed connection" in r.getMessage()]
        self.assertEqual(len(lines), 1,
                         [r.getMessage() for r in records])
        return lines[0]

    def test_a_failure_while_the_manager_means_to_serve_is_an_error(self):
        # The control. Without it, an INFO everywhere would pass the test below.
        self.mgr._mark_ready()
        line = self._one_line_about_the_failure(self._failing_request())
        self.assertEqual(line.levelno, logging.ERROR, line.getMessage())

    def test_a_failure_after_a_deliberate_stop_is_an_info_naming_the_stop(self):
        self.mgr.stop()
        line = self._one_line_about_the_failure(self._failing_request())
        self.assertEqual(line.levelno, logging.INFO, line.getMessage())
        self.assertLess(line.levelno, logging.WARNING)
        message = line.getMessage().lower()
        self.assertIn("on purpose", message)

    def test_stop_marks_the_manager_before_it_signals_the_child(self):
        child = _Child(self.mgr)
        self.mgr._process = child
        self.mgr.stop()
        self.assertIs(child.signalled_while_marked, True,
                      "stop() signalled the child before marking the manager, "
                      "so a request already in flight cannot tell that the "
                      "stop was deliberate")

    def test_a_manager_that_is_serving_again_reports_at_error(self):
        self.mgr.stop()
        self.mgr._mark_ready()          # the one place that declares serving
        line = self._one_line_about_the_failure(self._failing_request())
        self.assertEqual(line.levelno, logging.ERROR, line.getMessage())

    def test_a_server_already_gone_on_sigterm_is_an_info_naming_it(self):
        """The measured case: the service was stopped, the group was signalled,
        and this code has not begun its own stop at all."""
        child = _Child(self.mgr)
        child.status = -signal.SIGTERM
        self.mgr._process = child
        self.assertFalse(self.mgr._stopping)     # nothing here asked for it
        line = self._one_line_about_the_failure(self._failing_request())
        self.assertEqual(line.levelno, logging.INFO, line.getMessage())
        self.assertIn("SIGTERM", line.getMessage())

    def test_a_server_that_crashed_keeps_its_error(self):
        for status in (-signal.SIGSEGV, -signal.SIGKILL, 1, 134):
            with self.subTest(status=status):
                mgr = LlamaManager()
                child = _Child(mgr)
                child.status = status
                mgr._process = child
                self.mgr = mgr
                line = self._one_line_about_the_failure(self._failing_request())
                self.assertEqual(line.levelno, logging.ERROR,
                                 line.getMessage())

    def test_a_server_still_running_keeps_its_error(self):
        child = _Child(self.mgr)
        child.status = None                      # still up; it just did not answer
        self.mgr._process = child
        line = self._one_line_about_the_failure(self._failing_request())
        self.assertEqual(line.levelno, logging.ERROR, line.getMessage())

    def test_an_unreadable_exit_status_is_not_read_as_planned(self):
        class _Unreadable(_Child):
            def poll(self_inner):
                raise OSError("no status here")

        self.mgr._process = _Unreadable(self.mgr)
        line = self._one_line_about_the_failure(self._failing_request())
        self.assertEqual(line.levelno, logging.ERROR, line.getMessage())

    def test_a_manager_without_the_mark_reports_at_error(self):
        bare = LlamaManager.__new__(LlamaManager)
        bare._config = None
        self.assertFalse(hasattr(bare, "_stopping"))
        records = []
        logger = logging.getLogger("intergen.llama_manager")
        handler = logging.Handler()
        handler.emit = records.append
        logger.addHandler(handler)
        try:
            with mock.patch("urllib.request.urlopen",
                            side_effect=Exception("Remote end closed "
                                                  "connection without "
                                                  "response")):
                self.assertIsNone(bare._embed_one_request(["a passage"], 5.0))
        finally:
            logger.removeHandler(handler)
        line = self._one_line_about_the_failure(records)
        self.assertEqual(line.levelno, logging.ERROR, line.getMessage())

    def test_the_malformed_response_paths_are_untouched(self):
        # The stop marker covers the TRANSPORT failure only. A server that
        # answers with the wrong shape is a real defect whatever the daemon
        # intends, so those two lines stay at ERROR.
        self.mgr.stop()
        records = []
        logger = logging.getLogger("intergen.llama_manager")
        handler = logging.Handler()
        handler.emit = records.append
        logger.addHandler(handler)

        class _Resp:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *_a):
                return False

            def read(self_inner):
                return b'{"data": []}'

        try:
            with mock.patch("urllib.request.urlopen", return_value=_Resp()):
                self.assertIsNone(
                    self.mgr._embed_one_request(["a passage"], 5.0))
        finally:
            logger.removeHandler(handler)
        rows = [r for r in records if "rows for" in r.getMessage()]
        self.assertEqual(len(rows), 1, [r.getMessage() for r in records])
        self.assertEqual(rows[0].levelno, logging.ERROR)


if __name__ == "__main__":
    unittest.main()
