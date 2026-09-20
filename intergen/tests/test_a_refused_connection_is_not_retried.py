# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Nothing is listening, so asking again cannot help.

WHAT WAS MEASURED. On 2026-09-20, proving that a deliberate stop is reported as
a stop, the fixed run still left seven error lines in the journal, written in
140 milliseconds:

    04:48:11,201 intergen.llm ERROR Local LLM request failed:
        <urlopen error [Errno 111] Connection refused>
    ... six more, through 04:48:11,341

One engine had stopped listening. The reply ladder turned that into seven
attempts, seven error lines and seven recorded transport failures, because a
request that gets no tokens reads to the gate as an "empty" answer, and an
empty answer is a thing worth retrying with more room. It is not, when the
reason there were no tokens is that nothing accepted the connection.

WHAT IS PINNED HERE:

  * a REFUSED connection is not retried inside the same turn — one attempt,
    not two, because a refused connect is a statement that nothing is
    listening and twenty milliseconds cannot change it;
  * a TIMED-OUT attempt still gets its retry — the two are different cases: a
    server that accepted the connection and did not answer in time may well
    answer with more room, which is what the retry was written for;
  * the transport failure is recorded and reported ONCE per down-episode, not
    once per attempt, and a later success clears it so the next episode is
    reported again — the same shape the embedding path already uses for a
    server that is down;
  * the person still gets the honest "the model server isn't running" reply,
    and the decision trace still carries a row for every attempt that got no
    response, because suppressing the noise must not suppress the record.
"""

from __future__ import annotations

import logging
import socket
import unittest
import urllib.error
from unittest import mock

from intergen import llm as llm_module
from intergen.interfaces.types import Message, MessageRole
from intergen.llm import LLMRouter

REFUSED = urllib.error.URLError(
    ConnectionRefusedError(111, "Connection refused"))
TIMED_OUT = socket.timeout("timed out")


class _Counting:
    """A urlopen stand-in that counts attempts and always fails the same way."""

    def __init__(self, exc):
        self.exc = exc
        self.attempts = 0

    def __call__(self, *_a, **_k):
        self.attempts += 1
        raise self.exc


class ARefusedConnectionTests(unittest.TestCase):

    def setUp(self):
        self.llm = LLMRouter(config=None)

    def _one_turn(self, exc):
        """Drive one chat() turn whose transport always fails, and return the
        attempt count, the log records and the trace rows."""
        transport = _Counting(exc)
        records = []
        logger = logging.getLogger("intergen.llm")
        handler = logging.Handler()
        handler.emit = records.append
        logger.addHandler(handler)
        old_level = logger.level
        logger.setLevel(logging.DEBUG)
        rows = []
        try:
            with mock.patch("urllib.request.urlopen", transport), \
                 mock.patch.object(llm_module.glass, "emit",
                                   side_effect=lambda *a, **k: rows.append((a, k))):
                reply = self.llm.chat(
                    [Message(role=MessageRole.USER, content="hello")])
        finally:
            logger.setLevel(old_level)
            logger.removeHandler(handler)
        return transport.attempts, records, rows, reply

    def _transport_lines(self, records):
        return [r for r in records
                if r.levelno >= logging.ERROR
                and "request failed" in r.getMessage()]

    # ---- the count ---------------------------------------------------------

    def test_a_refused_connection_is_attempted_once(self):
        attempts, _records, _rows, _reply = self._one_turn(REFUSED)
        self.assertEqual(attempts, 1,
                         "a refused connect was retried; nothing was listening "
                         "the first time and nothing had changed")

    def test_a_timeout_still_gets_its_retry(self):
        """The control, and the case the retry was written for."""
        attempts, _records, _rows, _reply = self._one_turn(TIMED_OUT)
        self.assertGreaterEqual(
            attempts, 2,
            "a server that accepted the connection and did not answer in time "
            "must still be given the second attempt with more room")

    def test_a_refused_connection_is_reported_once(self):
        _attempts, records, _rows, _reply = self._one_turn(REFUSED)
        self.assertEqual(len(self._transport_lines(records)), 1,
                         [r.getMessage() for r in records])

    def test_repeated_turns_against_a_down_server_report_once(self):
        """Many turns, one down-episode: the router is asked four times while
        nothing is listening and says so once."""
        lines = 0
        for _ in range(4):
            _a, records, _r, _reply = self._one_turn(REFUSED)
            lines += len(self._transport_lines(records))
        self.assertEqual(lines, 1,
                         "a server that is down was announced once per attempt "
                         "instead of once per episode")

    def test_a_later_success_reopens_the_episode(self):
        self._one_turn(REFUSED)
        self.assertIsNotNone(self.llm.transport_error)
        self.llm.note_transport_ok()
        self.assertIsNone(self.llm.transport_error)
        _a, records, _r, _reply = self._one_turn(REFUSED)
        self.assertEqual(len(self._transport_lines(records)), 1,
                         "after the server came back and went away again, the "
                         "new episode must be reported")

    # ---- the record --------------------------------------------------------

    def test_the_failure_is_still_recorded(self):
        self._one_turn(REFUSED)
        self.assertIsNotNone(
            self.llm.transport_error,
            "quieting the repeats must not stop the state being recorded")

    def test_the_person_still_gets_the_honest_reply(self):
        _a, _records, _rows, reply = self._one_turn(REFUSED)
        self.assertEqual(reply.text, self.llm._MODEL_SERVER_DOWN_FALLBACK)

    # ---- the record of every attempt survives ------------------------------

    def test_every_attempt_that_got_no_response_still_leaves_a_trace_row(self):
        """The log line is deduplicated; the decision trace is not. A reader
        counting attempts must still be able to count them."""
        for exc, name in ((REFUSED, "refused"), (TIMED_OUT, "timeout")):
            with self.subTest(case=name):
                llm = LLMRouter(config=None)
                self.llm = llm
                attempts, _records, rows, _reply = self._one_turn(exc)
                no_response = [k for a, k in rows
                               if a[:2] == ("model", "no_response")]
                self.assertEqual(len(no_response), attempts)

    # ---- the route that is left ---------------------------------------------

    def test_a_refused_local_server_still_reaches_the_cloud_provider(self):
        """Skipping the second LOCAL attempt must not skip the escalation.

        A local server nobody is listening on is exactly the situation a
        configured cloud provider exists for. An earlier draft of this change
        returned the "model server isn't running" text as soon as the connect
        was refused, which read as correct in every test here — because no
        provider was registered in any of them. With one registered, that draft
        served the fallback while a provider stood ready to answer.
        """

        class _Answering:
            def __init__(self):
                self.calls = 0

            def send(self, messages, max_tokens=None):
                self.calls += 1
                return mock.Mock(text="an answer from elsewhere",
                                 tokens_prompt=1, tokens_completion=2)

        provider = _Answering()
        self.llm.register_cloud_provider("test-provider", provider)
        attempts, _records, _rows, reply = self._one_turn(REFUSED)

        self.assertEqual(attempts, 1,
                         "the local server was asked twice after refusing")
        self.assertEqual(provider.calls, 1,
                         "the refused local connect never reached the cloud "
                         "provider, which was the one route left to an answer")
        self.assertEqual(reply.text, "an answer from elsewhere")
        self.assertEqual(reply.model, "cloud:test-provider")

    def test_the_trace_row_names_which_kind_of_failure_it_was(self):
        for exc, expected in ((REFUSED, "refused"), (TIMED_OUT, "timeout")):
            with self.subTest(expected=expected):
                llm = LLMRouter(config=None)
                self.llm = llm
                _a, _records, rows, _reply = self._one_turn(exc)
                detail = [k["detail"] for a, k in rows
                          if a[:2] == ("model", "no_response")][0]
                self.assertEqual(detail.get("transport"), expected)


class TheClassificationTests(unittest.TestCase):
    """The one place that decides which kind of transport failure this was."""

    def _kind(self, exc):
        return llm_module.transport_failure_kind(exc)

    def test_a_refused_connect_is_refused_however_it_is_wrapped(self):
        self.assertEqual(self._kind(REFUSED), "refused")
        self.assertEqual(self._kind(ConnectionRefusedError(111, "nope")),
                         "refused")
        self.assertEqual(
            self._kind(urllib.error.URLError(OSError(111, "Connection refused"))),
            "refused")

    def test_a_timeout_is_a_timeout_however_it_is_wrapped(self):
        self.assertEqual(self._kind(TIMED_OUT), "timeout")
        self.assertEqual(self._kind(TimeoutError()), "timeout")
        self.assertEqual(self._kind(urllib.error.URLError(socket.timeout())),
                         "timeout")

    def test_anything_else_is_neither_and_keeps_the_retry(self):
        """Unknown is not read as refused: a failure this function cannot
        classify must behave as it did before the function existed."""
        for exc in (urllib.error.URLError("something new"),
                    OSError("a disk?"), ValueError("nonsense")):
            with self.subTest(exc=type(exc).__name__):
                self.assertEqual(self._kind(exc), "other")

    def test_a_refused_errno_is_read_from_the_errno_not_the_text(self):
        """The text of a system error is locale-dependent; the number is not."""
        import errno
        self.assertEqual(
            self._kind(OSError(errno.ECONNREFUSED, "any words at all")),
            "refused")


if __name__ == "__main__":
    unittest.main()
