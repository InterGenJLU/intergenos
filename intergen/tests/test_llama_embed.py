# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""AI-12 part C — LlamaManager embedding-instance support.

Covers the new embed() path that replaces the sentence-transformers/torch/
huggingface stack with a local --embedding llama-server reached over stdlib
urllib (no PyPI/SDK). Transport is mocked so these run anywhere; the live
llama-server round-trip is a host/integration concern.
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from intergen.llama_manager import LlamaManager, ServerConfig


class _FakeResp:
    def __init__(self, payload: dict) -> None:
        self._p = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._p

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *exc) -> bool:
        return False


def _urlopen(payload: dict):
    return mock.patch(
        "intergen.llama_manager.urllib.request.urlopen",
        return_value=_FakeResp(payload),
    )


class EmbedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.m = LlamaManager()

    def _running(self, val: bool = True):
        return mock.patch.object(self.m, "is_running", return_value=val)

    def test_empty_input_is_empty_list(self):
        self.assertEqual(self.m.embed([]), [])

    def test_server_down_returns_none(self):
        with self._running(False):
            self.assertIsNone(self.m.embed(["x"]))

    def test_parses_vectors_in_order(self):
        payload = {"data": [
            {"embedding": [0.1, 0.2], "index": 0},
            {"embedding": [0.3, 0.4], "index": 1},
        ]}
        with self._running(), _urlopen(payload):
            self.assertEqual(self.m.embed(["a", "b"]), [[0.1, 0.2], [0.3, 0.4]])

    def test_reorders_by_index(self):
        # Server returns rows out of order; embed() must restore input order.
        payload = {"data": [
            {"embedding": [9.9], "index": 1},
            {"embedding": [1.1], "index": 0},
        ]}
        with self._running(), _urlopen(payload):
            self.assertEqual(self.m.embed(["a", "b"]), [[1.1], [9.9]])

    def test_row_count_mismatch_returns_none(self):
        payload = {"data": [{"embedding": [0.1], "index": 0}]}  # 1 row, 2 inputs
        with self._running(), _urlopen(payload):
            self.assertIsNone(self.m.embed(["a", "b"]))

    def test_request_failure_returns_none(self):
        with self._running(), mock.patch(
            "intergen.llama_manager.urllib.request.urlopen",
            side_effect=OSError("connection refused"),
        ):
            self.assertIsNone(self.m.embed(["a"]))

    def test_non_dict_rows_degrade_to_none(self):
        # WC verify catch: a data list whose ROWS are not dicts (e.g. list of
        # lists) made r.get(...) raise AttributeError, which the old
        # (KeyError, TypeError) except missed — crashing the caller. The
        # degrade-don't-crash contract must hold for this malformed shape too.
        payload = {"data": [["not", "a", "dict"], ["another", "list"]]}  # 2 rows, 2 inputs
        with self._running(), _urlopen(payload):
            self.assertIsNone(self.m.embed(["a", "b"]))

    def test_embedding_endpoint_path(self):
        self.m._config = ServerConfig(
            model_path="m", port=8081, context_size=512, gpu_layers=0,
            parallel=1, jinja=False, reasoning="off", embedding=True,
        )
        self.assertEqual(
            self.m.get_embedding_endpoint(), "http://localhost:8081/v1/embeddings",
        )

    def test_config_carries_embedding_flag(self):
        c = ServerConfig(
            model_path="m", port=8081, context_size=512, gpu_layers=0,
            parallel=1, jinja=False, reasoning="off", embedding=True,
        )
        self.assertTrue(c.embedding)


if __name__ == "__main__":
    unittest.main()
