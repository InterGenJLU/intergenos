# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""A read-side failure in the console client must be reported with its cause.

What this guards against
-----------------------
`ConsoleClient._reader_loop` (intergen/console/client.py) reads the server's
frames in a background task:

    try:
        async for msg in self._ws:
            ...
    except Exception:
        logger.debug("WebSocket reader loop ended")
    finally:
        self._connected = False
        await self._message_queue.put({"type": "disconnected"})

Every read-side fault — a transport error, a protocol violation, the pong
timeout aiohttp raises out of `receive()` because the client asks for
`heartbeat=PING_INTERVAL` — lands in that one `except Exception`. The cause is
recorded at DEBUG with no exception information and is then thrown away: the
only thing that leaves the loop is the bare `{"type": "disconnected"}` frame,
which `ConsoleShell._process_server_message` renders as

    "Disconnected from InterGen server."

— the same sentence a clean close produces. Nothing else on the path records
the cause: the exception never leaves the task (it is swallowed, so the
shell's own `logger.exception("Message processing error")` never sees it), and
nobody reads `ws.exception()`. The console's entry point calls
`logging.basicConfig(level=logging.WARNING)`, so the DEBUG line is not
emitted at all — a failed session and a clean goodbye are byte-identical to
the person at the terminal and to anyone reading the logs afterwards.

These cases assert the honest behaviour: a read that fails is reported AS a
failure — the frame that leaves the loop names the cause, and the cause is
logged at a level the console actually emits, with exception information
attached. They capture logging all the way down to DEBUG, so the current
DEBUG line is seen rather than hidden; what they refuse is a fault recorded
below the console's own threshold with the exception discarded.
"""

from __future__ import annotations

import asyncio
import logging

import aiohttp
import pytest

from intergen.console.client import ConsoleClient

CLIENT_LOGGER = "intergen.console.client"


class _ExplodingWS:
    """Stand-in for aiohttp's ClientWebSocketResponse whose read raises.

    aiohttp re-raises out of `receive()` for exactly this shape of fault —
    `asyncio.TimeoutError` and `ServerTimeoutError` from the heartbeat the
    client configures, `RuntimeError` from a concurrent receive — so the
    `async for` in the reader loop is where they surface.
    """

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.closed = False

    def __aiter__(self) -> "_ExplodingWS":
        return self

    async def __anext__(self):
        raise self._exc


async def _drive(exc: BaseException) -> dict:
    """Run one reader loop against a read that raises; return the frame."""
    client = ConsoleClient(source_interface="console")
    client._ws = _ExplodingWS(exc)
    client._connected = True
    await client._reader_loop()
    assert not client.connected, "the loop must mark the client disconnected"
    return client._message_queue.get_nowait()


def _cause_is_named(frame: dict, needle: str) -> bool:
    """True when some value of the frame names the failure."""
    return any(
        isinstance(value, str) and needle in value
        for key, value in frame.items()
        if key != "type"
    )


def _reported_records(caplog) -> list[logging.LogRecord]:
    """Records the console would actually emit (WARNING and above)."""
    return [
        record for record in caplog.records
        if record.name == CLIENT_LOGGER and record.levelno >= logging.WARNING
    ]


# The two limbs of the contract are separate cases: an assertion that stops at
# the frame would leave the logging limb unexecuted, and each limb is a claim
# in its own right.
READ_FAULTS = pytest.mark.parametrize("exc, needle", [
    (RuntimeError("boom"), "boom"),
    (aiohttp.ServerTimeoutError("No PONG received after 30 seconds"), "PONG"),
])


@READ_FAULTS
def test_a_reader_loop_failure_is_reported_with_its_cause(caplog, exc, needle):
    """The frame the loop hands the shell names the failure."""
    caplog.set_level(logging.DEBUG, logger=CLIENT_LOGGER)

    frame = asyncio.run(_drive(exc))

    assert frame.get("type") == "disconnected"
    assert _cause_is_named(frame, needle), (
        "a read that FAILED leaves the loop as a bare disconnect: the frame "
        f"{frame!r} names nothing about {type(exc).__name__}, so the shell "
        "renders the fault with the same sentence as a clean close"
    )


@READ_FAULTS
def test_a_reader_loop_failure_is_logged_where_the_console_can_see_it(
        caplog, exc, needle):
    """The cause reaches the log at a level the console emits, with its trace."""
    caplog.set_level(logging.DEBUG, logger=CLIENT_LOGGER)

    asyncio.run(_drive(exc))

    reported = _reported_records(caplog)
    assert reported, (
        "the failure is recorded below the level the console emits — the "
        "entry point sets basicConfig(level=logging.WARNING), so "
        f"{[(r.levelname, r.getMessage()) for r in caplog.records]} reaches "
        "no log at all"
    )
    assert any(record.exc_info for record in reported), (
        "the failure is logged without exception information: "
        f"{[r.getMessage() for r in reported]} — the cause is unrecoverable "
        "from the log"
    )
    assert any(needle in record.getMessage() or (
        record.exc_info and needle in str(record.exc_info[1])
    ) for record in reported), (
        "no emitted record names the cause of the disconnect: "
        f"{[r.getMessage() for r in reported]}"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
