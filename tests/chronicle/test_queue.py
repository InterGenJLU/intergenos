#!/usr/bin/env python3
"""Durable capture-intent queue: intents persist across a simulated restart, a
drain runs and removes them, and status reports the pending count (spec §5)."""

import tempfile
import unittest

from chronicle import queue as _queue


class QueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chronicle-queue-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _intent(self, layer="user-data", reason="big change", est=2_000_000_000):
        return {"layer": layer, "scope": None, "reason": reason,
                "trigger_time": 1_000_000, "estimate": est}

    def test_persists_across_restart(self):
        q = _queue.Queue(self.tmp)
        iid = q.enqueue(self._intent())
        self.assertEqual(q.count(), 1)
        # Simulate a restart: a brand-new Queue over the same root sees it.
        q2 = _queue.Queue(self.tmp)
        self.assertEqual(q2.count(), 1)
        self.assertIsNotNone(q2.get(iid))

    def test_status_summary_reports_pending(self):
        q = _queue.Queue(self.tmp)
        q.enqueue(self._intent())
        q.enqueue(self._intent(reason="another"))
        summary = q.status_summary(work_window_text="09:00-18:00")
        self.assertIn("2 changes queued", summary)
        self.assertIn("09:00-18:00", summary)
        # Idle queue -> empty line.
        empty = _queue.Queue(tempfile.mkdtemp(prefix="chronicle-queue-idle-"))
        self.assertEqual(empty.status_summary(), "")

    def test_drain_runs_and_removes(self):
        q = _queue.Queue(self.tmp)
        q.enqueue(self._intent())
        q.enqueue(self._intent(reason="two"))
        ran = []
        q.drain(run_intent=lambda intent: ran.append(intent["reason"]) or True)
        self.assertEqual(len(ran), 2)
        self.assertEqual(q.count(), 0, "successful drain empties the queue")

    def test_drain_keeps_intent_on_failure(self):
        q = _queue.Queue(self.tmp)
        q.enqueue(self._intent())

        def failing(_intent):
            return False  # e.g. target absent -> quiet skip, intent stays

        q.drain(run_intent=failing)
        self.assertEqual(q.count(), 1, "a non-completed intent stays queued")

    def test_remove(self):
        q = _queue.Queue(self.tmp)
        iid = q.enqueue(self._intent())
        q.remove(iid)
        self.assertEqual(q.count(), 0)


if __name__ == "__main__":
    unittest.main()
