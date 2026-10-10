# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The daemon's status reads the panel's state once per response and derives
both of its panel fields from that one reading.

The status used to read the panel's state twice, once for ``panel_down`` and
once for ``components.panel``. A bind or a stop landing between the two reads
gave one response that contradicted itself: the panel named down in words
while its component read true, or named serving while its component read
false. Each case here changes the server's bind state between two reads, the
way such a landing would, and holds the response to one answer.
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from intergen.tests.test_daemon_escalate import _status_daemon


class _ServerWhoseBindChanges:
    """A panel server whose bind state is the next of ``readings`` at each
    read, the last one standing once they run out."""

    address = "127.0.0.1:8089"

    def __init__(self, *readings: bool) -> None:
        self._readings = list(readings)
        self.seen: list[bool] = []

    @property
    def running(self) -> bool:
        value = (self._readings.pop(0) if len(self._readings) > 1
                 else self._readings[0])
        self.seen.append(value)
        return value


class TheStatusReadsThePanelOnce(unittest.TestCase):

    def status_over(self, server, recorded: str) -> dict:
        daemon = _status_daemon(web_server=server, panel_down=recorded)
        with mock.patch("intergen.dbus_daemon.package_record.identity",
                        return_value=("stand-in", True)):
            return json.loads(daemon.status())

    def assert_one_answer(self, status: dict, server) -> None:
        self.assertEqual(status["panel_down"] is None,
                         status["components"]["panel"],
                         f"one response says panel_down "
                         f"{status['panel_down']!r} and components.panel "
                         f"{status['components']['panel']!r}")
        self.assertEqual(len(server.seen), 1,
                         f"the panel's state was read {len(server.seen)} "
                         f"times for one response")

    def test_a_bind_landing_while_the_response_is_built_gives_one_answer(self):
        server = _ServerWhoseBindChanges(False, True)
        recorded = "127.0.0.1:8089 is being bound and does not answer yet"
        status = self.status_over(server, recorded)
        self.assert_one_answer(status, server)
        self.assertEqual(status["panel_down"], recorded)
        self.assertIs(status["components"]["panel"], False)

    def test_a_stop_landing_while_the_response_is_built_gives_one_answer(self):
        server = _ServerWhoseBindChanges(True, False)
        status = self.status_over(server, "the panel was stopped with the "
                                          "daemon")
        self.assert_one_answer(status, server)
        self.assertIsNone(status["panel_down"])
        self.assertIs(status["components"]["panel"], True)


if __name__ == "__main__":
    unittest.main()
