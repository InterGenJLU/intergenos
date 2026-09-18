# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Nothing the memory index writes lands after ``stop()`` has returned.

WHY THIS EXISTS. ``SessionTurnIndex`` embeds each finished exchange on its own
worker thread and writes that exchange's trace rows from there. ``stop()`` set
the stop flag and returned immediately, without waiting for the worker, so a
worker still inside an embed kept running with rows left to write. Where those
rows landed was decided by whatever the record pointed at by then — which, in a
test process, is the NEXT test's record, and on a machine is the record of a
conversation that has already been torn down.

Measured 2026-09-18 on a full suite run at tree 3e394c54c executing
concurrently with a second suite run:
test_an_exchange_indexed_outside_any_turn_is_still_indexed read two "indexed"
rows in its own temporary record where one exchange had been indexed —
"AssertionError: 2 != 1". The same file passed ten of ten re-runs alone. The count was right about what it saw: a row from an index that belonged
to a test that had already finished.

The window is not a test artefact. The worker appends the exchange under the
lock and writes its rows AFTER releasing it, so a caller that waits for the
exchange to be indexed is released one step before the last row is written; any
descheduling in that step moves the row into whatever comes next. Waiting in the
test would only make the test slower to be wrong, so the wait belongs where the
worker is stopped.
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest

import numpy  # noqa: F401  (see below)
import intergen.glass as glass
from intergen.memory import SessionTurnIndex
from intergen.tests import glass_rows

# numpy is imported HERE, in the test process, before any worker runs. The
# index imports it inside the worker thread on the first exchange a process
# embeds, and that first import takes seconds under pytest — long enough to
# swallow the race this file measures and make the test pass for a reason that
# has nothing to do with stop(). Warming it makes the measurement be about what
# the test says it is about.


def _point_the_record_at(tmp: str) -> None:
    """What every test's setUp does: send the trace to this test's own file."""
    os.environ["XDG_STATE_HOME"] = tmp
    os.environ.pop("INTERGEN_GLASS", None)
    glass._glass = None


class TheIndexWorkerIsQuietAfterStop(unittest.TestCase):
    def setUp(self) -> None:
        self._first = tempfile.TemporaryDirectory()
        self._second = tempfile.TemporaryDirectory()
        self.addCleanup(self._first.cleanup)
        self.addCleanup(self._second.cleanup)
        self._prev = os.environ.get("XDG_STATE_HOME")
        self.addCleanup(self._restore)
        _point_the_record_at(self._first.name)

    def _restore(self) -> None:
        glass._glass = None
        if self._prev is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = self._prev

    @staticmethod
    def _release_and_collect(release: threading.Event,
                             index: SessionTurnIndex) -> None:
        """Let the hung embedder answer, then wait for the worker to finish.

        Registered after the index is built, so it runs BEFORE the record is
        restored and the temporary directories are removed (cleanups run in
        reverse registration order): the worker's last row lands in the record
        the exchange belonged to, and no writer survives this test.
        """
        release.set()
        index.stop(timeout=10.0)

    @staticmethod
    def _memory_rows(tmp: str) -> list[dict]:
        return glass_rows.where(glass_rows.read(tmp), phase="memory")

    def test_no_row_of_a_stopped_index_lands_in_the_next_record(self):
        """The whole defect, measured: stop(), then move the record, then look.

        RED AT BASE: stop() returns while the embed is still in flight, and the
        worker's "indexed" row is written into the record that replaced the one
        the exchange belonged to.
        """
        reached_the_embedder = threading.Event()

        def slow_embedder(texts):
            reached_the_embedder.set()
            time.sleep(1.0)
            return [[0.5] * 8 for _ in texts]

        index = SessionTurnIndex(embedder=slow_embedder)
        self.addCleanup(index.stop)
        with glass.turn("turn-in-the-first-record", "dbus"):
            index.index_turn("a question", "an answer")
        self.assertTrue(reached_the_embedder.wait(10.0),
                        "the worker never reached the embedder, so this test "
                        "never arranged the state it measures")

        index.stop()

        # Exactly what the next test's setUp does.
        _point_the_record_at(self._second.name)
        time.sleep(2.0)   # every chance for a late row to land

        strays = self._memory_rows(self._second.name)
        self.assertEqual(
            strays, [],
            "a memory index that has been stopped wrote "
            f"{len(strays)} row(s) into the record that replaced its own: "
            f"{[r.get('event') for r in strays]}. stop() must not return while "
            "the worker still has rows to write, or the row is attributed to "
            "whatever came next.")

    def test_the_exchange_is_still_indexed_into_its_own_record(self):
        """Control: stopping must not cost the exchange its row.

        A stop() that simply killed the in-flight work would pass the test
        above and lose the trace of an exchange that WAS indexed.
        """
        index = SessionTurnIndex(embedder=lambda texts: [[0.5] * 8 for _ in texts])
        self.addCleanup(index.stop)
        with glass.turn("turn-in-the-first-record", "dbus"):
            index.index_turn("a question", "an answer")
        index.stop()
        indexed = glass_rows.where(glass_rows.read(self._first.name),
                                   phase="memory", event="indexed")
        self.assertEqual(
            len(indexed), 1,
            "the exchange handed to the index before stop() left no indexed "
            f"row in its own record; memory rows: "
            f"{[r.get('event') for r in self._memory_rows(self._first.name)]}")
        self.assertEqual(indexed[0].get("turn_id"), "turn-in-the-first-record")

    def test_stop_returns_bounded_when_the_embedder_never_answers(self):
        """An embedder that never answers must not wedge the stop.

        Waiting for the worker is only safe if the wait is bounded and says so
        when it expires: a hung :8081 must cost a stated timeout, never a
        process that will not shut down.
        """
        release = threading.Event()
        reached_the_embedder = threading.Event()

        def hung_embedder(texts):
            reached_the_embedder.set()
            release.wait(60.0)
            return [[0.5] * 8 for _ in texts]

        index = SessionTurnIndex(embedder=hung_embedder)
        # THIS TEST IS THE ONE THAT DELIBERATELY ABANDONS A WORKER, so it is the
        # one that has to collect it. stop(timeout=0.5) below leaves the worker
        # inside the embedder with an "indexed" row still to write. Releasing the
        # embedder at cleanup and returning leaves that write to land wherever the
        # record points by the time it happens: the next test's record (an extra
        # "indexed" row a later test counts as its own), or a temporary directory
        # that has already been deleted — which binds the process-wide trace
        # writer to a path that no longer exists, so the rows of whatever test
        # comes next are dropped with only a log line. Measured on this file
        # alone at tree 426fe5167: 6 of 30 runs red in the control below, "0 != 1".
        # Releasing and then WAITING keeps the abandoned worker inside the test
        # that abandoned it, while its own record is still the one in place.
        self.addCleanup(self._release_and_collect, release, index)
        index.index_turn("a question", "an answer")
        self.assertTrue(reached_the_embedder.wait(10.0),
                        "the worker never reached the embedder, so this test "
                        "never arranged the state it measures")

        started = time.monotonic()
        index.stop(timeout=0.5)
        waited = time.monotonic() - started

        self.assertLess(
            waited, 5.0,
            f"stop() waited {waited:.1f}s on an embedder that never answers; "
            "the wait must be bounded by the timeout it was given")
        gave_up = glass_rows.where(self._memory_rows(self._first.name),
                                   event="index_stop_timeout")
        self.assertEqual(
            len(gave_up), 1,
            "stop() gave up on a worker that was still running and said "
            "nothing about it; a stop that did not actually stop the worker "
            "must be in the record. memory rows: "
            f"{[r.get('event') for r in self._memory_rows(self._first.name)]}")

    def test_stopping_an_idle_index_is_immediate_and_repeatable(self):
        """Control: the common case stays cheap, and stop() twice is not an error."""
        index = SessionTurnIndex(embedder=lambda texts: [[0.5] * 8 for _ in texts])
        started = time.monotonic()
        index.stop()
        index.stop()
        self.assertLess(time.monotonic() - started, 5.0,
                        "stopping an idle index took longer than a stopped "
                        "worker should ever need")


if __name__ == "__main__":
    unittest.main()
