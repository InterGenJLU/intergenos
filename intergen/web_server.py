# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""InterGen web server — aiohttp + WebSocket bridge to the InterGen daemon.

Serves the browser web UI, the terminal console (via WebSocket parity), and
the governance/metrics dashboards. All three frontends speak the same protocol
documented at docs/architecture/intergen-web-ui/websocket-protocol.md.

Architecture:
  Browser / Console  ──WebSocket──►  web_server.py  ──direct refs──►
    router, llm, tools, governance, metrics, event_logger, state_cache

Static files are served from intergen/web/ with Content-Security-Policy headers.
WebSocket auth is a single-token check at handshake — no per-message auth.

Connection lifecycle:
  1. Client opens ws://localhost:8089/ws?token=<hex>&source_interface=<web|console>
  2. Server validates token, creates ConnectionContext, sends {type:"connected"}
  3. Message dispatch loop — type→handler map for all client→server message types
  4. On disconnect: cleanup connection registry, release session resources
  5. Graceful shutdown: SIGTERM/SIGINT → force-close all WS → stop aiohttp → exit
"""

from __future__ import annotations

import asyncio
import hmac
import errno
import json
import logging
import os
import signal
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import aiohttp
from aiohttp import WSCloseCode, WSMsgType, web

from intergen import glass, safety
from intergen.governance import GovernanceEngine
from intergen.session_manager import SessionManager
from intergen.conversation_state import (
    ConversationState, new_conversation_state,
)
from intergen.interfaces.types import (
    AnswerLinkage,
    Message,
    MessageRole,
    RouteResult,
    ToolCall,
    ToolResult,
)

logger = logging.getLogger(__name__)

# A send hit a socket the peer has already closed — a half-open connection, or a
# client that navigated away / hit the G3-17 reconnect mid-stream. This is a
# normal end-of-conversation condition, NOT an error: we log it at debug and
# stop the stream cleanly, never a traceback. aiohttp raises
# ClientConnectionResetError ("Cannot write to closing transport") on write to a
# closing transport; a reset peer surfaces as ConnectionResetError.
_CLIENT_GONE = (ConnectionResetError, aiohttp.ClientConnectionResetError)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8089

# Perceived-latency slow-lane backstop: if a tool's execute() runs longer than
# this, fire a hop-2 "still working" nudge (and keep the thinking pill alive)
# while it finishes. A constant backstop is correct for every tool — fast calls
# (<120ms) never trip it, the LLM-backed analyze_file (~12s) does. The latency
# matrix can later PRE-arm known-slow calls to nudge sooner; this guarantees
# correctness regardless. See docs/architecture/intergen-perceived-latency-design.md.
_SLOW_TOOL_THRESHOLD_S = 5.0

# Outer ceiling for the gate bridge (_make_web_review_callback): the worker
# thread blocks on the async card up to this long. Set just ABOVE the card's own
# 300 s await so the INNER timeout fires first and returns a clean "denied"
# verdict; the outer ceiling is only the fail-closed backstop if the card coro
# never completes (loop stall). Either way the bridge fails closed to "deny".
_GATE_BRIDGE_TIMEOUT_S = 310.0

# TURN-LIFECYCLE DEADLINES. The browser arms a whole-turn failsafe the moment
# the user presses send and disarms it only on the first server frame for that
# turn. Nothing used to be sent between "message received" and "routing
# finished", so whenever routing outlived that failsafe the browser hid the
# thinking indicator, told the user InterGen had not responded and force-closed
# the socket — while the server was still working on the turn, and with the
# answer, when it existed, having nowhere left to go. Two rules close that:
# every turn is acknowledged immediately (the turn_ack frame in _run_turn), and
# the server holds itself to a routing deadline STRICTLY SHORTER than the
# client's failsafe, so the side that gives up first is always the side that can
# say why.
#
# CLIENT_RESPONSE_TIMEOUT_S mirrors RESPONSE_TIMEOUT_MS in intergen/web/app.js.
# It is not read by the server at runtime; it is here so the relationship is
# stated in one place. intergen/tests/test_turn_lifecycle_contract.py parses the
# real app.js and fails if the two ever drift apart or if the ordering below is
# inverted, so neither side can be edited alone.
CLIENT_RESPONSE_TIMEOUT_S = 30.0
SERVER_ROUTE_DEADLINE_S = 20.0

# How often the routing watchdog wakes to decide whether to charge the deadline.
# Small enough that the deadline is accurate to a fraction of a second, large
# enough that the poll costs nothing next to a routing round-trip.
_ROUTE_DEADLINE_TICK_S = 0.05

TOKEN_PATH = Path.home() / ".config" / "intergen" / "web-token"
STATIC_DIR = Path(__file__).parent / "web"
_503_BODY = json.dumps({"type": "error", "code": "server_not_ready",
                         "message": "InterGen components not yet initialized."})

CSP_HEADER = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self' ws://localhost:8089; "
    "font-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'none'"
)

SYSTEM_STATS_INTERVAL = 60  # seconds between system_status broadcasts
HEARTBEAT_INTERVAL = 30     # seconds between server→client pings

VALID_SOURCE_INTERFACES = frozenset({"web", "console"})


# ---------------------------------------------------------------------------
# Per-connection context
# ---------------------------------------------------------------------------

@dataclass
class ConnectionContext:
    """All state belonging to a single WebSocket connection.

    One instance per connected client. Web sessions and console sessions
    are independently namespaced — switching sessions in the browser does
    not affect the console and vice versa.

    `conversation` is the connection's own conversation: the history the model
    is prompted with, the consent decisions the person made here, the ingress
    watermarks, the offers awaiting a yes or no. It is per connection because a
    conversation is what a person is having, and two tabs are two conversations.
    `session_history` is the same list seen from the transcript's side — the
    pane and the model's prompt are one list, so they cannot drift.
    """

    client_id: str
    source_interface: str                      # "web" or "console"
    ws: web.WebSocketResponse
    connected_at: float = field(default_factory=time.monotonic)
    conversation: ConversationState = None      # set in __post_init__
    cmd_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending_gate: str | None = None            # tool_call_id awaiting decision
    gate_future: asyncio.Future | None = None  # resolved when gate_decision arrives
    tool_tasks: set = field(default_factory=set)  # in-flight user-invoked tools;
    #                                            held so a detached task is not
    #                                            garbage-collected mid-gate
    turn_task: asyncio.Task | None = None      # in-flight chat turn (runs off the
    #                                            receive loop so a gated turn can't
    #                                            block the loop from reading its own
    #                                            gate_decision — see _dispatch_loop)
    model_tier: str = "medium"                 # "small", "medium", "large"
    current_session_id: str = "default"
    auth_token: str = ""
    user_agent: str = ""

    def __post_init__(self) -> None:
        # A connection without a conversation cannot be served, so one is made
        # here when the caller did not supply the router-wired one. Never
        # shared: the whole point of this field is that no two connections hold
        # the same conversation.
        if self.conversation is None:
            self.conversation = new_conversation_state()

    @property
    def session_history(self) -> "list[Message]":
        """The transcript — the SAME list the model's prompt is built from."""
        return self.conversation.history

    @session_history.setter
    def session_history(self, value) -> None:
        # Replaced in place rather than rebound, so the conversation and the
        # transcript stay one object no matter which side writes.
        self.conversation.history[:] = list(value or [])


def _make_error(code: str, message: str, detail: dict | None = None) -> dict[str, Any]:
    """Construct a protocol-compliant error message."""
    msg: dict[str, Any] = {"type": "error", "code": code, "message": message}
    if detail:
        msg["detail"] = detail
    return msg


def _history_wire_format(history: "list[Message]") -> list[dict[str, str]]:
    """Render a session history for the wire, preserving order and roles.

    The stored history holds Message objects whose role is a MessageRole enum;
    the client needs plain role strings. Ordering is the transcript's meaning,
    so the sequence is passed through untouched — no filtering, no dedup.
    """
    wire: list[dict[str, str]] = []
    for m in history:
        role = getattr(m, "role", None)
        wire.append({
            "role": getattr(role, "value", None) or str(role or "assistant"),
            "content": getattr(m, "content", "") or "",
        })
    return wire


# ---------------------------------------------------------------------------
# Web server
# ---------------------------------------------------------------------------

class WebServer:
    """aiohttp application serving the InterGen web UI + WebSocket bridge.

    Receives component references at construction time. The daemon calls
    start() once all components are initialized, and stop() on shutdown.
    """

    def __init__(self, *,
                 host: str = DEFAULT_HOST,
                 port: int = DEFAULT_PORT,
                 router: Any = None,
                 llm: Any = None,
                 tools: Any = None,
                 governance: GovernanceEngine | None = None,
                 metrics: Any = None,
                 event_logger: Any = None,
                 state_cache: Any = None,
                 memory: Any = None,
                 health_aggregator: Any = None,
                 ) -> None:
        self._host = host
        self._port = port
        self._router = router
        self._llm = llm
        self._tools = tools
        # Serializes the offloaded router.route() across connections (single-gate review): the
        # fast/offer path now runs route() in a worker thread so its internal
        # offer-accept dispatch can gate via the async web card without deadlocking
        # the loop; the router is a single shared instance with per-turn mutable
        # state, so cross-connection route()s must not run concurrently (they were
        # implicitly serialized by the single event-loop thread before). Lazily
        # created on the loop in _handle_client_message so it binds to the right
        # loop (same convention as ConnectionContext.cmd_lock).
        self._route_lock: asyncio.Lock | None = None
        self._governance = governance
        self._metrics = metrics
        self._event_logger = event_logger
        self._state_cache = state_cache
        self._memory = memory
        self._health_agg = health_aggregator

        self._sessions = SessionManager()

        # Perceived-latency voice (filler picker). Loaded once; if the asset is
        # missing it degrades to available=False and no fillers are emitted.
        from intergen.voice import FillerPicker
        self._filler = FillerPicker()

        self._app = web.Application()
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._connections: dict[str, ConnectionContext] = {}
        self._running = False
        self._ready = False       # True when components are initialized
        self._startup_time = time.monotonic()
        self._stats_task: asyncio.Task | None = None

        self._setup_routes()

    # -- Property -----------------------------------------------------------

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def running(self) -> bool:
        """True once OUR bind holds the port. The daemon's web watchdog keys its
        retry on this (our own bind state), never on an HTTP probe of the port,
        so a foreign holder answering on the port is never mistaken for ours."""
        return self._running

    def mark_ready(self) -> None:
        self._ready = True
        logger.info("Web server marked ready — serving requests")

    # -- Route setup --------------------------------------------------------

    def _setup_routes(self) -> None:
        app = self._app

        # WebSocket endpoint
        app.router.add_get("/ws", self._handle_websocket)

        # API endpoints
        app.router.add_get("/api/status", self._handle_api_status)
        app.router.add_get("/api/metrics/performance", self._handle_metrics_performance)
        app.router.add_get("/api/metrics/usage", self._handle_metrics_usage)
        app.router.add_get("/api/metrics/realtime", self._handle_metrics_realtime)

        # Phone-a-friend provider config (§B). User-authenticated HTTP — NOT a
        # tool surface, so the AI cannot edit the AI-immutable escalation/
        # providers config. Keys go to the keyring; only ids are persisted.
        app.router.add_get("/api/providers", self._handle_providers_get)
        app.router.add_post("/api/providers", self._handle_providers_post)
        app.router.add_post("/api/providers/primary", self._handle_providers_primary)
        app.router.add_delete("/api/providers/{name}", self._handle_providers_delete)

        # Static file serving with CSP
        app.router.add_get("/", self._handle_index)

        # Catch-all for static files
        app.router.add_static("/", STATIC_DIR, show_index=False)

    # -- Middleware ---------------------------------------------------------

    @web.middleware
    async def _csp_middleware(self, request: web.Request,
                              handler: Callable) -> web.StreamResponse:
        """Inject CSP + cache headers on all responses from static routes."""
        response = await handler(request)
        response.headers.setdefault("Content-Security-Policy", CSP_HEADER)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        # The panel UI ships WITH the app and updates with it. Without an explicit
        # Cache-Control, WebKit applies HEURISTIC freshness (based on Last-Modified
        # age) and serves a stale style.css/app.js from its persistent cache after
        # an update — e.g. a CSS layout fix never appears in the running panel.
        # "no-cache" = store but ALWAYS revalidate (ETag -> 304 when unchanged, a
        # fresh 200 the moment the file changes), so the panel never renders stale
        # assets. Validated 2026-06-09: a fresh WebView already loaded the fixed
        # CSS correctly; only cached panels were stale.
        response.headers.setdefault("Cache-Control", "no-cache")
        return response

    # -- Lifecycle ----------------------------------------------------------

    async def start(self) -> bool:
        """Start the aiohttp server. Returns True once OUR bind holds the port,
        False if the port is held by another process (a HANDLED cold-boot
        collision, not a crash).

        Bind-ownership by bind: aiohttp's TCPSite.start() atomically binds the
        socket and raises EADDRINUSE if a foreign holder — the GDM greeter
        session's own InterGen web server, on the fixed-port cold-boot collision
        (the same class the chat/embed ports already guard) — already holds the
        port. That is treated as a HANDLED condition: log it and stay
        not-running so the daemon's web watchdog rebinds once the greeter session
        tears down and frees the port, rather than letting the OSError escape as
        an unhandled "web server thread crashed" traceback with no recovery.
        _running is set ONLY after OUR bind succeeds, so a foreign holder
        answering on the port can never count as ours — readiness keys on our own
        bind, never an HTTP probe of the port.

        Idempotent + retriable (the watchdog path): an already-running server is
        a no-op; the middleware + runner are set up once and reused across rebind
        attempts so a retry never double-registers middleware.
        """
        if self._running:
            return True
        # Set up the runner + middleware exactly once; the watchdog may call
        # start() repeatedly until the port frees.
        if self._runner is None:
            self._app.middlewares.append(self._csp_middleware)
            self._runner = web.AppRunner(self._app)
            await self._runner.setup()
        self._site = web.TCPSite(self._runner, self._host, self._port)
        try:
            await self._site.start()
        except OSError as e:
            # Port held by another process — the cold-boot greeter collision (or
            # any bind refusal). HANDLED: drop the unstarted site, stay
            # not-running so the watchdog retries; never propagate as a crash.
            self._site = None
            if e.errno == errno.EADDRINUSE:
                logger.warning(
                    "Web port %d is held by another process (cold-boot greeter "
                    "collision); refusing to bind — the web watchdog will rebind "
                    "once it is freed. Handled, not a crash.", self._port)
            else:
                logger.warning(
                    "Web server bind on %s:%d failed (%s); the web watchdog will "
                    "retry.", self._host, self._port, e)
            return False
        self._running = True
        self._startup_time = time.monotonic()
        logger.info("Web server listening on http://%s:%d", self._host, self._port)

        # Start periodic system stats broadcaster
        self._stats_task = asyncio.create_task(self._broadcast_system_stats())
        return True

    async def stop(self) -> None:
        """Graceful shutdown.

        Closes all WebSocket connections with a normal close code, cancels
        background tasks, and shuts down the aiohttp runner. Called by the
        daemon's stop_service().
        """
        logger.info("Web server shutting down...")
        self._running = False

        # Cancel stats broadcaster
        if self._stats_task and not self._stats_task.done():
            self._stats_task.cancel()
            try:
                await self._stats_task
            except asyncio.CancelledError:
                pass

        # Close all WebSocket connections
        for ctx in list(self._connections.values()):
            try:
                await ctx.ws.close(code=WSCloseCode.GOING_AWAY,
                                   message=b"Server shutting down")
            except Exception:
                pass
        self._connections.clear()

        # Shut down aiohttp
        if self._site:
            await self._site.stop()
        if self._runner:
            await self._runner.cleanup()
        logger.info("Web server stopped")

    # -- Health / status ----------------------------------------------------

    def _check_ready(self) -> bool:
        return self._ready and self._running

    # -- Auth ---------------------------------------------------------------

    @staticmethod
    def _load_auth_token() -> str | None:
        """Read the web auth token from disk.

        Token is generated at 'intergen setup' time (B-005). If the token
        file doesn't exist, the server refuses WebSocket connections with
        an auth error.
        """
        try:
            if TOKEN_PATH.exists():
                token = TOKEN_PATH.read_text().strip()
                if token:
                    return token
        except OSError:
            pass
        return None

    @staticmethod
    def _extract_ws_token(request: web.Request) -> str | None:
        """Extract bearer token from Sec-WebSocket-Protocol header.

        Client sends: Sec-WebSocket-Protocol: intergen, bearer.<token>
        Server echoes: Sec-WebSocket-Protocol: intergen (echo discipline)
        """
        proto_header = request.headers.get("Sec-WebSocket-Protocol", "")
        if not proto_header:
            return None
        for part in proto_header.split(","):
            part = part.strip()
            if part.startswith("bearer."):
                return part[len("bearer."):]
        return None

    @staticmethod
    def _extract_auth_token(request: web.Request) -> str | None:
        """Extract auth token from Authorization header for HTTP requests.

        Supports: Authorization: Bearer <token>
        """
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[len("Bearer "):]
        return None

    def _validate_http_request(self, request: web.Request) -> bool:
        """Validate Authorization header for HTTP endpoints.

        Fails closed: returns False if no token is configured OR the provided
        token does not match. Matches the WS handshake's fail-close posture so
        pre-setup state has no data-surface exposure (Arc4-OPDECISION-1
        unified fail-close per security-only alignment).
        """
        expected = self._load_auth_token()
        if not expected:
            return False
        token = self._extract_auth_token(request)
        return bool(token and hmac.compare_digest(token, expected))

    # -- Static file handlers -----------------------------------------------

    async def _handle_index(self, request: web.Request) -> web.Response:
        """Serve index.html with injected auth token when provided."""
        if not self._check_ready():
            return web.Response(text=_503_BODY, status=503,
                                content_type="application/json")
        index_path = STATIC_DIR / "index.html"
        if not index_path.exists():
            return web.Response(text="InterGen web UI not installed.",
                                status=404)
        token = self._extract_auth_token(request)
        if token:
            html = index_path.read_text()
            token_script = (
                f'<script>window.__INTERGEN_TOKEN__ = '
                f'{json.dumps(token)};</script>\n'
            )
            html = html.replace('<head>', '<head>\n' + token_script)
            return web.Response(text=html, content_type="text/html")
        return web.FileResponse(index_path)

    def _auth_error(self) -> web.Response:
        return web.Response(text=json.dumps(_make_error(
            "auth_failed", "Invalid or missing auth token.",
        )), status=401, content_type="application/json")

    async def _handle_api_status(self, request: web.Request) -> web.Response:
        """GET /api/status — quick health check."""
        if not self._check_ready():
            return web.Response(text=_503_BODY, status=503,
                                content_type="application/json")
        if not self._validate_http_request(request):
            return self._auth_error()
        status = {
            "type": "system_status",
            "server": {
                "running": self._running,
                "host": self._host,
                "port": self._port,
                "uptime_seconds": round(time.monotonic() - self._startup_time),
                "connections": len(self._connections),
                "ready": self._ready,
                "web_connections": sum(1 for c in self._connections.values()
                                       if c.source_interface == "web"),
                "console_connections": sum(1 for c in self._connections.values()
                                           if c.source_interface == "console"),
            },
        }
        if self._governance:
            status["governance"] = self._governance.health_snapshot()
        if self._metrics:
            status["metrics"] = self._metrics.get_status()
        return web.json_response(status)

    async def _handle_metrics_performance(self, request: web.Request) -> web.Response:
        """GET /api/metrics/performance — latency + throughput data."""
        if not self._check_ready():
            return web.Response(text=_503_BODY, status=503,
                                content_type="application/json")
        if not self._validate_http_request(request):
            return self._auth_error()
        data: dict[str, Any] = {"type": "metrics_performance"}
        if self._metrics:
            ms = self._metrics.get_status()
            counters = ms.get("counters", {})
            # Per-route average latency. The router records each route's elapsed
            # time under latency_route_<src>_avg_ms; a missing bucket means that
            # route hasn't been taken yet -> 0 (not a fabricated identical value).
            data["latency"] = {
                "keyword_ms": ms.get("latency_route_keyword_avg_ms", 0),
                "cache_ms": ms.get("latency_route_cache_avg_ms", 0),
                "semantic_ms": ms.get("latency_route_semantic_avg_ms", 0),
                "llm_tools_ms": ms.get("latency_route_llm_tools_avg_ms", 0),
                "llm_freeform_ms": ms.get("latency_route_llm_freeform_avg_ms", 0),
            }
            # Counts live under get_status()["counters"], NOT the top level —
            # reading them flat made every count default to 0 (identical rows).
            data["counts"] = {
                "keyword": counters.get("route_keyword", 0),
                "cache": counters.get("route_cache", 0),
                "semantic": counters.get("route_semantic", 0),
                "llm_tools": counters.get("route_llm_tools", 0),
                "llm_freeform": counters.get("route_llm_freeform", 0),
                "total_requests": counters.get("requests", 0),
                "llm_calls": counters.get("llm_calls", 0),
                "escalations": counters.get("escalations", 0),
            }
        # Cumulative authoritative token usage (llama.cpp tokenizer counts) +
        # tool-cache effectiveness — the perceived-latency surfaces.
        data["tokens"] = self._token_usage()
        data["cache"] = self._cache_stats()
        data["model_perf"] = self._model_perf()
        return web.json_response(data)

    # -- Phone-a-friend provider config (§B) --------------------------------
    # Keys are received over the Bearer-authed loopback, handed straight to the
    # keyring, and NEVER persisted to config or logged.

    async def _handle_providers_get(self, request: web.Request) -> web.Response:
        if not self._validate_http_request(request):
            return self._auth_error()
        from intergen import provider_config as pc
        return web.json_response(pc.list_providers())

    async def _handle_providers_post(self, request: web.Request) -> web.Response:
        """Add/update a provider. Body: {name, adapter, model, api_key?,
        base_url?, max_tokens?, temperature?}. api_key (if given) -> keyring."""
        if not self._validate_http_request(request):
            return self._auth_error()
        from intergen import provider_config as pc
        from intergen.cloud.http_adapter import store_secret, CloudAdapterError
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON body"}, status=400)
        try:
            entry = pc.upsert_provider(
                body.get("name", ""), body.get("adapter", ""),
                body.get("model", ""),
                base_url=body.get("base_url") or None,
                max_tokens=int(body.get("max_tokens", 4096)),
                temperature=float(body.get("temperature", 0.7)),
            )
        except (ValueError, TypeError) as e:
            return web.json_response({"error": str(e)}, status=400)
        # Store the key in the keyring (never in config/logs). Optional on an
        # update that only changes the model.
        api_key = (body.get("api_key") or "").strip()
        if api_key:
            try:
                store_secret(entry["api_key_keyring_id"], api_key,
                             label=f"InterGen — {entry['name']}")
            except (CloudAdapterError, Exception) as e:  # noqa: BLE001
                logger.warning("keyring store failed for %s: %s",
                               entry["name"], type(e).__name__)
                return web.json_response(
                    {"error": "could not store the API key in the system keyring "
                              "(is a keyring/login session available?)"}, status=500)
        self._reload_escalation()
        return web.json_response(pc.list_providers())

    async def _handle_providers_primary(self, request: web.Request) -> web.Response:
        if not self._validate_http_request(request):
            return self._auth_error()
        from intergen import provider_config as pc
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON body"}, status=400)
        try:
            pc.set_primary(body.get("name"))
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        self._reload_escalation()
        return web.json_response(pc.list_providers())

    async def _handle_providers_delete(self, request: web.Request) -> web.Response:
        if not self._validate_http_request(request):
            return self._auth_error()
        from intergen import provider_config as pc
        from intergen.cloud.http_adapter import delete_secret
        name = request.match_info.get("name", "")
        removed = pc.remove_provider(name)
        if removed:
            try:
                delete_secret(pc.keyring_id_for(name))
            except Exception as e:  # noqa: BLE001 — config removal already done
                logger.debug("keyring delete for %s: %s", name, type(e).__name__)
        self._reload_escalation()
        return web.json_response(pc.list_providers())

    def _reload_escalation(self) -> None:
        """Rebuild the EscalationManager from fresh config after a panel change,
        reusing the existing scan floor. No daemon restart needed."""
        if self._router is None:
            return
        try:
            from intergen.config import Config
            from intergen.escalation import EscalationManager
            cfg = Config()  # re-reads /etc + ~/.config layers
            old = getattr(self._router, "_escalation", None)
            scanner = getattr(old, "_scanner", None)
            self._router._escalation = EscalationManager.from_config(
                cfg.get("escalation"), cfg.get("providers"), scanner=scanner)
            logger.info("Phone-a-friend reloaded: %d provider(s)",
                        len(self._router._escalation.list_providers()))
        except Exception as e:  # noqa: BLE001 — a reload failure must not 500 the panel
            logger.warning("Escalation reload failed: %s", type(e).__name__)

    def _token_usage(self) -> dict[str, int]:
        """Cumulative prompt/completion/total tokens from llama.cpp's tokenizer."""
        usage = getattr(self._llm, "token_usage", None)
        if callable(usage):
            try:
                return usage()
            except Exception:  # noqa: BLE001
                pass
        return {"prompt": 0, "completion": 0, "total": 0}

    def _cache_stats(self) -> dict[str, Any]:
        """Tool-cache hit/miss/hit-rate (the read-through cache, phase 5)."""
        cache = getattr(self._tools, "_cache", None)
        if cache is None:
            return {"hits": 0, "misses": 0, "hit_rate": 0.0}
        hits, misses = int(cache.hits), int(cache.misses)
        total = hits + misses
        return {
            "hits": hits, "misses": misses,
            "hit_rate": round(hits / total * 100, 1) if total else 0.0,
        }

    def _model_name(self) -> str:
        """Served model name via the llama_manager the aggregator already holds."""
        llama = getattr(self._health_agg, "_llama", None) if self._health_agg else None
        return getattr(llama, "model_name", None) or "local"

    def _model_perf(self) -> dict[str, Any]:
        """Local model identity + throughput (llama.cpp timings) for the
        Performance tab's model table."""
        perf: dict[str, Any] = {
            "model": self._model_name(), "calls": 0,
            "prompt_tps": 0.0, "gen_tps": 0.0,
            "avg_ttft_ms": 0.0, "p95_latency_ms": 0.0,
            "prompt_tokens": 0, "completion_tokens": 0,
        }
        fn = getattr(self._llm, "model_perf", None)
        if callable(fn):
            try:
                perf.update(fn())
            except Exception:  # noqa: BLE001
                pass
        return perf

    async def _handle_metrics_usage(self, request: web.Request) -> web.Response:
        """GET /api/metrics/usage — tool stats + query type distribution."""
        if not self._check_ready():
            return web.Response(text=_503_BODY, status=503,
                                content_type="application/json")
        if not self._validate_http_request(request):
            return self._auth_error()
        data: dict[str, Any] = {"type": "metrics_usage"}
        if self._metrics:
            ms = self._metrics.get_status()
            counters = ms.get("counters", {})
            data["requests"] = counters.get("requests", 0)
            data["escalations"] = counters.get("escalations", 0)
            data["llm_calls"] = counters.get("llm_calls", 0)
            # Query-type distribution (router increments qtype:<type> per request).
            data["query_types"] = {
                k.split(":", 1)[1]: v for k, v in counters.items()
                if k.startswith("qtype:")
            }
        # Per-tool invocation counts (Top Tools) straight from the registry.
        getter = getattr(self._tools, "get_tool_call_counts", None)
        data["tool_counts"] = getter() if callable(getter) else {}
        return web.json_response(data)

    async def _handle_metrics_realtime(self, request: web.Request) -> web.Response:
        """GET /api/metrics/realtime — current snapshot for HUD widgets."""
        if not self._check_ready():
            return web.Response(text=_503_BODY, status=503,
                                content_type="application/json")
        if not self._validate_http_request(request):
            return self._auth_error()
        data: dict[str, Any] = {
            "type": "metrics_realtime",
            "connections": len(self._connections),
            "server_uptime": round(time.monotonic() - self._startup_time),
        }
        if self._governance:
            data["governance"] = self._governance.health_snapshot()
        if self._metrics:
            data.update(self._metrics.get_status())
        return web.json_response(data)

    # -- WebSocket handler --------------------------------------------------

    async def _handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        """WebSocket endpoint — auth via Sec-WebSocket-Protocol subprotocol.

        Token passed as 'bearer.<token>' subprotocol component in the
        Sec-WebSocket-Protocol header. Validated BEFORE upgrade accept
        via hmac.compare_digest. Invalid auth returns HTTP 401 — the
        WebSocket upgrade never occurs.
        """
        if not self._check_ready():
            return web.Response(status=503,
                                text="InterGen components not yet initialized.")

        expected_token = self._load_auth_token()
        source_interface = request.query.get("source_interface", "web")

        if source_interface not in VALID_SOURCE_INTERFACES:
            source_interface = "web"

        token = self._extract_ws_token(request)
        if not token or not expected_token or not hmac.compare_digest(token, expected_token):
            logger.warning("WebSocket auth rejected (source=%s, ip=%s)",
                           source_interface, request.remote)
            return web.Response(status=401, text="Invalid auth token.")

        # heartbeat=: aiohttp autopings every 30s and closes the socket if the
        # peer fails to pong. Without it a half-open connection (dead TCP, peer
        # never notified) lingers in self._connections forever and the dispatch
        # loop blocks on a read that never returns — the G3-17 "thinking hangs
        # forever" zombie. The autoping reaps dead clients and fires the peer's
        # onclose so it reconnects.
        ws = web.WebSocketResponse(protocols=["intergen"], heartbeat=30.0)
        await ws.prepare(request)

        client_id = f"{source_interface}_{uuid.uuid4().hex[:12]}"
        # This connection's own conversation. Asked of the router so it carries
        # a relevance index over ITS OWN turns; built plainly if no router is
        # wired yet, because a connection without a conversation cannot be
        # served at all.
        conversation = (self._router.new_conversation()
                        if self._router is not None
                        and hasattr(self._router, "new_conversation")
                        else new_conversation_state())
        ctx = ConnectionContext(
            client_id=client_id,
            source_interface=source_interface,
            ws=ws,
            auth_token=token,
            user_agent=request.headers.get("User-Agent", ""),
            conversation=conversation,
        )
        # Each connection is its own conversation (Claude-Code-style): every chat
        # is its own listed, persisted session — not a shared "default" that the
        # next chat overwrites. Lazy: the record is written on the first message,
        # so an idle connect does not spawn a blank session.
        ctx.current_session_id = f"session_{uuid.uuid4().hex[:8]}"
        self._connections[client_id] = ctx

        logger.info("WebSocket connected: %s (source=%s, total=%d)",
                     client_id, source_interface, len(self._connections))

        # Send connected message
        connected_msg = self._build_connected_message(ctx)
        await ws.send_json(connected_msg)
        # Populate the sidebar immediately with the existing chats.
        await self._send_session_list(ctx)

        # Message dispatch loop
        try:
            await self._dispatch_loop(ctx)
        except _CLIENT_GONE:
            logger.debug("WebSocket %s connection reset", client_id)
        except Exception:
            logger.exception("WebSocket %s error in dispatch loop", client_id)
        finally:
            # Cancel an in-flight turn so it can't write into a closing
            # socket (and so a turn paused on a gate can't outlive the conn).
            if ctx.turn_task is not None and not ctx.turn_task.done():
                ctx.turn_task.cancel()
            # Persist session before cleanup
            if ctx.session_history and ctx.current_session_id:
                try:
                    self._sessions.save(
                        ctx.current_session_id,
                        ctx.session_history,
                    )
                except Exception:
                    logger.debug("Failed to persist session on disconnect",
                                 exc_info=True)
            self._connections.pop(client_id, None)
            if not ws.closed:
                await ws.close()
            logger.info("WebSocket disconnected: %s (total=%d)",
                         client_id, len(self._connections))

        return ws

    def _build_connected_message(self, ctx: ConnectionContext) -> dict[str, Any]:
        """Build the {type:"connected"} message per protocol §connection."""
        msg: dict[str, Any] = {
            "type": "connected",
            "client_id": ctx.client_id,
            "source_interface": ctx.source_interface,
            "timestamp": time.time(),
        }
        if self._governance:
            msg["system_status"] = self._build_system_status()
        return msg

    # -- Message dispatch ---------------------------------------------------

    async def _dispatch_loop(self, ctx: ConnectionContext) -> None:
        """Main WebSocket message loop.

        Reads messages from the client, looks up the handler by type,
        and dispatches. Runs until the client disconnects or an error
        occurs.
        """
        handler_map: dict[str, Callable] = {
            "message": self._handle_client_message,
            "gate_decision": self._handle_gate_decision,
            "switch_model": self._handle_switch_model,
            "slash_command": self._handle_slash_command,
            "frontier_escalate": self._handle_frontier_escalate,
            "switch_session": self._handle_switch_session,
            "new_session": self._handle_new_session,
            "list_sessions": self._handle_list_sessions,
            "request_health": self._handle_request_health,
            "request_governance": self._handle_request_governance,
            "request_metrics": self._handle_request_metrics,
        }

        async for msg in ctx.ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    await ctx.ws.send_json(_make_error(
                        "invalid_json", "Failed to parse message as JSON."
                    ))
                    continue

                msg_type = data.get("type", "")
                handler = handler_map.get(msg_type)
                if handler is None:
                    await ctx.ws.send_json(_make_error(
                        "unknown_type", f"Unknown message type: {msg_type}"
                    ))
                    continue

                # A chat turn runs for many seconds and may PAUSE on an
                # interactive gate (await gate_future). It must NOT be awaited
                # inline in this receive loop: while suspended it would block
                # the loop from ever reading the very gate_decision — or the
                # heartbeat pong — that releases it. That deadlock is the F2
                # deny-hang: aiohttp reaps the starved socket as a ~45s
                # pong-timeout close, and the deny friendly-refusal never
                # sends. So turns run as a background task; the receive loop
                # stays free to dispatch gate_decision / cancel / pong
                # concurrently. Serialize turn-vs-turn with a busy guard.
                # Other handlers are short and stay inline under cmd_lock;
                # gate_decision under cmd_lock runs fine because the turn task
                # does NOT hold cmd_lock while it awaits the gate.
                if msg_type == "message":
                    if ctx.turn_task is not None and not ctx.turn_task.done():
                        await ctx.ws.send_json(_make_error(
                            "busy",
                            "I'm still working on your previous message — "
                            "give me just a moment."
                        ))
                        continue
                    ctx.turn_task = asyncio.create_task(
                        self._run_turn(ctx, data)
                    )
                    continue

                try:
                    async with ctx.cmd_lock:
                        await handler(ctx, data)
                except _CLIENT_GONE as exc:
                    # Client vanished mid-handler (e.g. closed the tab, or a
                    # half-open socket). Normal — stop the loop, let finally
                    # clean up. No traceback, no error-send into a dead socket.
                    logger.debug(
                        "Client %s disconnected mid-handler (%s); closing.",
                        ctx.client_id, type(exc).__name__,
                    )
                    break
                except Exception:
                    logger.exception(
                        "Error handling message type=%s for client=%s",
                        msg_type, ctx.client_id,
                    )
                    # Best-effort error notice; ignore if the client is gone too.
                    try:
                        await ctx.ws.send_json(_make_error(
                            "internal_error",
                            "An internal error occurred processing your request."
                        ))
                    except _CLIENT_GONE:
                        break

            elif msg.type == WSMsgType.ERROR:
                logger.error("WebSocket error for %s: %s",
                              ctx.client_id, ctx.ws.exception())
                break
            elif msg.type == WSMsgType.CLOSE:
                break

    # -- Client→Server message handlers -------------------------------------

    async def _run_turn(self, ctx: ConnectionContext,
                        data: dict[str, Any]) -> None:
        """Run a chat turn off the receive loop.

        A turn may pause on an interactive gate, so it cannot be awaited
        inline in _dispatch_loop without deadlocking the socket (see the
        comment there). It runs as its own task instead. Being detached, it
        owns its full error handling and MUST leave the client terminal
        clean on EVERY exit — the "no turn ever wedges" invariant: whether a
        turn ends in an answer, a refusal, or a crash, the user always gets a
        deterministic terminal reply, never silence.
        """
        try:
            # M1 Glass Pipeline: mint ONE turn id for the whole web turn and
            # bind it for the task's duration. Everything downstream — router
            # verdicts, decisions, prompt bytes, the streamed model output, the
            # delivered bytes — shares this turn_id. The one seam a ContextVar
            # cannot cross is the LLM worker THREAD; _stream_llm_response binds
            # the context explicitly (glass.bind_context) at the submit.
            _gturn = glass.new_turn_id()
            with glass.turn(_gturn, "web"):
                glass.emit("route", "turn_start",
                           detail={"user_msg": data.get("content", "")})
                # ACKNOWLEDGE BEFORE ROUTING. The browser cannot distinguish a
                # server that is working from one that has died, and its
                # whole-turn failsafe acts on that difference: with no frame in
                # hand it eventually hides the indicator and force-closes the
                # socket. This frame is that difference. It carries no result
                # and waits on nothing, so it is sent before any routing work
                # begins — a turn that is slow to answer is still visibly a
                # turn that arrived.
                await ctx.ws.send_json({
                    "type": "turn_ack",
                    "turn_id": _gturn,
                })
                await self._handle_client_message(ctx, data)
        except _CLIENT_GONE:
            logger.debug(
                "Client %s disconnected mid-turn; turn abandoned.",
                ctx.client_id,
            )
        except asyncio.CancelledError:
            # Connection teardown cancelled the turn — the socket is going
            # away, nothing to send. Re-raise so the task records as cancelled.
            raise
        except Exception:
            logger.exception("Turn failed for client=%s", ctx.client_id)
            # No-wedge backstop: a crashed turn must still terminate the
            # client cleanly rather than leave the thinking pill spinning
            # until the heartbeat reaps the socket.
            try:
                await ctx.ws.send_json(_make_error(
                    "internal_error",
                    "Something went wrong on my end with that one. "
                    "Could you try again?"
                ))
            except _CLIENT_GONE:
                pass

    async def _await_route_within_deadline(
        self,
        ctx: ConnectionContext,
        route_future: "asyncio.Future",
    ) -> Any:
        """Await routing, charging the deadline only for time the SERVER spent.

        A turn may legitimately sit inside route() for minutes with a consent
        card on screen while the person decides. That wait is theirs, not a
        stall — the browser has the card in hand and is not waiting on us — so
        the clock is charged only while no card is pending. Raises TimeoutError
        once the server's own working time passes SERVER_ROUTE_DEADLINE_S.

        What this bounds is what the USER experiences, not what the machine
        does: route() runs in a worker thread and cannot be interrupted, so on a
        timeout that thread runs on and its eventual result is discarded. Any
        state route() mutates on the way is still mutated. The guarantee is a
        truthful terminal frame instead of silence, and that is the guarantee
        the person in front of the browser actually needs.
        """
        charged = 0.0
        while True:
            done, _pending = await asyncio.wait(
                {route_future}, timeout=_ROUTE_DEADLINE_TICK_S)
            if done:
                return route_future.result()
            if ctx.pending_gate is not None:
                continue  # the person is deciding; their time is not ours
            charged += _ROUTE_DEADLINE_TICK_S
            if charged >= SERVER_ROUTE_DEADLINE_S:
                raise TimeoutError(
                    f"routing exceeded {SERVER_ROUTE_DEADLINE_S}s of server "
                    f"working time")

    async def _handle_client_message(self, ctx: ConnectionContext,
                                      data: dict[str, Any]) -> None:
        """Handle a chat message from the client.

        Routes the message through the ConversationRouter, streams tokens
        back to the client, and handles tool calls with provenance gate
        interceptions.
        """
        content = data.get("content", "").strip()
        if not content:
            await ctx.ws.send_json(_make_error(
                "empty_message", "Message content cannot be empty."
            ))
            return

        # Unify the WS message turn id with the glass turn id (minted in
        # _run_turn) so the client-facing turn_id and the trace turn_id are one.
        turn_id = glass.current_turn_id() or uuid.uuid4().hex[:12]

        if self._router is None:
            await ctx.ws.send_json(_make_error(
                "router_unavailable",
                "InterGen conversation router is not initialized."
            ))
            return

        # Append user message to session history
        ctx.session_history.append(
            Message(role=MessageRole.USER, content=content)
        )

        user_msg = content

        # Extract image data for vision models (M-002)
        image_data = data.get("image_data", None)

        # Try keyword/semantic/cache fast paths first (P0-P2)
        # These are non-streaming and return complete results
        route_start = time.monotonic()
        # decide_only: fast paths still return final text; LLM paths return just
        # the route decision so the answer is generated ONCE (streamed below),
        # not twice (the router's chat() pass was redundant for the WS path).
        #
        # single-gate review: route() is offloaded to a worker thread and given the web review
        # bridge. The fast/offer-accept path EXECUTES tools inside route() (a
        # staged "yes" runs the offered command through the registry); running
        # that on the event-loop thread while its gate bridged back to the async
        # card would DEADLOCK the loop (the observed offer/deny loop's home).
        # Offloaded, the registry's gate calls the bridge from the worker thread,
        # which schedules the card on the free loop — no deadlock. A server-level
        # lock serializes route() across connections (the loop thread used to do
        # that implicitly); lazily created here so it binds to this loop.
        loop = asyncio.get_running_loop()
        if self._route_lock is None:
            self._route_lock = asyncio.Lock()
        _route_review_cb = self._make_web_review_callback(ctx, turn_id, loop)
        # The router is shared by every connection and by the desktop bus, so
        # the turn names the conversation it belongs to. The binding is held
        # only while route() runs, under the same lock that serializes route()
        # across connections; everything the turn writes back afterwards names
        # the conversation explicitly instead, because by then another
        # connection may already be bound.
        async with self._route_lock:
            # A ContextVar does not cross a thread, so route() ran with no turn
            # bound and every row it wrote — every routing verdict, every
            # decision it made on the way — landed as "no-turn" and could not be
            # joined to the turn that caused it. Bind the context here and run
            # the callable through it (REC-17).
            _route_ctx = glass.bind_context()
            _route_future = loop.run_in_executor(
                None,
                lambda: _route_ctx.run(
                    self._router.route,
                    user_msg, decide_only=True,
                    conversation=ctx.conversation,
                    review_callback=_route_review_cb,
                ),
            )
            try:
                result = await self._await_route_within_deadline(
                    ctx, _route_future)
            except TimeoutError:
                # The server has now spent longer routing than it allows itself,
                # and the browser's own failsafe is still ahead of us. Say so and
                # end the turn: a truthful stop is worth more to the person
                # waiting than an answer that arrives after the socket is gone.
                glass.emit("route", "deadline_exceeded", detail={
                    "iface": "web",
                    "deadline_s": SERVER_ROUTE_DEADLINE_S,
                })
                # The turn ends HERE, and the record says so in the vocabulary
                # every other ending uses. Without this the turn would still
                # terminate — glass.turn() synthesizes an ending for a block that
                # simply returns — but it would read "unreported", which is the
                # word for an ending nobody accounted for. This one is accounted
                # for: the deadline fired.
                glass.emit("delivery", "timeout", detail={
                    "iface": "web",
                    "deadline_s": SERVER_ROUTE_DEADLINE_S,
                    "abandoned": "the routing thread runs on; its result is "
                                 "discarded",
                })
                logger.warning(
                    "Routing exceeded the %.1fs server deadline for client=%s; "
                    "ending the turn and abandoning the routing thread.",
                    SERVER_ROUTE_DEADLINE_S, ctx.client_id,
                )
                await ctx.ws.send_json(_make_error(
                    "route_timeout",
                    "That one is taking me longer to work out than it should. "
                    "I've stopped rather than leave you waiting with nothing — "
                    "please try asking again.",
                ))
                return

        # M3(i): a prefixed "yes" over a live offer routed the tail through the
        # pipeline; route() set the reminder on the result (web is decide_only, so
        # it is NOT inlined there). Fold it into the DELIVERED text for the
        # non-streamed dispositions so a fast-path tail still carries the reminder.
        _reminder = getattr(result, "reoffer_reminder", None)
        _delivered = (result.text.rstrip() + "\n\n" + _reminder
                      if _reminder and result.text else result.text)

        if result.source in ("cache", "keyword", "semantic", "decomposed",
                              "identity", "memory"):
            # Fast path — send complete response without streaming
            msg = {
                "type": "response",
                "turn_id": turn_id,
                "content": _delivered,
                "source": result.source,
                "handled": result.handled,
                # Per-turn routing confidence (semantic cosine score); None for
                # deterministic routes that resolved before semantic matching.
                "confidence": self._router.last_route_confidence(),
            }
            # Terse fast-path summaries carry the full raw output for the D-2
            # "show full output" expander (e.g. the raw df/lscpu table).
            if getattr(result, "full_output", ""):
                msg["full_output"] = result.full_output
            if result.tool_results:
                msg["tool_calls"] = [
                    {"name": tr.name, "success": tr.success,
                     "summary": tr.content[:200]}
                    for tr in result.tool_results
                ]
            await ctx.ws.send_json(msg)
            # ANSWER->DISPATCH LINKAGE (see AnswerLinkage): what the reply was
            # composed from, or an explicit "undeclared" so an uninstrumented
            # path stays visible rather than reading as code-owned.
            _link = getattr(result, "answer_linkage", None)
            if not isinstance(_link, AnswerLinkage):
                _link = None  # see the dbus surface: only a real linkage speaks
            glass.emit("delivery", "final", detail={
                "iface": "web", "text": _delivered, "source": result.source,
                "handled": result.handled, "fast_path": True,
                "tool_count": len(result.tool_results),
                "answer_linkage": (_link.as_detail() if _link is not None
                                   else {"kind": "undeclared"})})
            # M8-2 RESULT DELIVERY INVARIANT (fast path): a successful dispatch whose
            # value did not reach the delivered answer is a NAMED, LOUD defect.
            for _tr, _reason in safety.find_unconsumed_dispatches(
                    _delivered, result.tool_results, _link):
                glass.emit("delivery", "dispatch_unconsumed", detail={
                    "tool": _tr.name, "reason": _reason, "iface": "web",
                    "fast_path": True})
                logger.warning(
                    "M8-2: dispatch %s succeeded but its result did not reach the "
                    "delivered answer (%s) — fast path", _tr.name, _reason)
            ctx.session_history.append(
                Message(role=MessageRole.ASSISTANT, content=_delivered)
            )
            # M2a: fast (deterministic) web turns also feed the model's buffer so
            # "the model sees what the user sees" holds for EVERY web turn — the
            # The "How much RAM? → Can I add any?" fast-path miss. Idempotent
            # against sub-paths that self-appended inside route().
            if _delivered.strip():
                self._router._append_history(user_msg, _delivered,
                                            state=ctx.conversation)
            await self._persist_and_list(ctx)
            return

        # LLM path (P3/P4) — streaming required
        if result.source in ("llm_tools", "llm_freeform"):
            await self._stream_llm_response(ctx, turn_id, user_msg,
                                             result, image_data)
            await self._persist_and_list(ctx)
            return

        # Fallback — send whatever we got. This catches every source outside the
        # fast-path allowlist and the streamed pair (direct_answer, explain,
        # system_map, current_data_offer, capability_question, safety_decline,
        # file_lifecycle_offer, ip_answer, …), and it delivers real bytes to the
        # user, so it records them like the other two dispositions do.
        await ctx.ws.send_json({
            "type": "response",
            "turn_id": turn_id,
            "content": _delivered,
            "source": result.source,
            "handled": result.handled,
            "confidence": self._router.last_route_confidence(),
        })
        # M1: the final bytes delivered to chat are reconstructible from the trace
        # ALONE. This disposition used to leave only a route/turn_start behind — a
        # real answer reached the user and the trace could not say what it was, and
        # the M8-2 result-delivery invariant below never ran for the whole class.
        _fb_link = getattr(result, "answer_linkage", None)
        if not isinstance(_fb_link, AnswerLinkage):
            _fb_link = None  # only a genuine linkage may speak for an answer
        glass.emit("delivery", "final", detail={
            "iface": "web", "text": _delivered, "source": result.source,
            "handled": result.handled, "fast_path": False,
            "tool_count": len(result.tool_results),
            "answer_linkage": (_fb_link.as_detail() if _fb_link is not None
                               else {"kind": "undeclared"})})
        # M8-2 RESULT DELIVERY INVARIANT (fallback): a successful dispatch whose
        # value did not reach the delivered answer is a NAMED, LOUD defect here too.
        for _tr, _reason in safety.find_unconsumed_dispatches(
                _delivered, result.tool_results, _fb_link):
            glass.emit("delivery", "dispatch_unconsumed", detail={
                "tool": _tr.name, "reason": _reason, "iface": "web",
                "fast_path": False})
            logger.warning(
                "M8-2: dispatch %s succeeded but its result did not reach the "
                "delivered answer (%s) — fallback path", _tr.name, _reason)
        ctx.session_history.append(
            Message(role=MessageRole.ASSISTANT, content=_delivered)
        )
        # M2a: the fallback (offer-resolution / IP / clarify) web turns too —
        # every web turn feeds the model-facing buffer (idempotent).
        if _delivered.strip():
            self._router._append_history(user_msg, _delivered,
                                            state=ctx.conversation)
        await self._persist_and_list(ctx)

    async def _send_session_list(self, ctx: ConnectionContext) -> None:
        """Push the session list so the sidebar shows every chat (current +
        past), Claude-Code-style. Best-effort; never breaks a turn."""
        try:
            sessions = self._sessions.list_sessions(ctx.source_interface)
        except Exception:
            logger.debug("list_sessions failed", exc_info=True)
            return
        try:
            await ctx.ws.send_json({
                "type": "session_list",
                "sessions": sessions,
                "current_session_id": ctx.current_session_id,
            })
        except Exception:
            logger.debug("session_list send failed", exc_info=True)

    async def _persist_and_list(self, ctx: ConnectionContext) -> None:
        """Persist the live conversation incrementally (survives a restart and
        appears in the sidebar at once) and push the refreshed list."""
        if ctx.session_history and ctx.current_session_id:
            try:
                self._sessions.save(ctx.current_session_id, ctx.session_history)
            except Exception:
                logger.debug("incremental session persist failed", exc_info=True)
        await self._send_session_list(ctx)

    async def _handle_list_sessions(self, ctx: ConnectionContext,
                                    data: dict[str, Any]) -> None:
        """Explicit client request for the session list."""
        await self._send_session_list(ctx)

    async def _handle_frontier_escalate(self, ctx: ConnectionContext,
                                         data: dict[str, Any]) -> None:
        """Phone-a-friend: the user invoked 'Ask my frontier model' in the web UI
        (the GUI affordance half of decision #4, parity with the CLI ask-frontier).

        This is the genuine initial human-authorized hop: show-before-send consent
        (the user sees the exact outbound content + provider, must approve), then
        escalate(user_consented=True) — NOT egress-scanned, since the human just
        reviewed it. The escalate() runs in a worker thread (consent modal + provider
        HTTP are blocking) so the event loop is never blocked. Fail-safe: no manager /
        no provider / declined / error all return a clean message, nothing sent.
        """
        content = (data.get("content") or "").strip()
        if not content:
            await ctx.ws.send_json(_make_error(
                "empty_message", "Nothing to send to the frontier model."))
            return
        manager = getattr(self._router, "_escalation", None) if self._router else None
        if manager is None or manager._primary_provider_name() is None:
            await ctx.ws.send_json({
                "type": "frontier_response", "sent": False,
                "content": ("No frontier model is configured. Add a provider to "
                            "~/.config/intergen/ (the human-only config)."),
            })
            return
        provider = manager._primary_provider_name()

        def _run():
            from intergen.consent_modal import prompt_send_consent
            from intergen.interfaces.types import Message, MessageRole
            if not prompt_send_consent(content, provider,
                                       reason="you asked to reach your frontier model"):
                return (False, "Cancelled — nothing was sent to the frontier model.")
            resp = manager.escalate(
                [Message(role=MessageRole.USER, content=content)],
                reason="user-invoked phone-a-friend (web)", user_consented=True,
            )
            return (True, resp.text)

        loop = asyncio.get_event_loop()
        # Bind the turn across the thread hop, so the consent prompt and the
        # escalation itself are joinable to the turn that asked for them.
        _esc_ctx = glass.bind_context()
        try:
            sent, text = await loop.run_in_executor(
                None, lambda: _esc_ctx.run(_run))
        except Exception as exc:  # noqa: BLE001 — never crash the socket on escalation
            logger.error("frontier_escalate failed: %s", type(exc).__name__)
            sent, text = False, f"Escalation failed: {type(exc).__name__}"
        await ctx.ws.send_json({
            "type": "frontier_response", "sent": sent,
            "content": text, "provider": provider if sent else None,
        })
        if sent:
            ctx.session_history.append(
                Message(role=MessageRole.ASSISTANT, content=text))

    async def _handle_gate_decision(self, ctx: ConnectionContext,
                                     data: dict[str, Any]) -> None:
        """Handle a provenance gate decision from the client.

        The client responds to a gate_prompt with allow, allow_conversation,
        or deny. This handler signals the awaiting streaming coroutine
        via the gate_future on the connection context.
        """
        tool_call_id = data.get("tool_call_id", "")
        decision = data.get("decision", "deny")

        if not tool_call_id:
            await ctx.ws.send_json(_make_error(
                "missing_field",
                "gate_decision requires tool_call_id."
            ))
            return

        valid_decisions = ("allow", "allow_conversation", "deny")
        if decision not in valid_decisions:
            await ctx.ws.send_json(_make_error(
                "invalid_decision",
                f"Invalid gate decision: {decision}. "
                f"Must be: {', '.join(valid_decisions)}."
            ))
            return

        logger.info("Gate decision for %s: %s (client=%s)",
                     tool_call_id, decision, ctx.client_id)

        # Signal the streaming coroutine waiting on this gate
        if ctx.gate_future is not None and not ctx.gate_future.done():
            ctx.gate_future.set_result(decision)
        else:
            # No awaiting stream — send resolved directly
            await ctx.ws.send_json({
                "type": "gate_resolved",
                "tool_call_id": tool_call_id,
                "decision": decision,
            })

        ctx.pending_gate = None

    async def _handle_switch_model(self, ctx: ConnectionContext,
                                    data: dict[str, Any]) -> None:
        """Request model tier change (small/medium/large).

        Stores the preference on the connection context. An actual model
        switch requires reloading a different GGUF in llama-server, which
        takes tens of seconds. The preference is stored so subsequent
        requests use the intended tier; the model_manager can trigger a
        reload when the model is available.
        """
        tier = data.get("tier", "medium")
        valid = {"small", "medium", "large"}
        if tier not in valid:
            await ctx.ws.send_json(_make_error(
                "invalid_model_tier",
                f"Unknown model tier: {tier}. Must be: {', '.join(sorted(valid))}."
            ))
            return
        ctx.model_tier = tier
        logger.info("Model tier set to %s (client=%s)", tier, ctx.client_id)
        await ctx.ws.send_json({
            "type": "model_changed",
            "tier": tier,
        })

    async def _handle_slash_command(self, ctx: ConnectionContext,
                                     data: dict[str, Any]) -> None:
        """Handle a slash command from the client.

        Routes through the B-006 Slash Command Router when available.
        For now, delegates standard commands directly.
        """
        command = data.get("command", "").strip()
        if not command:
            await ctx.ws.send_json(_make_error(
                "empty_command", "Slash command cannot be empty."
            ))
            return

        cmd_lower = command.lower()

        if cmd_lower == "/new":
            ctx.session_history.clear()
            await ctx.ws.send_json({
                "type": "session_created",
                "client_id": ctx.client_id,
            })
        elif cmd_lower == "/clear":
            ctx.session_history.clear()
            await ctx.ws.send_json({"type": "buffer_cleared"})
        elif cmd_lower in ("/health", "/status"):
            await self._handle_request_health(ctx, {})
        elif cmd_lower == "/governance":
            await self._handle_request_governance(ctx, {})
        elif cmd_lower == "/metrics":
            await self._handle_request_metrics(ctx, {})
        elif cmd_lower == "/tier":
            await ctx.ws.send_json({
                "type": "tier_status",
                "tier": (self._governance.health_snapshot()
                         if self._governance else "unavailable"),
            })
        elif cmd_lower == "/screenshot":
            # The button and the typed command are both a direct human request,
            # so this runs the REGISTERED tool through the ordinary gate rather
            # than a private capture path: consent, governance and the audit
            # record stay exactly where every other tool call meets them.
            self._spawn_tool_task(ctx, "take_screenshot", {})
        elif cmd_lower == "/file":
            await self._handle_file_command(ctx, data)
        else:
            # Unknown command — let the client know
            await ctx.ws.send_json(_make_error(
                "unknown_command",
                f"Unknown slash command: {command}"
            ))

    async def _handle_file_command(self, ctx: ConnectionContext,
                                    data: dict[str, Any]) -> None:
        """Load a file into the conversation buffer.

        Two routes, deliberately distinct:

        - ``content`` present — the human picked the file in their own browser's
          chooser and the bytes came with the request. Nothing on this machine
          is read, so there is no ingress read to gate; the content is the
          user's own message content.
        - ``path`` present — the user named a path for the ASSISTANT to read.
          That is an ingress read of this machine, so it goes through the
          registered ``read_file`` tool and its gate. Never open the path here:
          a private read would bypass the provenance gate that exists precisely
          because file bodies can carry injection bytes.
        """
        content = data.get("content")
        if content is not None:
            filename = str(data.get("filename") or "file").strip() or "file"
            text = str(content)
            ctx.session_history.append(
                Message(role=MessageRole.USER,
                        content=f"[file: {filename}]\n{text}")
            )
            await ctx.ws.send_json({
                "type": "file_loaded",
                "filename": filename,
                "chars": len(text),
            })
            return

        path = str(data.get("path") or "").strip()
        if not path:
            await ctx.ws.send_json(_make_error(
                "missing_field",
                "/file requires a chosen file or a path.",
            ))
            return
        self._spawn_tool_task(ctx, "read_file", {"path": path})

    def _spawn_tool_task(self, ctx: ConnectionContext, tool_name: str,
                          arguments: dict[str, Any]) -> None:
        """Run a user-invoked tool OFF the receive loop.

        This is the F2 deny-hang discipline the chat turn already follows, and
        it applies here for the same reason: the tool may pause on an
        interactive gate, and anything awaited inline in _dispatch_loop blocks
        that loop from reading the very gate_decision that would release it —
        the card renders, the user clicks, and the click is never read. Detached
        like a turn, the task owns its own error reporting so the client always
        gets a terminal line.
        """
        task = asyncio.create_task(
            self._run_user_invoked_tool(ctx, tool_name, arguments))
        ctx.tool_tasks.add(task)
        task.add_done_callback(ctx.tool_tasks.discard)

    async def _run_user_invoked_tool(self, ctx: ConnectionContext,
                                      tool_name: str,
                                      arguments: dict[str, Any]) -> None:
        """Run a tool the user invoked directly, through the ordinary gate.

        Provenance is USER_DIRECT — the user pressed the button or typed the
        command, which is the literal definition of the label. The gate still
        runs: USER_DIRECT states WHO asked, never that the action is free.

        Result handling matches the streaming path's contract — a
        ``tool_executed`` line carrying the structured summary, the full payload
        for the expander — so a user-invoked tool renders identically to one the
        model called, and the output joins the history the model reads next turn.
        """
        if not self._tools:
            await ctx.ws.send_json(_make_error(
                "tool_unavailable",
                f"No tool registry is available to run {tool_name}.",
            ))
            return
        if self._tools.get_tool(tool_name) is None:
            await ctx.ws.send_json(_make_error(
                "tool_unavailable",
                f"Tool not registered: {tool_name}",
            ))
            return

        from intergen.interfaces.provenance import Provenance

        call = ToolCall(
            name=tool_name,
            arguments=dict(arguments),
            call_id=uuid.uuid4().hex[:12],
            source_of_request=Provenance.USER_DIRECT,
        )
        turn_id = uuid.uuid4().hex[:12]

        await ctx.ws.send_json({
            "type": "tool_ack",
            "tool_name": tool_name,
            "tool_call_id": call.call_id,
        })

        verdict = await self._evaluate_tool_with_gate(ctx, turn_id, call)
        if verdict != "approved":
            # Denied is a real outcome, reported as one. The optimistic
            # "working on it" line is never left standing over a refusal.
            await ctx.ws.send_json({
                "type": "tool_executed",
                "tool_name": tool_name,
                "success": False,
                "summary": f"{tool_name} was not run — the request was denied.",
            })
            return

        loop = asyncio.get_running_loop()
        # Bind the turn across the thread hop: a tool execution's rows, including
        # its gate decision, belong to the turn that invoked it.
        _tool_ctx = glass.bind_context()
        try:
            tr = await loop.run_in_executor(
                None,
                lambda: _tool_ctx.run(
                    self._tools.execute,
                    call,
                    ingress_tracker=ctx.conversation.ingress_tracker,
                    trust_state=ctx.conversation.trust_state,
                    review_callback=self._make_web_review_callback(
                        ctx, turn_id, loop),
                ),
            )
        except Exception as exc:                      # noqa: BLE001
            logger.exception("User-invoked tool %s failed", tool_name)
            await ctx.ws.send_json({
                "type": "tool_executed",
                "tool_name": tool_name,
                "success": False,
                "summary": f"{tool_name} failed: {exc}",
            })
            return

        summary = tr.model_summary or (tr.content or "")[:256]
        payload: dict[str, Any] = {
            "type": "tool_executed",
            "tool_name": tr.name,
            "success": tr.success,
            "summary": summary,
        }
        full = tr.content or ""
        if tr.model_summary is not None or len(full) > len(summary):
            payload["full_output"] = full
        await ctx.ws.send_json(payload)

        if tr.success and full:
            ctx.session_history.append(
                Message(role=MessageRole.USER,
                        content=f"[{tool_name} result]\n{full}")
            )

    async def _handle_switch_session(self, ctx: ConnectionContext,
                                      data: dict[str, Any]) -> None:
        """Switch to a different conversation session."""
        session_id = data.get("session_id", "")
        if not session_id:
            await ctx.ws.send_json(_make_error(
                "missing_field",
                "switch_session requires session_id."
            ))
            return

        try:
            SessionManager._validate_session_id(session_id)
        except ValueError:
            await ctx.ws.send_json(_make_error(
                "invalid_session_id",
                f"Invalid session_id: {session_id}"
            ))
            return

        if ctx.session_history:
            try:
                self._sessions.save(
                    ctx.current_session_id,
                    ctx.session_history,
                )
            except Exception:
                logger.debug("Failed to persist session on switch",
                             exc_info=True)

        ctx.current_session_id = session_id
        # The conversation the person is leaving ends here: its consent
        # decisions, its ingress watermark, its staged offers and its model
        # buffer belong to it and must not follow them into the next one. The
        # transcript of the conversation they are switching TO is loaded back
        # in afterwards, so the pane and the model's prompt are the same list.
        if self._router is not None and hasattr(
                self._router, "reset_conversation_state"):
            self._router.reset_conversation_state(ctx.conversation)
        loaded = self._sessions.load(session_id)
        ctx.session_history = (loaded.get("messages")
                               if loaded and loaded.get("messages") else [])

        logger.info("Session switched to %s (client=%s, history=%d msgs)",
                     session_id, ctx.client_id,
                     len(ctx.session_history))
        # Ship the loaded transcript WITH the switch. A message_count alone tells
        # the client how much history exists but gives it nothing to render, so
        # the user sees an empty pane for a session that demonstrably has
        # content — the count and the view disagree, which is the silent-failure
        # shape. One payload keeps the count and the rendered messages derived
        # from the same load, so they cannot drift.
        await ctx.ws.send_json({
            "type": "session_switched",
            "session_id": session_id,
            "message_count": len(ctx.session_history),
            "messages": _history_wire_format(ctx.session_history),
        })
        await self._send_session_list(ctx)

    async def _handle_new_session(self, ctx: ConnectionContext,
                                    data: dict[str, Any]) -> None:
        """Start a new empty session.
        Saves the current session to disk, then starts a fresh one.
        """
        if ctx.session_history:
            try:
                self._sessions.save(
                    ctx.current_session_id,
                    ctx.session_history,
                    category=data.get("category", ""),
                )
            except Exception:
                logger.debug("Failed to persist session on new_session",
                             exc_info=True)
        ctx.current_session_id = f"session_{uuid.uuid4().hex[:8]}"
        # The old conversation ENDS. Emptying the pane is not enough: the model
        # buffer, the consent decisions taken in it, the ingress watermark, the
        # offers left awaiting a yes and the turn index all belong to the
        # conversation that just finished, and a "yes" typed in the new one must
        # not be able to reach back into it.
        if self._router is not None and hasattr(
                self._router, "reset_conversation_state"):
            self._router.reset_conversation_state(ctx.conversation)
        else:
            ctx.session_history = []
        self._sessions.create(session_id=ctx.current_session_id,
                              source_interface=ctx.source_interface)
        await ctx.ws.send_json({
            "type": "session_created",
            "client_id": ctx.client_id,
            "session_id": ctx.current_session_id,
        })
        await self._send_session_list(ctx)

    async def _handle_request_health(self, ctx: ConnectionContext,
                                      data: dict[str, Any]) -> None:
        """Return a full health report (protocol §8)."""
        report = self._build_health_report()
        await ctx.ws.send_json(report)

    async def _handle_request_governance(self, ctx: ConnectionContext,
                                          data: dict[str, Any]) -> None:
        """Return the governance dashboard data (protocol §9)."""
        report = self._build_governance_report()
        await ctx.ws.send_json(report)

    async def _handle_request_metrics(self, ctx: ConnectionContext,
                                       data: dict[str, Any]) -> None:
        """Return the metrics dashboard data (MTR-001)."""
        data_out: dict[str, Any] = {"type": "metrics_report"}
        if self._metrics:
            data_out.update(self._metrics.get_status())
        if self._governance:
            data_out["governance"] = self._governance.health_snapshot()
        data_out["connections"] = len(self._connections)
        await ctx.ws.send_json(data_out)

    # -- Streaming LLM response ---------------------------------------------

    async def _stream_llm_response(self, ctx: ConnectionContext,
                                    turn_id: str, user_msg: str,
                                    route_result: RouteResult,
                                    image_data: str | None = None) -> None:
        """Stream an LLM-generated response token-by-token over WebSocket.

        Uses LLMRouter.stream_with_tools() in a thread to avoid blocking the
        event loop. Each token is sent as a stream_token message. When a
        ToolCall is encountered mid-stream, governance is checked and the
        provenance gate flow is triggered (pause → gate_prompt → await
        gate_decision → execute or deny → resume). After any tool execution,
        the tool result is synthesized via LLMRouter.continue_after_tool_call()
        and streamed as the final response.
        """
        if self._llm is None:
            await ctx.ws.send_json(_make_error(
                "llm_unavailable", "LLM router is not initialized."
            ))
            return

        # M3(i): when a prefixed "yes"/"no" over an offer routed its TAIL here, the
        # model must be prompted with the STRIPPED tail (route() published it on the
        # result) — prompting with "Yes, <tail>" while the offer sits in history
        # stalls the small model. user_msg stays the raw turn for turn_id/history/
        # delivery (what the user typed and saw); only generation uses the tail.
        _gen_input = getattr(route_result, "effective_input", "") or user_msg

        # System Map route: build grounded-synthesis messages from TRUE cached
        # system data and offer NO tools (the model reads real data instead of
        # fabricating; no tools means injected log/service text can't escalate).
        # Mirrors the CLI _try_system_map path so both surfaces behave the same.
        if route_result.source == "system_map":
            sysmap_data = (
                self._router._state_cache.get_system_map_data(_gen_input)
                if getattr(self._router, "_state_cache", None) else None
            )
            if sysmap_data:
                messages = self._router._build_system_map_messages(
                    _gen_input, sysmap_data)
            else:
                # Cache emptied since the route decision — fall back to the
                # no-fabricate freeform prompt rather than inventing state.
                messages = self._router._build_messages(_gen_input, with_tools=False)
            tool_schemas = []
        else:
            # Match the system prompt to the route: tool turns keep the
            # provenance directive (~197 tok); conversational turns drop it.
            _with_tools = route_result.source == "llm_tools"
            # L1 anti-fabrication grounding (Goal-2): on freeform how-to turns,
            # make true installed-tool facts available so the model grounds
            # instead of inventing (png2jpg) or defaulting to apt. None for
            # tool turns and non-subjects → those are untouched.
            _grounding = (self._router._grounding_context(_gen_input)
                          if route_result.source == "llm_freeform" else None)
            messages = self._router._build_messages(
                _gen_input, with_tools=_with_tools, grounding=_grounding)
            # Offer tools ONLY when the router's eligibility gate decided this
            # turn needs them (source == "llm_tools"). Freeform/conversational
            # turns must match the CLI path and attach NO tools — otherwise the
            # small model spuriously emits a tool call (e.g. run_command for
            # "say hi") that governance then denies, dead-ending the reply.
            # Empty schemas → stream_with_tools() falls through to a plain
            # stream(). (Root cause: the WS path had drifted from router.py's
            # validated tools-only-when-needed gate; see the tool-routing-
            # classification research.)
            tool_schemas = (self._tools.get_tool_schemas()
                            if (self._tools and route_result.source == "llm_tools")
                            else [])

        # Send stream_start
        model_display = getattr(self._llm, '_endpoint', '')
        if '8080' in model_display:
            model_display = 'local'
        await ctx.ws.send_json({
            "type": "stream_start",
            "turn_id": turn_id,
            "source": route_result.source,
            "model_name": model_display or "local",
        })

        # Hop-1 ack (perceived-latency): the instant a turn commits to the tool
        # path, greet the user so the slow LLM/tool round-trip doesn't feel like
        # a silent stall. Tool turns inherently route + execute + synthesize, so
        # they are always in the medium/slow band. The line asserts nothing, so
        # it composes with success, a gate prompt, or a refusal alike.
        if route_result.source == "llm_tools" and self._filler.available:
            await ctx.ws.send_json({
                "type": "tool_ack",
                "turn_id": turn_id,
                "text": self._filler.hop1(),
            })

        collected_tokens: list[str] = []
        tool_calls_made: list[ToolCall] = []
        tool_results: list[ToolResult] = []
        stream_started = time.monotonic()
        # Cap the grounded system-state answer (1-2 sentences) so generation
        # can't stretch the turn; conversational/tool turns keep the default.
        _max_tokens = (self._router._SYSTEM_MAP_MAX_TOKENS
                       if route_result.source == "system_map" else None)

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        _SENTINEL = object()

        def _run_llm() -> None:
            """Run the synchronous LLM streaming generator in a thread."""
            try:
                for item in self._llm.stream_with_tools(
                    messages, tools=tool_schemas, image_data=image_data,
                    max_tokens=_max_tokens,
                ):
                    loop.call_soon_threadsafe(queue.put_nowait, item)
                loop.call_soon_threadsafe(queue.put_nowait, _SENTINEL)
            except Exception as exc:
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    ("__error__", exc),
                )

        executor = ThreadPoolExecutor(max_workers=1)
        # M1: ContextVars do not auto-propagate into a worker thread; bind the
        # current context so the glass turn_id (and any active trace span) stays
        # attached to the llm.py model emissions that run inside _run_llm — the
        # one seam the design flagged.
        _gctx = glass.bind_context()
        executor.submit(lambda: _gctx.run(_run_llm))

        try:
            full_response = await self._process_llm_stream(
                ctx, turn_id, queue, _SENTINEL,
                collected_tokens, tool_calls_made, tool_results,
                messages, tool_schemas,
            )
        finally:
            executor.shutdown(wait=False)

        elapsed_ms = (time.monotonic() - stream_started) * 1000

        # Safety net — never leave the user staring at a blank reply. The small
        # local model occasionally emits an empty completion (no text, no tool
        # call), which would otherwise render as a silent non-answer. If the turn
        # produced neither text nor a tool result the user can see, send one
        # gentle, plain-language nudge instead of nothing. (Real answers always
        # carry text OR a tool result, so this never fires on a genuine reply.)
        if not full_response.strip() and not tool_results:
            full_response = (
                "Sorry — I didn't quite catch that. Could you rephrase it for me?"
            )
            collected_tokens.append(full_response)
            await ctx.ws.send_json({
                "type": "stream_token",
                "turn_id": turn_id,
                "token": full_response,
            })

        # M8-2 RESULT DELIVERY INVARIANT (backstop): after the raw-delivery recovery
        # above, a successful dispatch whose value STILL did not reach the delivered
        # answer — an empty delivery, or a deflection ("I don't have current data")
        # rendered ALONGSIDE a successful result (the compound sf_dispatch_run_command-20
        # shape) — is a NAMED, LOUD defect, never silent, AND IS NOW REPAIRED where the
        # value is genuinely missing: the tool's own output is carried into the answer
        # (safety.carry_result_into_answer) rather than left in a log while the user
        # keeps the wrong reply. An honest paraphrase is still never disturbed, because
        # it is not flagged in the first place; a substitution and an answer that already
        # states the result are named but NOT rewritten. Correcting full_response here is
        # exactly what the user keeps — see the M3(ii) note below on stream_end — and it
        # runs before both stream_end and the delivery/final row, so the trace carries
        # the bytes the user actually received.
        # The streamed path composes `full_response` itself: from the model's
        # synthesis over the FIRST tool result when anything dispatched, else
        # from free model text. Declared here rather than inherited, because the
        # RouteResult this path started from was decide_only and never carried
        # the composition that actually happened.
        _stream_link = (
            AnswerLinkage(kind="dispatch", tool=tool_results[0].name,
                          call_id=tool_results[0].call_id,
                          renderer="llm_synth_streamed")
            if tool_results else
            AnswerLinkage(kind="model", renderer="llm_stream"))
        for _tr, _reason in safety.find_unconsumed_dispatches(
                full_response, tool_results, _stream_link):
            _carried = safety.carry_result_into_answer(
                full_response, _tr, _reason)
            glass.emit("delivery", "dispatch_unconsumed", detail={
                "tool": _tr.name, "reason": _reason,
                "source": route_result.source,
                "repaired": _carried is not None})
            if _carried is not None:
                logger.warning(
                    "M8-2: dispatch %s succeeded but its result did not reach the "
                    "delivered answer (%s) — carried the result into the answer, "
                    "turn %s", _tr.name, _reason, turn_id)
                full_response = _carried
            else:
                logger.warning(
                    "M8-2: dispatch %s succeeded and the delivered answer is wrong "
                    "about it (%s) — not rewritten, see the glass row, turn %s",
                    _tr.name, _reason, turn_id)

        # M3(ii) honesty invariant: screen the model's DRAFT before delivery. On
        # the web path delivery == stream_end — the client REPLACES the streamed
        # tokens with full_response (app.js handleStreamEnd), so correcting
        # full_response here is exactly what the user keeps and what persists. Fires
        # only when nothing dispatched this turn; regeneration runs off-thread.
        _dispatched = any(getattr(tr, "executed", False) for tr in tool_results)
        _verdict, _marker = safety.screen_execution_claim(
            full_response, dispatched=_dispatched)
        if _verdict == "clean":
            glass.emit("decision", "claim_screen", detail={
                "verdict": "clean", "marker": None, "dispatched": _dispatched,
                "source": route_result.source})
        else:
            _claim_ctx = glass.bind_context()
            _corrected = await loop.run_in_executor(
                None,
                lambda: _claim_ctx.run(
                    self._router._regenerate_without_claim, messages, _marker))
            if _corrected is not None:
                full_response = _corrected
                _outcome = "violation_regenerated"
            else:
                full_response = safety.honest_action_fallback()
                _outcome = "violation_regen_failed_fallback"
            glass.emit("decision", "claim_screen", detail={
                "verdict": _outcome, "marker": _marker,
                "dispatched": _dispatched, "source": route_result.source})

        # M7 leg 2 — model-authored self-offer screen (web-path parity). A toolless
        # turn whose draft offers to perform an action no code-owned offer backs is
        # the leg-1 root enabler; regenerate off-thread once re-grounded, else the
        # honest no-self-offer fallback. Runs on the (execution-corrected)
        # full_response so ONE delivered answer clears every honesty gate.
        _code_offer = self._router._code_offer_staged()
        _off_verdict, _off_marker = safety.screen_model_text_offer(
            full_response, dispatched=_dispatched, code_offer_staged=_code_offer)
        if _off_verdict == "clean":
            glass.emit("decision", "model_offer_screen", detail={
                "verdict": "clean", "marker": None, "source": route_result.source})
        else:
            _offer_ctx = glass.bind_context()
            _off_corrected = await loop.run_in_executor(
                None,
                lambda: _offer_ctx.run(
                    self._router._regenerate_without_selfoffer, messages))
            if _off_corrected is not None:
                full_response = _off_corrected
                _off_outcome = "violation_regenerated"
            else:
                full_response = safety.honest_no_selfoffer_fallback()
                _off_outcome = "violation_regen_failed_fallback"
            glass.emit("decision", "model_offer_screen", detail={
                "verdict": _off_outcome, "marker": _off_marker,
                "source": route_result.source})

        # M4 capability gate — web-path parity with the router's
        # _screen_and_correct_capability. Screen the delivered draft for a first-party
        # command with an invented subcommand or flag; regenerate once grounded
        # (off-thread), else serve the
        # honest capability fallback. Runs on the (possibly execution-corrected)
        # full_response so ONE delivered answer clears both gates. Ruling 2: a missing
        # ground-truth surface is LOUD ("unavailable" — WARN + honest-under-
        # uncertainty fallback), never a silent green. Glass-logs every turn.
        _cap_verdict, _cap_marker = safety.screen_capability_claim(full_response)
        if _cap_verdict == "clean":
            glass.emit("decision", "capability_screen", detail={
                "verdict": "clean", "marker": None,
                "source": route_result.source})
        elif _cap_verdict == "unavailable":
            if _cap_marker is not None:
                full_response = safety.capability_unverified_fallback(_cap_marker)
                _cap_outcome = "unavailable_no_surface_fallback"
            else:
                _cap_outcome = "unavailable_no_surface"
            glass.emit("decision", "capability_screen", detail={
                "verdict": _cap_outcome, "marker": _cap_marker,
                "source": route_result.source,
                "degraded": "capability-surface.json missing/unreadable"})
        elif _cap_verdict == "unverifiable":
            # Web-path parity with the router: a REAL command whose option
            # surface cannot be introspected is stated honestly, not
            # regenerated — there is nothing proven wrong to correct.
            full_response = safety.capability_unintrospectable_fallback(_cap_marker)
            glass.emit("decision", "capability_screen", detail={
                "verdict": "unverifiable_tool_surface", "marker": _cap_marker,
                "source": route_result.source,
                "degraded": "tool argument surface is not introspectable"})
        else:
            _cap_ctx = glass.bind_context()
            _cap_corrected = await loop.run_in_executor(
                None,
                lambda: _cap_ctx.run(
                    self._router._regenerate_with_capability_grounding,
                    messages, _cap_marker))
            if _cap_corrected is not None:
                full_response = _cap_corrected
                _cap_outcome = "violation_regenerated"
            else:
                full_response = safety.honest_capability_fallback(_cap_marker)
                _cap_outcome = "violation_regen_failed_fallback"
            glass.emit("decision", "capability_screen", detail={
                "verdict": _cap_outcome, "marker": _cap_marker,
                "source": route_result.source})

        # M3(i): a prefixed "yes" over a live offer routed this tail; route() left
        # a one-line reminder on the result to land AFTER the answer.
        if getattr(route_result, "reoffer_reminder", None):
            full_response = (full_response.rstrip() + "\n\n"
                             + route_result.reoffer_reminder)

        # M2a: mirror the DELIVERED answer into the router's model-facing buffer so
        # the next turn's model sees what the user just saw (the streamed-web write
        # gap — idempotent; _append_history glass-logs decision/history_write).
        if full_response.strip():
            self._router._append_history(user_msg, full_response,
                                         state=ctx.conversation)

        # Send stream_end
        await ctx.ws.send_json({
            "type": "stream_end",
            "turn_id": turn_id,
            "full_response": full_response,
            "source": route_result.source,
            "used_llm": True,
            "escalated": route_result.escalated,
            # Per-turn routing confidence (semantic cosine score); set when the
            # turn reached P2 (llm_tools/llm_freeform always do), else None.
            "confidence": self._router.last_route_confidence(),
            # Phone-a-friend OFFER (decision #4): advisory string or null. The client
            # renders an "Ask my frontier model" affordance when present; accepting it
            # sends a frontier_escalate message (consent modal + user_consented=True).
            "escalation_offer": route_result.escalation_offer,
            "stats": {
                "total_ms": round(elapsed_ms, 1),
                "tokens": len(collected_tokens),
                "tool_calls_count": len(tool_calls_made),
            },
        })
        # M1 (d)-closure: the streamed freeform/tool path historically emitted NO
        # route_completed (it early-returns from route(decide_only=True) before
        # _record). Glass records the delivered bytes here so this path — where
        # the session_7074c444 fabrication rode — is fully visible.
        # ⚠ `stream_chunks`, NOT `tokens`. glass._SECRET_KEY_RE matches "token"
        # as a SUBSTRING, so a key named `tokens` — a plain chunk COUNT — was
        # redacted to "<redacted:tokens>" and the count was unreconstructible from
        # the trace. The redactor is correct and stays untouched (weakening a
        # credential rule to rescue a metric is the wrong trade); the key is named
        # so it is not credential-shaped. Do not rename this back. The
        # client-facing `stats.tokens` on stream_end above is a separate field with
        # real consumers and keeps its name.
        glass.emit("delivery", "final", detail={
            "iface": "web", "text": full_response, "source": route_result.source,
            "streamed": True, "stream_chunks": len(collected_tokens),
            "tool_calls": len(tool_calls_made),
            "answer_linkage": _stream_link.as_detail()},
            dur_ms=elapsed_ms)

        # Send tool_executed for any tools that ran. The single line the user
        # sees is the clean structured summary (D-1 `model_summary` contract)
        # when the tool sets one, falling back to the legacy content head; the
        # full payload rides along so the client can offer a "show full output"
        # expander. `content` ALWAYS stays the complete tool output (D-2).
        for tr in tool_results:
            summary = tr.model_summary or tr.content[:256]
            payload = {
                "type": "tool_executed",
                "tool_name": tr.name,
                "success": tr.success,
                "summary": summary,
            }
            # Offer the expander only when the full output adds something
            # beyond the summary line — a structured summary was set, or the
            # content head was truncated. Otherwise the line already shows it.
            full = tr.content or ""
            if tr.model_summary is not None or len(full) > len(summary):
                payload["full_output"] = full
            await ctx.ws.send_json(payload)

        # Identity guard on the persisted transcript: a streamed response can't
        # be un-shown token-by-token (identity QUESTIONS are caught proactively
        # by the self-awareness fast path and never stream), but keep the stored
        # history canonical so a rare mid-stream "I am InterGenOS" slip can't
        # re-prime later turns.
        from intergen.router import correct_identity_collision
        ctx.session_history.append(
            Message(role=MessageRole.ASSISTANT,
                    content=correct_identity_collision(full_response))
        )

    async def _process_llm_stream(
        self,
        ctx: ConnectionContext,
        turn_id: str,
        queue: asyncio.Queue,
        sentinel: object,
        collected_tokens: list[str],
        tool_calls_made: list[ToolCall],
        tool_results: list[ToolResult],
        messages: list[Message],
        tool_schemas: list,
    ) -> str:
        """Process items from the LLM generator queue.

        Handles text tokens (sent as stream_token), ToolCall objects
        (gate check + execution), and error/sentinel signals.
        Returns the complete response text.
        """
        while True:
            item = await queue.get()

            if item is sentinel:
                break

            if isinstance(item, tuple) and item[0] == "__error__":
                logger.error("LLM streaming error: %s", item[1])
                raise item[1]

            if isinstance(item, str):
                # Text token — send to client
                collected_tokens.append(item)
                await ctx.ws.send_json({
                    "type": "stream_token",
                    "turn_id": turn_id,
                    "token": item,
                })

            elif isinstance(item, ToolCall):
                # Tool call detected mid-stream — handle with gate
                tool_calls_made.append(item)

                # ── FORBIDDEN (gating model §3/§5/§6) ──────────────────────
                # A Z3 write/state-change — system-critical files OR InterGen's
                # own substrate — is never performed and never prompted. This is
                # the no-self-modification keystone: even a flawless injection
                # can't make InterGen weaken InterGen. Transparent anti-HAL
                # refusal (states what/why + "you can do it yourself"), then the
                # turn ends. The user may still do it manually; InterGen won't.
                from intergen.zones import forbidden_reason
                refusal = forbidden_reason(item.name, item.arguments)
                if refusal:
                    logger.info(
                        "Gate: FORBIDDEN Z3 action %s — transparent refusal",
                        item.name,
                    )
                    collected_tokens.append(refusal)
                    await ctx.ws.send_json({
                        "type": "stream_token",
                        "turn_id": turn_id,
                        "token": refusal,
                    })
                    break

                # SINGLE GATE: the registry's execute() below is the ONE
                # gate point. Only the two non-overridable governance classes
                # (hash_integrity / owner_only) are pre-checked here — they live
                # in governance.evaluate, NOT the registry's verify_tool_call, so
                # without this pre-check routing all gating through the registry
                # would silently regress them (governance parity). Everything
                # else — the AUTH-PROMPT consent card, the egress-scan review — is
                # rendered by the registry through the review_callback bridge, so
                # there is NO double gate (the prior code gated here AND again in
                # execute() with review_callback=None → registry fail-closed and
                # denied every held action: the incoherent offer/deny loop).
                if self._governance_hard_deny(item):
                    friendly = (
                        "I'm not able to do that from here right now. "
                        "If you'd like, I can look something up for you or walk "
                        "you through how to do it instead."
                    )
                    collected_tokens.append(friendly)
                    await ctx.ws.send_json({
                        "type": "stream_token",
                        "turn_id": turn_id,
                        "token": friendly,
                    })
                    break

                # Execute the tool in a thread so a slow call (e.g. the
                # LLM-backed analyze_file, ~12s on the 2B) does NOT block the
                # event loop, and so a hop-2 "still working" nudge can fire if
                # it crosses the slow-lane threshold while the call finishes.
                # The review_callback bridges the registry's synchronous gate
                # (running here in the worker thread) to the async web consent
                # card on the event loop — fail-closed to deny (single-gate review).
                if self._tools:
                    from intergen.interfaces.provenance import (
                        ConversationTrustState, IngressTracker,
                    )
                    loop = asyncio.get_running_loop()
                    _review_cb = self._make_web_review_callback(
                        ctx, turn_id, loop,
                    )
                    _exec_ctx = glass.bind_context()
                    exec_future = loop.run_in_executor(
                        None,
                        lambda: _exec_ctx.run(
                            self._tools.execute,
                            item,
                            ingress_tracker=ctx.conversation.ingress_tracker,
                            trust_state=ctx.conversation.trust_state,
                            review_callback=_review_cb,
                        ),
                    )
                    try:
                        tr = await asyncio.wait_for(
                            asyncio.shield(exec_future),
                            timeout=_SLOW_TOOL_THRESHOLD_S)
                    except asyncio.TimeoutError:
                        # Crossed the slow lane — reassure (hop-2) and keep the
                        # thinking pill alive, then wait for the real result.
                        if self._filler.available:
                            _args = item.arguments if isinstance(
                                item.arguments, dict) else {}
                            await ctx.ws.send_json({
                                "type": "tool_progress",
                                "turn_id": turn_id,
                                "text": self._filler.hop2(
                                    item.name, _args.get("action"),
                                    _args.get("service")),
                            })
                        tr = await exec_future
                else:
                    tr = ToolResult(
                        call_id=item.call_id,
                        name=item.name,
                        content="Tool registry not initialized.",
                        success=False,
                    )
                tool_results.append(tr)

                # GATE REFUSAL / HONEST HANDOFF: the registry did NOT run
                # the action (not executed) and it did not succeed — the user
                # denied the consent card, or there was no surface to collect
                # consent. NEVER feed the "denied"/"refused" result to the model
                # (a small local model paraphrases it as "blocked by the safety
                # layer" / "insufficient privileges"). Instead deliver a
                # deterministic, plain-language message: the 3-part honest handoff
                # (name the action, why it can't proceed here, the exact command)
                # for a real state-changing action, or the warm friendly refusal
                # otherwise — then end the turn. (executed=False + success=False
                # is the gate-refusal signal; a tool that ran and merely exited
                # non-zero is executed=True and still synthesizes below.)
                if not tr.executed and not tr.success:
                    handoff = self._gate_refusal_message(item, tr)
                    collected_tokens.append(handoff)
                    await ctx.ws.send_json({
                        "type": "stream_token",
                        "turn_id": turn_id,
                        "token": handoff,
                    })
                    break

                # Ask LLM to synthesize the tool result. Feed the model the
                # structured summary when the tool provides one (G3-22 real
                # fix); the user still gets full tr.content via tool_executed.
                # Synthesize on FAILURE too: a non-zero exit is frequently
                # benign and informative (lpstat with no CUPS scheduler, grep
                # with no match, diff finding differences). Gating on success
                # left the web UI showing only a raw red ✗ card with stderr and
                # no answer — the operator's "list the printers errored out".
                # The model explains the outcome from tr.content either way;
                # the ✗ card still honestly reports the exit alongside it.
                if self._llm and tr.content:
                    synthesis = self._llm.continue_after_tool_call(
                        messages, item, tr.model_summary or tr.content,
                        success=tr.success,
                    )
                    if synthesis and synthesis.text:
                        collected_tokens.append(synthesis.text)
                        await ctx.ws.send_json({
                            "type": "stream_token",
                            "turn_id": turn_id,
                            "token": synthesis.text,
                        })
                    elif (getattr(tr, "executed", False) and tr.success
                          and not getattr(tr, "blocked", False)):
                        # M8-2 RESULT DELIVERY INVARIANT: the dispatch SUCCEEDED and
                        # carries content, but synthesis produced no token — the
                        # dispatched-but-discarded seam that shipped an empty answer
                        # (the blank-reply net below is guarded OFF once tool_results
                        # is non-empty). Deliver the tool's own summary/content so its
                        # value is NEVER discarded, and name the defect loudly.
                        _raw = tr.model_summary or tr.content
                        collected_tokens.append(_raw)
                        await ctx.ws.send_json({
                            "type": "stream_token",
                            "turn_id": turn_id,
                            "token": _raw,
                        })
                        glass.emit("delivery", "dispatch_unconsumed", detail={
                            "tool": tr.name, "reason": "synthesis_empty_delivered_raw",
                            "recovered": True})
                        logger.warning(
                            "M8-2: tool %s executed+succeeded but synthesis yielded "
                            "no token; delivered raw content (turn %s)",
                            tr.name, turn_id)

                break  # one tool call per turn (current limitation)

        return "".join(collected_tokens)

    @staticmethod
    def _card_action_description(tool_call: ToolCall) -> tuple[str, str]:
        """(what, command) for the in-web review card (the review card).

        `what` = the action in plain user language, derived from the extracted
        intent — NEVER the raw command string or the raw user sentence. `command`
        = the concrete command shown for transparency. A trust surface must
        describe the real action, so the fallback names the tool + args rather
        than guessing."""
        name = tool_call.name
        args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
        if name == "manage_packages":
            action = str(args.get("action", ""))
            pkg = str(args.get("package") or args.get("name") or "")
            if action in ("update", "upgrade"):
                from intergen.capability_registry import PKM_UPDATE_COMMAND
                return ("Refresh the package index and install available updates.",
                        PKM_UPDATE_COMMAND)
            if action == "install":
                return (f"Install the package '{pkg}'." if pkg else "Install a package.",
                        f"pkm install {pkg}".strip())
            if action in ("remove", "uninstall"):
                return (f"Remove the package '{pkg}'." if pkg else "Remove a package.",
                        f"pkm remove {pkg}".strip())
            return (f"Manage packages ({action}).", f"pkm {action} {pkg}".strip())
        if name == "manage_services":
            action = str(args.get("action", ""))
            svc = str(args.get("service") or args.get("unit") or "")
            verb = action.capitalize() or "Change"
            return (f"{verb} the {svc} service." if svc else f"{verb} a service.",
                    f"systemctl {action} {svc}".strip())
        if name == "run_command":
            return ("Run a system command.", str(args.get("command", "")))
        if name == "write_file":
            path = str(args.get("path", ""))
            return (f"Write to the file {path}." if path else "Write to a file.",
                    f"write {path}".strip())
        return (f"Perform a {name} action.", f"{name} {args}".strip())

    @staticmethod
    def _handoff_command(tool_call: ToolCall) -> str:
        """The concrete command to hand a user for an action they can run
        themselves — ONLY for tools whose action maps to a real command line
        (manage_packages / manage_services / run_command). Empty for tools where
        no such line exists (write_file / take_screenshot / analyze_file / …), so
        the honest handoff omits a bogus command rather than inventing one."""
        if tool_call.name in ("manage_packages", "manage_services", "run_command"):
            _what, command = WebServer._card_action_description(tool_call)
            return command
        return ""

    def _gate_refusal_message(self, tool_call: ToolCall, tr: ToolResult) -> str:
        """User-facing line when the registry did NOT run a held/denied action.

        A BLOCKED action (a destructive command the safety tier hard-refuses)
        NEVER gets a "run it yourself" command — that would hand the user the very
        thing the block exists to prevent (fail-closed, security-first); it gets the plain friendly
        refusal. A normal state-changing action that was denied or had no consent
        surface gets the 3-part honest handoff (user-language classification), so the user is honestly
        advised of the real path forward rather than dead-ended."""
        from intergen.tool_registry import (
            _classify_risk_tier, tier_needs_admin, honest_handoff_message,
        )
        friendly = (
            "I'm not able to do that from here right now. "
            "If you'd like, I can look something up for you or walk "
            "you through how to do it instead."
        )
        if getattr(tr, "blocked", False):
            return friendly
        command = self._handoff_command(tool_call)
        if not command:
            return friendly
        tool_obj = (self._tools.get_tool(tool_call.name)
                    if self._tools else None)
        risk_tier = _classify_risk_tier(
            tool_obj, tool_call.arguments, tool_call.name,
        )
        what, _cmd = self._card_action_description(tool_call)
        return honest_handoff_message(
            what, command, tier_needs_admin(risk_tier),
        )

    async def _evaluate_tool_with_gate(
        self,
        ctx: ConnectionContext,
        turn_id: str,
        tool_call: ToolCall,
        decision: "DispatchDecision | None" = None,
    ) -> str:
        """Evaluate a tool call through governance and provenance gate.

        If the tool requires review, sends a gate_prompt to the client,
        pauses the stream, and awaits a gate_decision via a Future.
        Returns "approved" if the tool can execute, "denied" otherwise.

        `decision` (optional): when the registry drives this via the web
        review_callback bridge, the registry's DispatchDecision carries the full
        reason set (governance + any Sentinel egress-scan flag) so the card can
        render them — the reasons the standalone web gate never sees.
        """
        tool_call_id = getattr(tool_call, 'call_id', None) or uuid.uuid4().hex[:12]

        # Risk tier from the tool registry — read-only via each tool's enumerated
        # AUTO allowlist; privileged/state-changing otherwise.
        from intergen.tool_registry import _classify_risk_tier
        from intergen.interfaces.provenance import ToolRiskTier, INGRESS_TOOLS_V1
        tool_obj = (self._tools.get_tool(tool_call.name)
                    if self._tools else None)
        risk_tier = _classify_risk_tier(
            tool_obj, tool_call.arguments, tool_call.name,
        )

        # ── FREE: read-only actions (gating model §5) ──────────────────────
        # A read-only action changes nothing and needs no escalation, so it is
        # auto-approved with NO gate prompt — reading your own machine's state
        # must never pop a governance card (PRIME DIRECTIVE). The one carve-out
        # is ingress tools (read_file / web_search / …), whose RESULT can carry
        # injection bytes; that is the separate content-trust axis, which still
        # applies, so those keep the inline gate.
        if (risk_tier == ToolRiskTier.READ_ONLY
                and tool_call.name not in INGRESS_TOOLS_V1):
            logger.info(
                "Gate: auto-approving read-only %s (FREE per gating model)",
                tool_call.name,
            )
            return "approved"

        # Governance evaluation (AUTH-PROMPT, gating model §5/§6). After FREE
        # reads and FORBIDDEN Z3 actions are handled above, what's left is a
        # permitted-by-the-OS state-change. The autonomy tier does NOT hard-block
        # it: a tier / owner / cooldown shortfall is the signal to ASK, not to
        # refuse. We route to the consent prompt (Allow once / Allow conversation
        # / Deny) and let the user authorize; the actual privileged execution is
        # then OS-enforced (systemd+polkit / pkexec). The ONE hard denial is a
        # hash_integrity failure — governance itself may be compromised, which is
        # never user-overridable.
        # Two block classes are NOT user-overridable and never become an
        # AUTH-PROMPT: a hash_integrity failure (governance itself may be
        # compromised) and an owner_only action (modify_governance / model /
        # signing key / bootloader / secure-boot — Z3 trust-chain + self
        # substrate; operator-deliberate, never the assistant's to take, even
        # with user consent at this layer). owner_only is also caught earlier by
        # zones.forbidden_reason → transparent refusal; this is the backstop.
        # The two non-overridable block classes — a hash_integrity failure and
        # an owner_only action — live ONLY in governance.evaluate (NOT in the
        # registry's verify_tool_call), so this pre-check is what preserves them
        # when the single registry gate drives the card (governance parity).
        # Extracted to _governance_hard_deny so the streaming + fast paths run the
        # identical pre-check before handing execution to the registry.
        if self._governance_hard_deny(tool_call):
            logger.error(
                "Gate: non-overridable block — refusing %s",
                tool_call.name,
            )
            await ctx.ws.send_json({
                "type": "gate_resolved",
                "tool_call_id": tool_call_id,
                "decision": "deny",
            })
            return "denied"

        # AUTH-PROMPT — render the consent card and await the user's decision.
        # Card rendering is shared with the registry-driven bridge through
        # _render_review_card (the SINGLE card surface), so the standalone web
        # gate and the registry's review_callback show one identical card.
        return await self._render_review_card(
            ctx, turn_id, tool_call, risk_tier, decision,
        )

    def _governance_hard_deny(self, tool_call: ToolCall) -> bool:
        """True when governance HARD-denies this call — hash_integrity or
        owner_only, the two non-overridable classes.

        These live ONLY in governance.evaluate, NOT in the registry's
        verify_tool_call, so this is kept as a PRE-CHECK on every surface
        (standalone web gate + streaming + fast/offer) that runs BEFORE the
        single registry gate. Without it, routing all gating through the
        registry callback would silently regress the hash_integrity / owner_only
        hard-deny (governance parity — confirmed 2026-07-14). Read-only
        non-ingress calls and an absent governance both return False (nothing to
        hard-deny)."""
        if not self._governance:
            return False
        from intergen.tool_registry import _classify_risk_tier
        from intergen.interfaces.provenance import ToolRiskTier, INGRESS_TOOLS_V1
        tool_obj = (self._tools.get_tool(tool_call.name)
                    if self._tools else None)
        risk_tier = _classify_risk_tier(
            tool_obj, tool_call.arguments, tool_call.name,
        )
        if (risk_tier == ToolRiskTier.READ_ONLY
                and tool_call.name not in INGRESS_TOOLS_V1):
            return False
        tool_call_id = getattr(tool_call, 'call_id', None) or uuid.uuid4().hex[:12]
        gov_decision = self._governance.evaluate(
            tool_call_id=tool_call_id,
            tool_name=tool_call.name,
            risk_tier=risk_tier.value,
            tool_category=tool_call.name,
        )
        if gov_decision.allowed:
            return False
        blocked_gates = {c.gate_name for c in gov_decision.checks
                         if not c.passed}
        return bool(blocked_gates & {"hash_integrity", "owner_only"})

    async def _render_review_card(
        self,
        ctx: ConnectionContext,
        turn_id: str,
        tool_call: ToolCall,
        risk_tier: "ToolRiskTier",
        decision: "DispatchDecision | None" = None,
    ) -> str:
        """Render the review card, await the user's decision, return
        "approved" / "denied".

        The SINGLE card surface. Called both by the standalone web gate
        (_evaluate_tool_with_gate) and, in production, by the registry-driven
        review_callback bridge (_make_web_review_callback) — which passes the
        registry's full DispatchDecision so the card can show the complete reason
        set (governance + any Sentinel egress-scan FLAG the standalone web gate
        never sees). Renders UNCONDITIONALLY: the caller has already decided a
        review is required, so there is deliberately NO free-read short-circuit
        here — that would silently bypass an ingress-scan FLAG raised on an
        otherwise read-only tool."""
        tool_call_id = getattr(tool_call, 'call_id', None) or uuid.uuid4().hex[:12]

        # In-web review card (the review card) — mirrors the desktop r66
        # card, translated to user language. `what` = the action in plain terms;
        # `command` = the concrete command for transparency; `classification` =
        # the COMPUTED tier as a user sentence (never the raw "privileged state
        # changing" label); `footer` states the polkit boundary up front on every
        # card. The card is a trust surface: it shows what was actually
        # classified, and the administrator prompt is the real authorization.
        from intergen.tool_registry import (
            classification_sentence, tier_needs_admin,
        )
        card_what, card_command = self._card_action_description(tool_call)
        card = {
            "what": card_what,
            "command": card_command,
            "classification": classification_sentence(risk_tier),
            "footer": (
                "When you approve, InterGenOS will ask for your administrator "
                "password before anything runs."
                if tier_needs_admin(risk_tier)
                else "This runs with your normal permissions — no password needed."
            ),
            "actions": ["approve", "deny"],
        }
        # The registry-driven bridge passes the full DispatchDecision so any
        # Sentinel egress-scan reason (which the standalone web gate never sees)
        # is surfaced on the card, not silently folded away.
        if decision is not None and getattr(decision, "reason", None):
            card["reason"] = decision.reason

        gate_prompt_msg = {
            "type": "gate_prompt",
            "turn_id": turn_id,
            "tool_call_id": tool_call_id,
            "action": json.dumps(tool_call.arguments)[:200],
            "tool_name": tool_call.name,
            "provenance": {
                "classification": (
                    tool_call.source_of_request.value
                    if hasattr(tool_call, 'source_of_request')
                    and tool_call.source_of_request
                    else "user_direct"
                ),
            },
            "governance_check": None,
            # The computed tier, never a fixed label — the consent card is a
            # trust surface and must state what the system actually classified.
            "risk_tier": risk_tier.value,
            "card": card,
            "blocked_by": "provenance_gate",
        }

        # Send gate prompt to client
        await ctx.ws.send_json(gate_prompt_msg)

        # Wait for user decision via Future
        ctx.pending_gate = tool_call_id
        ctx.gate_future = asyncio.get_running_loop().create_future()

        try:
            user_decision = await asyncio.wait_for(
                ctx.gate_future, timeout=300.0  # 5 minute gate timeout
            )
        except asyncio.TimeoutError:
            user_decision = "deny"
            await ctx.ws.send_json({
                "type": "gate_resolved",
                "tool_call_id": tool_call_id,
                "decision": "deny",
                "reason": "Gate prompt timed out after 5 minutes.",
            })
        finally:
            ctx.gate_future = None
            ctx.pending_gate = None

        # Send resolved message
        await ctx.ws.send_json({
            "type": "gate_resolved",
            "tool_call_id": tool_call_id,
            "decision": user_decision,
        })

        return ("approved"
                if user_decision in ("allow", "allow_conversation")
                else "denied")

    def _make_web_review_callback(
        self,
        ctx: ConnectionContext,
        turn_id: str,
        loop: "asyncio.AbstractEventLoop",
    ) -> "Callable[[ToolCall, Any], str]":
        """Build the sync review_callback the registry's execute() invokes when
        it holds a call for review — the gate bridge.

        The registry gate is synchronous and (on the streaming path) runs in a
        WORKER thread; the web card is async and lives on the event loop. This
        bridges the two: it schedules _render_review_card on `loop` and blocks
        the worker thread for the verdict via run_coroutine_threadsafe.

        FAIL-CLOSED on EVERY branch — any exception, or the outer timeout, returns
        "deny", so a bridge failure can NEVER let a held action through (HOLY
        GRAIL). Returns "allow_once" on approval, never "allow_conversation": a
        web card is a per-action consent, and the registry already downgrades
        allow_conversation to allow_once for privileged calls."""
        from intergen.tool_registry import _classify_risk_tier

        def _callback(call: "ToolCall", decision: "Any") -> str:
            try:
                # Parity pre-check (single-gate review): the fast/offer path funnels its held
                # dispatches through THIS bridge instead of the streaming path's
                # :1752 forbidden check + _governance_hard_deny pre-check, so run
                # the same two hard-deny classes here. A forbidden Z3 action or a
                # hash_integrity/owner_only hard-deny NEVER reaches a consent card
                # — it fails closed to "deny". Idempotent for the streaming path
                # (already pre-checked before execute()).
                from intergen.zones import forbidden_reason
                if forbidden_reason(call.name, call.arguments):
                    return "deny"
                if self._governance_hard_deny(call):
                    return "deny"
                tool_obj = (self._tools.get_tool(call.name)
                            if self._tools else None)
                risk_tier = _classify_risk_tier(
                    tool_obj, call.arguments, call.name,
                )
                fut = asyncio.run_coroutine_threadsafe(
                    self._render_review_card(
                        ctx, turn_id, call, risk_tier, decision,
                    ),
                    loop,
                )
                verdict = fut.result(timeout=_GATE_BRIDGE_TIMEOUT_S)
            except Exception as exc:  # noqa: BLE001 — ANY failure fails closed
                logger.error(
                    "web review bridge failed for %s: %s — denying",
                    getattr(call, "name", "?"), type(exc).__name__,
                )
                return "deny"
            return "allow_once" if verdict == "approved" else "deny"

        return _callback

    # -- Server→Client broadcast helpers ------------------------------------

    def _build_system_status(self) -> dict[str, Any]:
        """Build system_status message per protocol §7."""
        status: dict[str, Any] = {
            "type": "system_status",
            "uptime_seconds": round(time.monotonic() - self._startup_time),
            "connections": {
                "total": len(self._connections),
                "web": sum(1 for c in self._connections.values()
                           if c.source_interface == "web"),
                "console": sum(1 for c in self._connections.values()
                               if c.source_interface == "console"),
            },
        }
        # Engine readiness — the daemon + transport can be fully up while no
        # local inference server is running (model not yet downloaded, or it
        # failed to start), in which case a sent message fails with an opaque
        # error. Surface a coherent ready flag so the panel can show an
        # actionable "run intergen setup" banner instead. Derived solely from
        # llama_manager.is_running() (avoids the health.py attr issues).
        llama = getattr(self._health_agg, "_llama", None) if self._health_agg else None
        # Distinguish "no model set up yet" from "model downloaded but the
        # inference server is still starting/loading". The banner wording differs:
        # the first needs `intergen setup`; the second just needs a moment. Marker
        # = a GGUF present in the model dir (written by setup's download step).
        try:
            from intergen.model_manager import MODEL_DIR
            model_present = any(MODEL_DIR.glob("*.gguf"))
        except Exception:
            model_present = False
        status["engine"] = {
            "ready": bool(llama and llama.is_running()),
            "model_present": model_present,
        }
        # Context window + model name for the chat-UI HUD (the CTX stat, which
        # previously hardcoded '--'). 0/"" when no inference server is up.
        status["context_size"] = llama.context_size if llama else 0
        status["model_name"] = llama.model_name if llama else ""
        if self._governance:
            status["governance"] = self._governance.health_snapshot()
        if self._metrics:
            status["metrics"] = self._metrics.get_status()
        return status

    def _build_health_report(self) -> dict[str, Any]:
        """Build health_report message per protocol §8.

        Delegates to HealthAggregator when available (B-007).
        Falls back to minimal server-only report otherwise.
        """
        if self._health_agg:
            report = self._health_agg.collect()
            report["type"] = "health_report"
            report["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            return report

        # Fallback: minimal report when aggregator unavailable
        layers = []
        model_checks = [
            {"name": "Web server", "status": "green",
             "summary": f"Running on {self._host}:{self._port}"},
            {"name": "Connections", "status": "green",
             "summary": f"{len(self._connections)} active"},
        ]
        layers.append({"name": "Web Server", "checks": model_checks})

        if self._governance:
            hs = self._governance.health_snapshot()
            gov_checks = [
                {"name": "Governance hash",
                 "status": "green" if hs.get("hash_verified") else "red",
                 "summary": "Verified" if hs.get("hash_verified")
                            else "UNVERIFIED — possible tampering"},
                {"name": "Autonomy tier",
                 "status": "green",
                 "summary": hs.get("autonomy_tier_name", "Unknown")},
            ]
            layers.append({"name": "Governance", "checks": gov_checks})

        green = sum(1 for layer in layers for c in layer.get("checks", [])
                     if c["status"] == "green")
        yellow = sum(1 for layer in layers for c in layer.get("checks", [])
                      if c["status"] == "yellow")
        red = sum(1 for layer in layers for c in layer.get("checks", [])
                   if c["status"] == "red")

        return {
            "type": "health_report",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "layers": layers,
            "summary": {"green": green, "yellow": yellow, "red": red},
        }

    def _build_governance_report(self) -> dict[str, Any]:
        """Build governance_report message per protocol §9."""
        report: dict[str, Any] = {
            "type": "governance_report",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        if self._governance:
            hs = self._governance.health_snapshot()
            report.update({
                "autonomy_tier": hs.get("autonomy_tier"),
                "autonomy_tier_name": hs.get("autonomy_tier_name"),
                "hash_verified": hs.get("hash_verified"),
                "hash_path": hs.get("hash_path"),
                "active_cooldowns": hs.get("active_cooldowns", 0),
                "commandments": [
                    {"num": c["num"], "title": c["title"],
                     "enforcement": c["enforcement"], "text": c["text"]}
                    for c in self._governance.get_commandments()
                ],
            })
        return report

    # -- Periodic system stats broadcaster ----------------------------------

    async def _broadcast_system_stats(self) -> None:
        """Periodically push system_status to all connected clients."""
        while self._running:
            try:
                await asyncio.sleep(SYSTEM_STATS_INTERVAL)
                if not self._running:
                    break

                status = self._build_system_status()
                dead: list[str] = []

                for client_id, ctx in list(self._connections.items()):
                    try:
                        if not ctx.ws.closed:
                            await ctx.ws.send_json(status)
                        else:
                            dead.append(client_id)
                    except Exception:
                        dead.append(client_id)

                for client_id in dead:
                    self._connections.pop(client_id, None)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in system stats broadcaster")

    # -- Restart mechanism (N-005 lifecycle integration) --------------------

    async def request_restart(self) -> None:
        """Trigger an os.execv restart after a 0.5s delay.

        Used by the GTK4 wrapper's quit path and by the '/restart' slash
        command. The 0.5s delay gives the WebSocket time to send a
        final message to connected clients.
        """
        logger.info("Restart requested — waiting 500ms then execv")
        await asyncio.sleep(0.5)
        os.execv(sys.executable, [sys.executable] + sys.argv)
