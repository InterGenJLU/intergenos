# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Every line of the health report says what it read, about the address it
names.

THE CHAT SERVER. The "API reachable" line asked one fixed address,
127.0.0.1:8080, whatever port the chat server was configured to serve on, so
on any other port it read red over a server that answered, or green over
whatever else answered there. It now asks the address the chat server's own
manager names, and says which address it asked.

THE AUDIT RECORD. The "Audit log" line was always green, reading "0 entries,
0 tamper events" from a count of zero the daemon handed it and never changed:
it never read the audit record, so it asserted a tamper verdict nobody had
measured. The check now reads the record through the audit log's own surface
and claims only what it read: no tamper verdict, and nothing green over a
record it could not read. Its case here points the check at a record it cannot
read, never at the account's own.

The cases record the address the line asks and answer it themselves; nothing
here opens a connection.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from intergen.health import HealthAggregator

# A configured chat port that is not the shipped one.
CHAT_PORT = 18124


def _line(report: dict, name: str) -> dict:
    for layer in report["layers"]:
        for check in layer["checks"]:
            if check["name"] == name:
                return check
    raise AssertionError(f"the health report has no {name!r} line")


def _chat_manager(port: int) -> mock.Mock:
    """A running chat server's manager whose endpoint names ``port``."""
    llama = mock.Mock()
    llama.is_running.return_value = True
    llama.pid = 4242
    llama.model_name = "stand-in"
    llama.context_size = 4096
    llama.health.return_value = mock.Mock(model_loaded=True)
    llama.get_endpoint.return_value = (
        f"http://127.0.0.1:{port}/v1/chat/completions")
    return llama


class _Answer:
    """What a server answering /health with 200 hands back."""
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TheChatServersLineAsksTheAddressItsManagerNames(unittest.TestCase):

    def api_line(self, answer) -> tuple[dict, list[str]]:
        asked: list[str] = []

        def urlopen(request, *args, **kwargs):
            asked.append(request.full_url)
            return answer()

        with mock.patch("urllib.request.urlopen", urlopen):
            report = HealthAggregator(
                llama_manager=_chat_manager(CHAT_PORT)).collect()
        return _line(report, "API reachable"), asked

    def test_the_line_asks_the_configured_address_and_names_it(self):
        line, asked = self.api_line(_Answer)
        self.assertEqual(asked, [f"http://127.0.0.1:{CHAT_PORT}/health"])
        self.assertEqual(line["status"], "green")
        self.assertIn(f"127.0.0.1:{CHAT_PORT}", line["summary"])

    def test_a_configured_address_that_does_not_answer_is_named_red(self):
        def refused():
            raise ConnectionRefusedError(111, "Connection refused")

        line, asked = self.api_line(refused)
        self.assertEqual(asked, [f"http://127.0.0.1:{CHAT_PORT}/health"])
        self.assertEqual(line["status"], "red")
        self.assertIn(f"127.0.0.1:{CHAT_PORT}", line["summary"])
        self.assertIn("Connection refused", line["summary"])


class TheAuditLineClaimsNothingItDidNotRead(unittest.TestCase):

    def test_the_audit_line_is_not_green_over_a_record_it_never_read(self):
        # A directory where the record file should be: a record nothing can
        # read, in a scratch directory, so the account's own is never opened.
        with tempfile.TemporaryDirectory() as scratch:
            record = Path(scratch) / "tool-dispatch.jsonl"
            record.mkdir()
            with mock.patch("intergen.audit_log.default_log_path",
                            return_value=record):
                line = _line(HealthAggregator().collect(), "Audit log")
        self.assertNotEqual(line["status"], "green")
        self.assertNotIn("tamper", line["summary"])


if __name__ == "__main__":
    unittest.main()
