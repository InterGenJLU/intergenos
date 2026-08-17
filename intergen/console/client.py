# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""WebSocket client for the InterGen console.

Connects to web_server.py at ws://localhost:8089/ws using the stored
auth token. Provides an async message sender and an async generator
for receiving typed messages from the server.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, AsyncIterator

import aiohttp

logger = logging.getLogger(__name__)

DEFAULT_WS_URL = "ws://127.0.0.1:8089/ws"
TOKEN_PATH = Path.home() / ".config" / "intergen" / "web-token"
RECONNECT_DELAYS = [1.0, 2.0, 4.0, 8.0, 16.0, 30.0]
PING_INTERVAL = 30.0


class ConsoleClient:
    """aiohttp WebSocket client for the terminal console.

    Usage:
        async with ConsoleClient(source_interface="console") as client:
            await client.send_message("Hello")
            async for msg in client.messages():
                print(msg["type"], msg.get("token", ""))
    """

    def __init__(self, *,
                 source_interface: str = "console",
                 ws_url: str = DEFAULT_WS_URL,
                 ) -> None:
        self._source_interface = source_interface
        self._ws_url = ws_url
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._connected = False
        self._client_id: str | None = None
        self._token: str = ""
        self._message_queue: asyncio.Queue = asyncio.Queue()
        self._reader_task: asyncio.Task | None = None

    # -- Connection ---------------------------------------------------------

    def _load_token(self) -> str | None:
        """Read the web auth token. Returns None if unavailable."""
        try:
            if TOKEN_PATH.exists():
                token = TOKEN_PATH.read_text().strip()
                if token:
                    return token
        except OSError:
            pass
        return None

    async def connect(self) -> dict[str, Any]:
        """Open WebSocket connection and receive the connected message."""
        self._token = self._load_token()
        if self._token is None:
            raise ConnectionError(
                "No web auth token found. Run 'intergen setup' first. "
                f"Expected at {TOKEN_PATH}"
            )
        if not self._token:
            raise ConnectionError(
                "Web auth token is empty. Run 'intergen setup' first."
            )

        url = f"{self._ws_url}?source_interface={self._source_interface}"

        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(
            url,
            protocols=["intergen", f"bearer.{self._token}"],
            heartbeat=PING_INTERVAL,
        )
        self._connected = True
        logger.info("Console WebSocket connected to %s", self._ws_url)

        # Read the connected message
        raw = await self._ws.receive_json()
        self._client_id = raw.get("client_id", "")
        logger.info("Connected as %s (source=%s)",
                     self._client_id, self._source_interface)

        # Start background reader
        self._reader_task = asyncio.create_task(self._reader_loop())

        return raw

    async def _reader_loop(self) -> None:
        """Background task: read messages from WebSocket into the queue."""
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        continue
                    await self._message_queue.put(data)
                elif msg.type in (aiohttp.WSMsgType.CLOSED,
                                  aiohttp.WSMsgType.ERROR):
                    break
        except Exception:
            logger.debug("WebSocket reader loop ended")
        finally:
            self._connected = False
            await self._message_queue.put({"type": "disconnected"})

    # -- Send ---------------------------------------------------------------

    async def send(self, data: dict[str, Any]) -> None:
        """Send a JSON message to the server."""
        if not self._ws or not self._connected:
            raise ConnectionError("Not connected to InterGen web server")
        await self._ws.send_json(data)

    async def send_message(self, content: str) -> None:
        """Send a chat message."""
        await self.send({"type": "message", "content": content})

    async def send_gate_decision(self, tool_call_id: str,
                                  decision: str) -> None:
        """Send a provenance gate decision."""
        await self.send({
            "type": "gate_decision",
            "tool_call_id": tool_call_id,
            "decision": decision,
        })

    async def send_slash_command(self, command: str) -> None:
        """Send a slash command."""
        await self.send({"type": "slash_command", "command": command})

    # -- Receive ------------------------------------------------------------

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        """Async generator yielding server→client messages.

        Messages are parsed JSON dicts with at minimum a "type" key.
        """
        while self._connected:
            try:
                msg = await asyncio.wait_for(
                    self._message_queue.get(), timeout=0.1
                )
                yield msg
                if msg.get("type") == "disconnected":
                    break
            except asyncio.TimeoutError:
                continue
        # Drain remaining messages on disconnect
        while not self._message_queue.empty():
            try:
                yield self._message_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    # -- Lifecycle ----------------------------------------------------------

    async def close(self) -> None:
        """Close the WebSocket connection and cleanup."""
        self._connected = False
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session:
            await self._session.close()
        logger.info("Console WebSocket disconnected")

    async def __aenter__(self) -> "ConsoleClient":
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def client_id(self) -> str | None:
        return self._client_id
