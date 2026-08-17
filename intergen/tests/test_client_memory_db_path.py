# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""InterGenTestClient.memory_db_path must resolve per transport mode.

In dbus mode the harness drives the live per-user daemon; exposing that daemon's
real memory.db (the shipped-default XDG path) is what lets the run's snapshot /
delta-cleanup / leak / write-gap checks MEASURE what a live memory turn wrote —
returning None (the prior behaviour) silently no-op'd every isolation check in
dbus mode, so a store scenario ran blind (surfaced on the first live 9B run).
Direct mode keeps the isolated throwaway DB. Daemon-free: the client is built via
__new__ so no bus/model is constructed.
"""

from __future__ import annotations

import unittest

from intergen.memory import _default_db_path
from intergen.tests.client import InterGenTestClient


def _client(mode: str, test_mem_dir):
    c = InterGenTestClient.__new__(InterGenTestClient)  # bypass bus/daemon construction
    c._mode = mode
    c._test_mem_dir = test_mem_dir
    return c


class MemoryDbPathTests(unittest.TestCase):
    def test_dbus_mode_exposes_the_real_default_db(self):
        c = _client("dbus", None)
        self.assertEqual(c.memory_db_path(), str(_default_db_path()))

    def test_direct_mode_uses_the_isolated_db(self):
        c = _client("direct", "/tmp/intergen-test-mem-xyz")
        self.assertEqual(c.memory_db_path(), "/tmp/intergen-test-mem-xyz/memory.db")

    def test_direct_mode_without_isolation_returns_none(self):
        # A partially-constructed direct client (no isolated dir yet) makes no
        # claim rather than guessing a path.
        c = _client("direct", None)
        self.assertIsNone(c.memory_db_path())


if __name__ == "__main__":
    unittest.main()
