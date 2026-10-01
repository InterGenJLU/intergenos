# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The red check for a stopped inference server says nothing about it.

THE CODE. intergen/health.py's "llama-server process" check builds its one
line of text like this (health.py:88-90):

    summary=f"{'Running' if running else 'Not running'} "
            f"(PID {self._llama.pid or '?'})"
            if running else "",

Two adjacent f-strings are concatenated by the parser BEFORE the trailing
conditional is applied, so the `if running else ""` governs the whole joined
string, not the parenthesised PID tail the author was reaching for. The
expression is therefore

    (f"...Running/Not running... (PID ...)")  if running else  ""

and the inner 'Not running' branch can only be reached when `running` is
True — where the same inner conditional immediately chooses 'Running'. It is
dead text. Every stopped engine gets `summary == ""` beside `status == "red"`.

WHAT A READER SEES. The report is handed straight to the two renderers with
no default text: the console prints `f"  {dot} {name}: {summary}"`
(intergen/console/shell.py:690) and the panel prints
`${dot} ${escapeHTML(check.name)}: ${escapeHTML(check.summary || '')}`
(intergen/web/app.js:717). Both render a red dot, the check's name, a colon,
and nothing — on the one check whose job is to name the failure. The
neighbouring checks in the same layer all carry their failing text
("localhost:8080 unreachable", "Not determined"), which is what the stopped
branch here was written to do too.

WHAT THIS ASSERTS. The behaviour the quoted line promises: a stopped engine's
check reads "Not running (PID ?)". The running case is asserted as well, as
the control — an instrument never shown to register a true positive cannot
be trusted when it registers nothing.

HERMETIC. No service, socket, model server or daemon is touched. The layer's
"API reachable" check opens http://127.0.0.1:8080/health, so
urllib.request.urlopen is replaced with one that raises; the report-level case
replaces the daemon and system layers, whose own probes reach the session bus
and spawn lspci. The model-server layer under test runs unmodified.
"""

from __future__ import annotations

import unittest
from unittest import mock

from intergen.health import HealthAggregator, HealthLayer, quick_health


class _StoppedLlama:
    """A manager that is present and whose server is not running.

    The state the daemon reports for a machine whose model has not been
    downloaded, or whose inference server failed to start — the daemon builds
    the aggregator with this manager either way (dbus_daemon.py:2301).
    """

    pid = None
    context_size = 0
    model_name = ""

    def is_running(self) -> bool:
        return False

    def health(self):
        raise RuntimeError("no server to ask")


class _RunningLlama:
    """The control: a manager whose server is up."""

    pid = 4242
    context_size = 8192
    model_name = "test-model.gguf"

    def is_running(self) -> bool:
        return True

    def health(self):
        return mock.Mock(model_loaded=True)


def _process_check(llama) -> dict:
    """The "llama-server process" check from the Model Server layer."""
    agg = HealthAggregator(llama_manager=llama)
    # The layer's API probe would connect to the inference server's port.
    with mock.patch("urllib.request.urlopen",
                    side_effect=OSError("no socket in a test")):
        layer = agg._check_model_server()
    by_name = {c.name: c for c in layer.checks}
    return by_name["llama-server process"]


class StoppedEngineCheckTests(unittest.TestCase):
    def test_a_running_engine_health_check_says_running_with_the_pid(self):
        # The control. This half of the conditional is reachable and does work.
        check = _process_check(_RunningLlama())
        self.assertEqual(check.status, "green")
        self.assertEqual(check.summary, "Running (PID 4242)")

    def test_a_stopped_engine_health_check_says_not_running(self):
        check = _process_check(_StoppedLlama())
        self.assertEqual(check.status, "red",
                         "a stopped inference server is a red check")
        self.assertIn(
            "Not running", check.summary,
            "the red check for a stopped engine must say so; its summary is "
            f"{check.summary!r}, so the renderers print the check's name, a "
            "colon and nothing")
        self.assertEqual(check.summary, "Not running (PID ?)")


class HealthReportTests(unittest.TestCase):
    """The same value, read where the renderers read it (protocol §8)."""

    def test_the_report_handed_to_the_panel_names_the_stopped_engine(self):
        empty = HealthLayer(name="Daemon", checks=[])
        with mock.patch("urllib.request.urlopen",
                        side_effect=OSError("no socket in a test")), \
             mock.patch.object(HealthAggregator, "_check_daemon",
                               return_value=empty), \
             mock.patch.object(HealthAggregator, "_check_system",
                               return_value=HealthLayer(name="System",
                                                        checks=[])):
            report = quick_health(llama_manager=_StoppedLlama())

        checks = [c for layer in report["layers"]
                  for c in layer["checks"]
                  if c["name"] == "llama-server process"]
        self.assertEqual(len(checks), 1, "expected exactly one process check")
        check = checks[0]
        self.assertEqual(check["status"], "red")
        # Every other failing check in the same layer carries its text; this
        # one is the failure the whole layer exists to report.
        self.assertTrue(
            check["summary"].strip(),
            "the red process check reaches the console and the panel with an "
            f"empty summary ({check['summary']!r}): the line they draw is a "
            "red dot, 'llama-server process', a colon and nothing")


if __name__ == "__main__":
    unittest.main()
