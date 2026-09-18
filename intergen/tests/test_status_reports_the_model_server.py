# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""`intergen status` must report the MODEL SERVER, not the daemon process.

THE TWO SIGHTINGS THIS FILE REPRODUCES, both on a real machine:

  1. With the engine pinned to cuda and its binary moved aside, the daemon
     refused loudly and correctly — an ERROR naming the absent binary, a
     WARNING that llama-server failed to start, then a line saying the
     selector had chosen vulkan. No chat server ever started: nothing was
     bound on the chat port, only the embedder ran, and the component marker
     read `[-] llama_server`. `intergen status` still printed
     `Running: True` and `Last Error: None`.

  2. The same shape with nothing moved aside at all: after a restart the chat
     port was unbound, status read "Offload: unknown, ? layers on CPU" with
     `Last Error: None`, and the web path logged "Turn failed" on every
     request.

The daemon is not the thing the user cares about. A person runs this command
to learn whether the assistant WORKS, and a daemon process that is alive while
nothing can generate a reply is exactly the silent failure the status line
exists to end. The payload already carried the truth — `model_server_down`
holds the reason and `components.llama_server` is False — and the renderer
threw both away in favour of the daemon's own liveness flag.

So this is a renderer contract, tested against payloads rather than through a
live bus: print_status is pure over its dict by design, for precisely this
reason.
"""
from __future__ import annotations

import io
import contextlib
import unittest

from intergen import cli


def _render(status: dict) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli.print_status(status)
    return buf.getvalue()


def _line(text: str, label: str) -> str:
    for ln in text.splitlines():
        if ln.strip().startswith(label):
            return ln.strip()
    raise AssertionError(f"no {label!r} line in:\n{text}")


# The first sighting, as the daemon's own payload reports it: the daemon is up,
# the chat server is not, and the daemon knows why.
SIGHTING_1 = {
    "running": True,
    "version": "0.1.0",
    "last_error": None,
    "model_server_down": (
        "the chat model server is not running: resolved engine server binary "
        "absent: '/opt/llama-cpp-cuda/bin/llama-server'"),
    "components": {
        "hardware_detector": True,
        "model_manager": True,
        "llama_server": False,
        "router": True,
    },
    "requests_handled": 0,
}

# The second sighting: no reason was recorded anywhere, the offload report is
# the "unknown / CPU" shape, and last_error is still None. The daemon knows
# less here, and the status line must still not claim the assistant works.
SIGHTING_2 = {
    "running": True,
    "version": "0.1.0",
    "last_error": None,
    "model_server_down": "the chat model server is not running: no reason recorded",
    "offload": {"backend": "unknown", "layers_offloaded": None, "on_gpu": False},
    "components": {"llama_server": False},
    "requests_handled": 0,
}

HEALTHY = {
    "running": True,
    "version": "0.1.0",
    "last_error": None,
    "model_server_down": None,
    "components": {"llama_server": True},
    "requests_handled": 3,
}


class RunningReflectsTheModelServerTest(unittest.TestCase):

    def test_sighting_1_does_not_print_running_true(self):
        """The defect verbatim: a bare `Running: True` with no chat server."""
        out = _render(SIGHTING_1)
        self.assertNotIn("Running:    True\n", out)

    def test_sighting_1_says_degraded(self):
        self.assertIn("degraded", _line(_render(SIGHTING_1), "Running:").lower())

    def test_sighting_2_says_degraded_even_with_no_reason_recorded(self):
        """The daemon knowing no reason does not entitle it to claim health."""
        self.assertIn("degraded", _line(_render(SIGHTING_2), "Running:").lower())

    def test_a_healthy_daemon_still_reads_true(self):
        """The gate must not report every machine as degraded — the control."""
        line = _line(_render(HEALTHY), "Running:")
        self.assertIn("True", line)
        self.assertNotIn("degraded", line.lower())

    def test_a_stopped_daemon_still_reads_false(self):
        """The daemon-down path is a different statement and keeps its own."""
        line = _line(_render({"running": False, "daemon_down": True}), "Running:")
        self.assertIn("False", line)


class TheReasonReachesLastErrorTest(unittest.TestCase):

    def test_sighting_1_puts_the_binary_failure_in_last_error(self):
        """The reason the daemon already holds must not stay in the payload.

        `Last Error: None` beside a dead chat server is the line that made a
        real failure look like a healthy machine.
        """
        line = _line(_render(SIGHTING_1), "Last Error:")
        self.assertNotIn("None", line)
        self.assertIn("llama-cpp-cuda/bin/llama-server", line)

    def test_sighting_2_reports_that_no_reason_was_recorded(self):
        """Saying "no reason recorded" is honest; saying None is not."""
        line = _line(_render(SIGHTING_2), "Last Error:")
        self.assertNotIn("None", line)
        self.assertIn("no reason recorded", line)

    def test_a_real_last_error_is_not_displaced_by_the_server_reason(self):
        """Both facts are real; neither may overwrite the other."""
        status = dict(SIGHTING_1, last_error="Router init failed: boom")
        line = _line(_render(status), "Last Error:")
        self.assertIn("Router init failed: boom", line)

    def test_a_healthy_daemon_keeps_its_none(self):
        self.assertIn("None", _line(_render(HEALTHY), "Last Error:"))


class TheOperatorCanSeeWhatToDoTest(unittest.TestCase):
    """A degraded line that does not say what is wrong is half a report."""

    def test_the_degraded_line_names_the_chat_server(self):
        out = _render(SIGHTING_1)
        self.assertIn("chat", out.lower())

    def test_the_logs_hint_is_offered_when_degraded(self):
        """The same hint the daemon-down path gives, for the same reason: the
        journal carries the ERROR and the WARNING the status line summarises."""
        self.assertIn("journalctl", _render(SIGHTING_1))

    def test_no_logs_hint_on_a_healthy_machine(self):
        self.assertNotIn("journalctl", _render(HEALTHY))


class TheOffloadLineDoesNotDescribeAServerThatIsNotThereTest(unittest.TestCase):
    """The second sighting rendered `Offload: unknown, ? layers on CPU`.

    Every word of that is defensible from the record — the backend was not
    known, the layer count was null, and null layers are not GPU layers — and
    together they read as a report about a model being served on the processor.
    Nothing was being served at all. A line describing the offload of a chat
    server that is not running is a statement about nothing, and it is the kind
    of statement a person acts on.
    """

    def test_it_says_the_server_is_not_running_rather_than_on_cpu(self):
        out = _render(SIGHTING_2)
        offload = _line(out, "Offload:")
        self.assertNotIn("on CPU", offload)

    def test_it_does_not_print_a_question_mark_layer_count(self):
        self.assertNotIn("? layers", _line(_render(SIGHTING_2), "Offload:"))

    def test_a_healthy_machine_keeps_its_ordinary_offload_line(self):
        """The control: this must not blank the line on a working machine."""
        healthy = dict(HEALTHY, offload={
            "backend": "CUDA", "offloaded_layers": 33, "total_layers": 33,
            "fully_offloaded": True})
        self.assertIn("CUDA, 33/33 layers on GPU",
                      _line(_render(healthy), "Offload:"))

    def test_a_deliberate_cpu_run_still_reads_as_cpu(self):
        """Serving on the processor by request is a real state and keeps its
        words; only a DOWN server loses the offload claim."""
        cpu = dict(HEALTHY, offload={
            "backend": "CPU", "offloaded_layers": 0, "total_layers": 33,
            "fully_offloaded": False, "cpu_only_by_request": True})
        self.assertIn("on CPU", _line(_render(cpu), "Offload:"))


class PayloadIsUnchangedTest(unittest.TestCase):
    """The renderer reads; it does not rewrite what it was given."""

    def test_rendering_does_not_mutate_the_status_dict(self):
        import copy
        status = copy.deepcopy(SIGHTING_1)
        _render(status)
        self.assertEqual(status, SIGHTING_1)

    def test_an_absent_model_server_down_key_is_not_an_error(self):
        """An older daemon's payload has no such key. It must still render."""
        out = _render({"running": True, "version": "0.1.0",
                       "last_error": None, "requests_handled": 0})
        self.assertIn("Running:", out)


if __name__ == "__main__":
    unittest.main()
