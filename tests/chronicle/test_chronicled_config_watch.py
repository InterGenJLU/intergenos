#!/usr/bin/env python3
"""The configuration watcher retries failed captures and reports them."""

import contextlib
import importlib.util
import io
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock


def _load_chronicled():
    assets = Path(__file__).resolve().parents[2] / "assets" / "intergenos-backup"
    loader = SourceFileLoader("chronicled_watch", str(assets / "chronicled"))
    spec = importlib.util.spec_from_loader("chronicled_watch", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class _StopLoop(BaseException):
    pass


class _FailOnceEngine:
    def __init__(self):
        self.capture_calls = 0

    def capture(self, *_args, **_kwargs):
        self.capture_calls += 1
        if self.capture_calls == 1:
            raise RuntimeError("injected capture failure")
        return {"version_id": "captured"}


class ChronicledConfigWatchTest(unittest.TestCase):
    def test_failed_capture_is_reported_and_retried_until_success(self):
        chronicled = _load_chronicled()
        engine = _FailOnceEngine()
        fingerprints = [(1, 1), (2, 1), (2, 1), (2, 1)]
        sleeps = [None, None, None, _StopLoop()]
        stderr = io.StringIO()

        with mock.patch.object(
            chronicled._sentinel,
            "config_set_fingerprint",
            side_effect=fingerprints,
        ), mock.patch.object(
            chronicled.time, "sleep", side_effect=sleeps
        ), contextlib.redirect_stderr(stderr):
            with self.assertRaises(_StopLoop):
                chronicled._config_watch_loop(engine, interval=0)

        self.assertEqual(
            engine.capture_calls,
            2,
            "one failed attempt must be retried, then stop repeating on success",
        )
        self.assertIn("injected capture failure", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
