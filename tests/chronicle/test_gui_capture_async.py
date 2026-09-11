#!/usr/bin/env python3
"""Capture transport leaves the GTK main loop responsive."""

import contextlib
import threading
import time
import unittest
import warnings
from types import MethodType

from chronicle import gui as _gui
from chronicle import paths as _paths


class _DelayedEngine:
    def __init__(self, delay=0.15):
        self.delay = delay
        self.layers = []
        self.thread_ids = []
        self.active = 0
        self.max_active = 0
        self.finished_at = None
        self.lock = threading.Lock()

    def call(self, verb, **args):
        if verb != "capture":
            raise AssertionError(f"unexpected verb {verb}")
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.layers.append(args["layer"])
            self.thread_ids.append(threading.get_ident())
        try:
            time.sleep(self.delay)
            return {"version_id": f"version-{args['layer']}"}
        finally:
            with self.lock:
                self.active -= 1
                self.finished_at = time.monotonic()


class _WindowHarness:
    def __init__(self, engine):
        self.engine = engine
        self.toasts = []
        self.render_threads = []
        self.refreshes = 0
        self.sensitivity = []
        self._capture_active = False
        self._capture_queue = []
        self._capture_thread = None

    def __getattr__(self, name):
        method = getattr(_gui.ChronicleWindow, name)
        return MethodType(method, self)

    def _toast(self, message):
        self.toasts.append(message)
        self.render_threads.append(threading.get_ident())

    def _refresh_overview(self):
        self.refreshes += 1
        self.render_threads.append(threading.get_ident())

    def _set_capture_enabled(self, enabled, state=None):
        self.sensitivity.append((enabled, state))


def _drain_main_context_until(predicate, timeout=3):
    context = _gui.GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while not predicate():
        while context.pending():
            context.iteration(False)
        if time.monotonic() >= deadline:
            raise AssertionError("timed out waiting for GTK-main-context work")
        time.sleep(0.005)
    while context.pending():
        context.iteration(False)


@contextlib.contextmanager
def _ignore_pygobject_python314_deprecations():
    """PyGObject still probes event-loop APIs deprecated by Python 3.14."""
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=".*asyncio.AbstractEventLoopPolicy.*deprecated.*",
            category=DeprecationWarning,
        )
        warnings.filterwarnings(
            "ignore",
            message=".*asyncio.get_event_loop_policy.*deprecated.*",
            category=DeprecationWarning,
        )
        yield


class GuiCaptureAsyncTest(unittest.TestCase):
    def test_capture_runs_off_thread_while_glib_heartbeat_continues(self):
        engine = _DelayedEngine()
        window = _WindowHarness(engine)
        main_thread = threading.get_ident()
        heartbeats = []
        with _ignore_pygobject_python314_deprecations():
            source = _gui.GLib.timeout_add(
                10, lambda: heartbeats.append(time.monotonic()) or True
            )
            try:
                started = time.monotonic()
                _gui.ChronicleWindow._on_capture(
                    window, None, _paths.LAYER_CONFIG_STATE
                )
                returned_after = time.monotonic() - started
                _drain_main_context_until(lambda: bool(window.toasts))
            finally:
                _gui.GLib.source_remove(source)

        self.assertLess(returned_after, 0.05)
        self.assertNotEqual(engine.thread_ids, [main_thread])
        self.assertTrue(
            any(stamp < engine.finished_at for stamp in heartbeats),
            "the GLib heartbeat must run before the transport finishes",
        )
        self.assertTrue(window.render_threads)
        self.assertEqual(set(window.render_threads), {main_thread})

    def test_capture_all_runs_layers_sequentially_off_thread(self):
        engine = _DelayedEngine(delay=0.05)
        window = _WindowHarness(engine)
        main_thread = threading.get_ident()
        started = time.monotonic()

        with _ignore_pygobject_python314_deprecations():
            _gui.ChronicleWindow._on_capture_all(window, None)
            returned_after = time.monotonic() - started
            _drain_main_context_until(
                lambda: len(engine.layers) == 2 and not window._capture_active
            )

        self.assertLess(returned_after, 0.05)
        self.assertEqual(
            engine.layers,
            [_paths.LAYER_CONFIG_STATE, _paths.LAYER_USER_DATA],
        )
        self.assertEqual(engine.max_active, 1)
        self.assertTrue(all(thread != main_thread for thread in engine.thread_ids))


if __name__ == "__main__":
    unittest.main()
