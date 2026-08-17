# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""embed()-while-down logs once per episode, never repeating at ERROR.

Post-install eval finding: the pre-onboarding window (embedding server
deliberately held down) produced `embed() called but embedding server is
not running` at ERROR on every call — a by-design state repeated as an
alarm. Now: one INFO before the first start (the onboarding window), one
WARNING when a previously-started server is down, repeats at DEBUG only.
"""

from __future__ import annotations

import logging
import unittest

from intergen.llama_manager import LlamaManager


class TestEmbedDownLogNoise(unittest.TestCase):

    def setUp(self):
        self.mgr = LlamaManager()

    def _embed_calls(self, n):
        records = []
        logger = logging.getLogger("intergen.llama_manager")
        handler = logging.Handler()
        handler.emit = records.append
        logger.addHandler(handler)
        old_level = logger.level
        logger.setLevel(logging.DEBUG)
        try:
            for _ in range(n):
                self.assertIsNone(self.mgr.embed(["text"]))
        finally:
            logger.setLevel(old_level)
            logger.removeHandler(handler)
        return records

    def test_pre_start_window_logs_single_info(self):
        records = self._embed_calls(6)
        non_debug = [r for r in records if r.levelno > logging.DEBUG]
        self.assertEqual(len(non_debug), 1, [r.getMessage() for r in records])
        self.assertEqual(non_debug[0].levelno, logging.INFO)
        self.assertIn("onboarding", non_debug[0].getMessage())
        # Repeats are present, but only at DEBUG.
        self.assertEqual(
            len([r for r in records if r.levelno == logging.DEBUG]), 5)

    def test_previously_started_logs_single_warning(self):
        self.mgr._start_time = 12345.0  # a start happened this lifetime
        records = self._embed_calls(4)
        non_debug = [r for r in records if r.levelno > logging.DEBUG]
        self.assertEqual(len(non_debug), 1, [r.getMessage() for r in records])
        self.assertEqual(non_debug[0].levelno, logging.WARNING)

    def test_never_error_level(self):
        records = self._embed_calls(3)
        self.assertFalse(
            [r for r in records if r.levelno >= logging.ERROR],
            "embed()-while-down must never log at ERROR")


if __name__ == "__main__":
    unittest.main()
