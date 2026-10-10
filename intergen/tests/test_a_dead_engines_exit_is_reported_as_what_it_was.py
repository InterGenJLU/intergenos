# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A chat engine that dies before it answers is reported as what happened to
it, never as a timeout.

THE DEFECT THIS PINS. When the engine's child exited before answering /health,
the health wait recorded its exit, and the start then wrote "Server failed to
become healthy within timeout" over it. An engine killed by a signal within
seconds of its launch (measured: signal 6 at the vision projector on a 4 GB
card, seven times per daemon start) therefore read, in the log and in the
daemon's model_server_down, as a timeout it never reached.

THE RULE THESE CELLS HOLD. A child that exited is recorded with its signal (by
number and name) or its exit status, how long after its launch, and the
engine's own last line: the line naming exhausted device memory when there is
one, which is the line the step-down reads, else whatever it printed last.
The engine's output follows those words in the record, from its end, as it
did before: the line that says why is often not the last one. The timeout
text is kept for a child still alive at the deadline. The failure class stays
UNHEALTHY, so the step-down and the restart ladder behave as before.

The engine in these cells is a stand-in script on a free port, launched
through the real start; it opens no card whatever layer count it is given (a
processor-only start would swap in the installed engine, so the cells ask for
one layer and name no device). The one that comes up is a small server that
answers /health as the engine does.
"""
from __future__ import annotations

import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import pytest

from intergen import llama_manager
from intergen.interfaces.types import StartFailure
from intergen.llama_manager import LlamaManager
from intergen.tests.test_llama_launch import _free_port

TIMEOUT_TEXT = "Server failed to become healthy within timeout"
LAST_LINE = "llama.cpp/ggml/src/ggml-backend.cpp:1234: GGML_ASSERT(buffer) failed"

# Dies by a signal well inside a second, after printing the engine's kind of
# last line. SIGKILL rather than the measured SIGABRT, so no core is ever
# written; the words for signal 6 are pinned on their own below.
DIES_BY_SIGNAL = f"""#!/bin/sh
echo 'load_tensors: loading model tensors' >&2
echo '{LAST_LINE}' >&2
kill -KILL $$
"""

# Lives past the deadline and never answers.
NEVER_ANSWERS = """#!/bin/sh
exec sleep 60
"""

# Answers /health as the engine does, on the port it is given.
COMES_UP = f"""#!{sys.executable} -I
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
port = int(sys.argv[sys.argv.index("--port") + 1])
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({{"status": "ok"}}).encode()
        self.send_response(200 if self.path == "/health" else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass
HTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""


class _StartHarness(unittest.TestCase):
    """A real LlamaManager whose start launches a stand-in engine script.

    The start's health wait asks the stand-in's own port, so every case built
    on this harness is marked as reaching a server it starts itself."""

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="dead-engine-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.model = self.dir / "model.gguf"
        self.model.write_bytes(b"GGUF")
        self.mgr = LlamaManager()
        self.addCleanup(self.mgr.stop)

    def engine(self, body: str) -> str:
        path = self.dir / "llama-server"
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return str(path)

    def start(self, body: str, budget: float = 3.0) -> tuple[bool, float]:
        began = time.monotonic()
        with mock.patch.object(llama_manager, "startup_budget_seconds",
                               return_value=budget):
            ok = self.mgr.start(str(self.model), port=_free_port(),
                                gpu_layers=1, server_path=self.engine(body))
        return ok, time.monotonic() - began


@pytest.mark.reaches_a_server_it_starts_itself
class AnEngineThatDiesIsReportedAsWhatHappened(_StartHarness):

    def test_an_engine_killed_by_a_signal_is_reported_as_that_signal(self):
        with self.assertLogs("intergen.llama_manager", level="ERROR") as logs:
            ok, took = self.start(DIES_BY_SIGNAL, budget=30.0)
        self.assertFalse(ok)
        error = self.mgr.last_error or ""
        self.assertNotIn(TIMEOUT_TEXT, error)
        self.assertIn("was killed by signal 9 (SIGKILL)", error)
        self.assertIn(LAST_LINE, error)
        self.assertLess(took, 15.0, "the start waited for a deadline over an "
                                    "engine that had already died")
        self.assertFalse(any(TIMEOUT_TEXT in line for line in logs.output),
                         logs.output)
        self.assertTrue(any("was killed by signal 9" in line
                            for line in logs.output), logs.output)

    def test_the_failure_class_the_ladder_reads_is_unchanged(self):
        ok, _took = self.start(DIES_BY_SIGNAL, budget=30.0)
        self.assertFalse(ok)
        self.assertIs(self.mgr.last_failure, StartFailure.UNHEALTHY)

    def test_an_engine_that_exits_with_a_status_is_reported_with_it(self):
        ok, _took = self.start(
            f"#!/bin/sh\necho '{LAST_LINE}' >&2\nexit 3\n", budget=30.0)
        self.assertFalse(ok)
        error = self.mgr.last_error or ""
        self.assertNotIn(TIMEOUT_TEXT, error)
        self.assertIn("exited with status 3", error)
        self.assertIn(LAST_LINE, error)

    def test_an_exit_found_only_after_the_deadline_is_still_an_exit(self):
        # The wait takes its last look while the child still lives, and the
        # child dies before the start reads the result: the start must not
        # call that a timeout either.
        def last_look_before_the_death(manager, port):
            manager._process.wait(timeout=30)
            return False

        with mock.patch.object(LlamaManager, "_wait_for_healthy",
                               last_look_before_the_death):
            ok, _took = self.start(DIES_BY_SIGNAL)
        self.assertFalse(ok)
        error = self.mgr.last_error or ""
        self.assertNotIn(TIMEOUT_TEXT, error)
        self.assertIn("was killed by signal 9 (SIGKILL)", error)
        self.assertIn(LAST_LINE, error)


@pytest.mark.reaches_a_server_it_starts_itself
class TheControls(_StartHarness):

    def test_an_engine_alive_and_silent_at_the_deadline_is_a_timeout(self):
        ok, _took = self.start(NEVER_ANSWERS, budget=2.0)
        self.assertFalse(ok)
        self.assertEqual(self.mgr.last_error, TIMEOUT_TEXT)
        self.assertIs(self.mgr.last_failure, StartFailure.UNHEALTHY)

    def test_an_engine_that_comes_up_is_serving(self):
        ok, _took = self.start(COMES_UP, budget=30.0)
        self.assertTrue(ok, self.mgr.last_error)
        self.assertTrue(self.mgr.is_running())
        self.assertIs(self.mgr.last_failure, StartFailure.NONE)


class TheRecordKeepsTheEnginesOutput(unittest.TestCase):

    def test_a_line_above_the_last_is_still_in_the_record(self):
        # The engine names the reason, then prints more on its way out; the
        # record must keep both, as it did before the exit was worded.
        child = subprocess.Popen(
            ["/bin/sh", "-c", "echo 'llama_model_load: error loading model: "
             "failed to load model' >&2; echo 'main: exiting due to model "
             "loading error' >&2; exit 1"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: [s.close() for s in (child.stdout, child.stderr)
                                 if s])
        child.wait(timeout=30)
        mgr = LlamaManager()
        mgr._process = child
        mgr._startup_budget = 1
        self.assertFalse(mgr._wait_for_healthy(port=9))
        self.assertIn("failed to load model", mgr.last_error or "")
        self.assertIn("main: exiting due to model loading error",
                      mgr.last_error or "")


class TheWordsForTheMeasuredDeath(unittest.TestCase):

    def test_signal_six_is_named(self):
        self.assertEqual(llama_manager._exit_words(-signal.SIGABRT),
                         "was killed by signal 6 (SIGABRT)")

    def test_the_device_memory_line_is_the_engines_last_words(self):
        dying = ("ggml_vulkan: Device memory allocation of size 636080128 "
                 "failed.\nllama_model_load: error loading model\n")
        self.assertIn("636080128", llama_manager._engine_last_words(dying))


if __name__ == "__main__":
    unittest.main()
