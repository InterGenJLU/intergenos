# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""The D-Bus Ask reply must carry the glass-trace join key.

Design §4.2: the returned ``trace_id`` is the join key to the always-on glass
trace. But the router's ``result.trace_id`` is the DEV-gated tracer id — empty
unless ``--observe``/``INTERGEN_TRACE`` is on (the router stamps it from the
active tracer span, else ""). Glass, meanwhile, threads THIS turn's ``_gturn``
into every row it emits, so ``_gturn`` is the id a reply must carry for a consumer
to join the reply back to its glass rows. Empirically, a live 9B run returned
``trace_id=''`` on every turn while glass carried ``turn_id`` on all 2000 rows —
so the reply→glass join could never resolve, and the harness's grounding /
decompose assertions failed closed for lack of the key, not the behaviour.

These pins verify the reply falls back to ``_gturn`` when the tracer id is empty
(normal operation) and preserves the tracer id when ``--observe`` supplied one —
daemon-free (mock router, no model, no bus), the same construction the
review-callback wiring test uses.
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from intergen.dbus_daemon import InterGenDaemon


def _route_result(trace_id: str = ""):
    r = mock.Mock()
    r.text = "ok"
    r.full_output = ""
    r.source = "identity"
    r.handled = True
    r.tool_calls = []
    r.tool_results = []
    r.used_llm = False
    r.escalated = False
    r.escalation_offer = None
    r.trace_id = trace_id
    return r


class DbusAskTraceIdTests(unittest.TestCase):
    def _daemon(self, trace_id: str = ""):
        d = InterGenDaemon()
        d._router = mock.Mock()
        d._router.route.return_value = _route_result(trace_id)
        return d

    def test_empty_tracer_id_falls_back_to_the_glass_turn_id(self):
        # result.trace_id="" (no --observe) -> the reply carries the glass turn id,
        # which is the join key every glass row for this turn shares.
        d = self._daemon(trace_id="")
        with mock.patch("intergen.glass.new_turn_id", return_value="GT-abc"):
            reply = json.loads(d.ask("what's my editor?"))
        self.assertEqual(reply["trace_id"], "GT-abc")

    def test_dev_tracer_id_is_preserved_when_present(self):
        # --observe on -> result.trace_id is the tracer id; keep it so a
        # decisions.jsonl capture still joins on it (dev-mode unchanged).
        d = self._daemon(trace_id="TRACER-xyz")
        with mock.patch("intergen.glass.new_turn_id", return_value="GT-abc"):
            reply = json.loads(d.ask("what's my editor?"))
        self.assertEqual(reply["trace_id"], "TRACER-xyz")


if __name__ == "__main__":
    unittest.main()
