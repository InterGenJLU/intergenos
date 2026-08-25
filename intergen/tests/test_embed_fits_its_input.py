# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The embedding server fits the input the daemon sends it, and is asked only
when it is ready.

Measured on an outside user's installed machine over three days, and reproduced
here against the real engine binary and the real embedding model:

  * every input longer than 512 tokens came back HTTP 500 — "input (799 tokens)
    is too large to process. increase the physical batch size (current batch
    size: 512)". The embedding instance was launched with no batch flags at all,
    so llama.cpp's default physical batch of 512 applied while the context was
    2048; the engine itself then clamps the logical batch DOWN to the physical
    one for an embedding server, so only the physical size matters. Four
    ordinary questions in three days lost wiki retrieval to this;
  * at every daemon start the first embeds were issued while the server process
    was alive but still loading its model, so each one sat for its full 30-second
    timeout — three blind timeouts before the first answer, with the tool intents
    held pending for that window.

These tests pin both directions without a server: the launch argv is captured by
a Popen stand-in, and the transport is mocked.
"""
from __future__ import annotations

import contextlib
import json
import socket
import tempfile
import time
import unittest
from unittest import mock

from intergen import llama_manager
from intergen.llama_manager import LlamaManager, ServerConfig


class _CmdRecorder:
    """Popen stand-in: record argv, then abort the launch."""

    last_cmd: list[str] | None = None

    def __init__(self, cmd, **_kwargs):
        _CmdRecorder.last_cmd = list(cmd)
        raise RuntimeError("test sentinel: stop after cmd construction")


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _build_cmd(*, embedding: bool, context_size: int) -> list[str]:
    _CmdRecorder.last_cmd = None
    real_popen = llama_manager.subprocess.Popen
    llama_manager.subprocess.Popen = _CmdRecorder
    try:
        with tempfile.NamedTemporaryFile(suffix=".gguf") as model:
            mgr = LlamaManager()
            with contextlib.suppress(Exception):
                mgr.start(
                    model.name,
                    port=_free_port(),
                    context_size=context_size,
                    gpu_layers=0 if embedding else 999,
                    embedding=embedding,
                )
    finally:
        llama_manager.subprocess.Popen = real_popen
    assert _CmdRecorder.last_cmd is not None, (
        "start() never reached command construction — a pre-launch gate failed; "
        "the test environment is wrong, not the fix")
    return _CmdRecorder.last_cmd


class LaunchFlagTests(unittest.TestCase):
    def test_embedding_instance_physical_batch_covers_its_context(self):
        cmd = _build_cmd(embedding=True, context_size=2048)
        self.assertIn("--ubatch-size", cmd, f"no physical batch size in {cmd}")
        self.assertEqual(cmd[cmd.index("--ubatch-size") + 1], "2048")

    def test_embedding_instance_logical_batch_matches_it(self):
        # The engine clamps the logical batch down to the physical one for an
        # embedding server and says so in its own log; passing both keeps the
        # argv honest about what the server will actually run with.
        cmd = _build_cmd(embedding=True, context_size=2048)
        self.assertIn("--batch-size", cmd)
        self.assertEqual(cmd[cmd.index("--batch-size") + 1], "2048")

    def test_a_smaller_context_gets_a_smaller_batch(self):
        cmd = _build_cmd(embedding=True, context_size=512)
        self.assertEqual(cmd[cmd.index("--ubatch-size") + 1], "512")

    def test_chat_instance_keeps_the_engine_defaults(self):
        # Only the embedding instance needs the whole input in one physical
        # batch; a chat server streams and must keep the engine's own sizing.
        cmd = _build_cmd(embedding=False, context_size=16384)
        self.assertNotIn("--ubatch-size", cmd)
        self.assertNotIn("--batch-size", cmd)


class _FakeResp:
    def __init__(self, payload: dict) -> None:
        self._p = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._p

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *exc) -> bool:
        return False


class ReadinessTests(unittest.TestCase):
    """A live process is not a ready server."""

    def setUp(self) -> None:
        self.m = LlamaManager()
        self.m._config = ServerConfig(
            model_path="m", port=8081, context_size=2048, gpu_layers=0,
            parallel=1, jinja=False, reasoning="off", embedding=True)

    def test_a_fresh_manager_is_not_ready(self):
        self.assertFalse(self.m.is_ready())

    def test_embed_does_not_send_while_the_server_is_still_loading(self):
        # The defect: is_running() is True the instant Popen returns, so embed()
        # sent a request into a server that had not loaded its model and waited
        # the whole request timeout for nothing.
        sent = []
        with mock.patch.object(self.m, "is_running", return_value=True), \
             mock.patch("intergen.llama_manager.urllib.request.urlopen",
                        side_effect=lambda *a, **k: sent.append(a)):
            out = self.m.embed(["x"], ready_timeout=0.2)
        self.assertIsNone(out)
        self.assertEqual(sent, [], "embed() sent a request before the server was ready")

    def test_the_wait_for_readiness_is_bounded(self):
        with mock.patch.object(self.m, "is_running", return_value=True):
            t0 = time.time()
            self.m.embed(["x"], ready_timeout=0.3)
            waited = time.time() - t0
        self.assertLess(waited, 5.0, f"embed() waited {waited:.1f}s for readiness")
        self.assertGreaterEqual(waited, 0.25)

    def test_a_ready_server_is_asked_normally(self):
        payload = {"data": [{"embedding": [0.5], "index": 0}]}
        self.m._mark_ready()
        with mock.patch.object(self.m, "is_running", return_value=True), \
             mock.patch("intergen.llama_manager.urllib.request.urlopen",
                        return_value=_FakeResp(payload)):
            self.assertEqual(self.m.embed(["x"]), [[0.5]])


class _Router:
    """urlopen stand-in that answers the three endpoints embed() may use."""

    def __init__(self, *, tokens_per_call: int) -> None:
        self.calls: list[str] = []
        self.embedded: list[str] = []
        self._tokens = tokens_per_call

    def __call__(self, req, *_a, **_k):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        body = json.loads(req.data.decode()) if getattr(req, "data", None) else {}
        self.calls.append(url)
        if url.endswith("/tokenize"):
            return _FakeResp({"tokens": list(range(self._tokens))})
        if url.endswith("/detokenize"):
            return _FakeResp({"content": "T" * len(body.get("tokens", []))})
        self.embedded.extend(body.get("input", []))
        return _FakeResp({"data": [{"embedding": [0.1], "index": i}
                                   for i in range(len(body.get("input", [])))]})


class FitToContextTests(unittest.TestCase):
    """An input the server cannot take is shortened deliberately, and said so."""

    def setUp(self) -> None:
        self.m = LlamaManager()
        self.m._config = ServerConfig(
            model_path="m", port=8081, context_size=2048, gpu_layers=0,
            parallel=1, jinja=False, reasoning="off", embedding=True)
        self.m._mark_ready()

    def test_a_text_that_cannot_fit_is_shortened_before_it_is_sent(self):
        router = _Router(tokens_per_call=3099)   # the measured over-context case
        long_text = "x" * 12000
        with mock.patch.object(self.m, "is_running", return_value=True), \
             mock.patch("intergen.llama_manager.urllib.request.urlopen",
                        side_effect=router), \
             self.assertLogs("intergen.llama_manager", level="WARNING") as logs:
            out = self.m.embed([long_text])
        self.assertIsNotNone(out)
        self.assertTrue(any("/tokenize" in c for c in router.calls),
                        "the server's own tokenizer was never consulted")
        self.assertEqual(len(router.embedded), 1)
        self.assertLess(len(router.embedded[0]), len(long_text),
                        "the oversized text was sent unchanged")
        self.assertTrue(any("3099" in line and "2048" in line
                            for line in logs.output),
                        f"the log does not say what was dropped: {logs.output}")

    def test_a_text_that_fits_is_never_tokenized(self):
        router = _Router(tokens_per_call=10)
        with mock.patch.object(self.m, "is_running", return_value=True), \
             mock.patch("intergen.llama_manager.urllib.request.urlopen",
                        side_effect=router):
            self.m.embed(["a short question about the weather"])
        self.assertFalse(any("/tokenize" in c for c in router.calls),
                         "a short text paid for a tokenize round trip")

    def test_a_text_at_or_above_the_context_in_characters_is_checked(self):
        # A token is at least one character, so anything shorter than the
        # context in CHARACTERS cannot exceed it in tokens: that is the cheap
        # pre-filter. At or above it, the server's tokenizer decides.
        router = _Router(tokens_per_call=1000)   # under the limit after all
        with mock.patch.object(self.m, "is_running", return_value=True), \
             mock.patch("intergen.llama_manager.urllib.request.urlopen",
                        side_effect=router):
            self.m.embed(["y" * 2048])
        self.assertTrue(any("/tokenize" in c for c in router.calls))
        self.assertEqual(router.embedded, ["y" * 2048],
                         "a text that fits must be sent unchanged")


class _CountingRouter:
    """urlopen stand-in that records each embeddings request's input size."""

    def __init__(self, *, fail_on: int | None = None) -> None:
        self.sizes: list[int] = []
        self._fail_on = fail_on

    def __call__(self, req, *_a, **_k):
        body = json.loads(req.data.decode())
        n = len(body["input"])
        self.sizes.append(n)
        if self._fail_on is not None and len(self.sizes) == self._fail_on:
            raise OSError("connection reset")
        base = sum(self.sizes[:-1])
        return _FakeResp({"data": [{"embedding": [float(base + i)], "index": i}
                                   for i in range(n)]})


class BoundedRequestTests(unittest.TestCase):
    """A request has to be small enough to finish inside its own timeout.

    A seat machine's journal shows the wiki index asking for 32 passages in one
    request and the client giving up at 30 seconds, on every daemon start, with
    the index falling back to keyword matching afterwards — and the server still
    working on the abandoned batch while the first user turn queues behind it.
    """

    def setUp(self) -> None:
        self.m = LlamaManager()
        self.m._config = ServerConfig(
            model_path="m", port=8081, context_size=2048, gpu_layers=0,
            parallel=1, jinja=False, reasoning="off", embedding=True)
        self.m._mark_ready()

    def test_a_large_batch_is_split_into_bounded_requests(self):
        router = _CountingRouter()
        texts = [f"passage {i}" for i in range(20)]
        with mock.patch.object(self.m, "is_running", return_value=True), \
             mock.patch("intergen.llama_manager.urllib.request.urlopen",
                        side_effect=router):
            out = self.m.embed(texts)
        self.assertEqual(router.sizes, [8, 8, 4])
        self.assertEqual(len(out), 20)
        self.assertEqual([v[0] for v in out], [float(i) for i in range(20)],
                         "the vectors came back out of input order")

    def test_a_batch_that_already_fits_is_one_request(self):
        router = _CountingRouter()
        with mock.patch.object(self.m, "is_running", return_value=True), \
             mock.patch("intergen.llama_manager.urllib.request.urlopen",
                        side_effect=router):
            self.m.embed([f"passage {i}" for i in range(8)])
        self.assertEqual(router.sizes, [8])

    def test_one_failed_slice_degrades_the_whole_call(self):
        # A caller that asked for 20 vectors cannot line up 8 with its inputs,
        # so a partial answer is never returned.
        router = _CountingRouter(fail_on=2)
        with mock.patch.object(self.m, "is_running", return_value=True), \
             mock.patch("intergen.llama_manager.urllib.request.urlopen",
                        side_effect=router):
            self.assertIsNone(self.m.embed([f"p{i}" for i in range(20)]))


if __name__ == "__main__":
    unittest.main()
