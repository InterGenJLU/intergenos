# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""InterGen Console Shell — prompt_toolkit + Rich terminal overlay.

Derived from preceding-project terminal overlay: full-screen takeover
with three zones — HUD bar (top), chat area (middle, scrollable),
input area (bottom, prompt_toolkit buffer with history and completion).

Start with: intergen console
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from intergen.private_state import private_touch

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import (
    BufferControl,
    Dimension,
    FormattedTextControl,
    HSplit,
    Layout,
    ScrollablePane,
    VSplit,
    Window,
    WindowAlign,
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from prompt_toolkit.history import FileHistory
from prompt_toolkit.formatted_text import ANSI, AnyFormattedText
from prompt_toolkit.output import ColorDepth

from rich.console import Console as RichConsole
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from rich.markdown import Markdown
from rich.markup import escape

from intergen.console.client import ConsoleClient, DEFAULT_WS_URL

logger = logging.getLogger(__name__)

HISTORY_FILE = Path.home() / ".local" / "share" / "intergen" / "console_history"

# ── InterGen OS theme colors ──────────────────────────────────────────────
ACCENT = "#0099FF"
ACCENT_BRIGHT = "#33b1ff"
BG_VOID = "#050810"
SURFACE = "#080c18"
TEXT_PRIMARY = "#e2e8f0"
TEXT_DIM = "#7a8ba8"
TEXT_GHOST = "#3d4f6a"

STYLE = Style.from_dict({
    "hud-bar": f"bg:{BG_VOID} fg:{ACCENT} bold",
    "hud-label": f"fg:{TEXT_DIM}",
    "hud-value": f"fg:{TEXT_PRIMARY}",
    "status-green": f"fg:{ACCENT} bold",
    "status-yellow": "fg:#f59e0b bold",
    "status-red": "fg:#ef4444 bold",
    "input-prompt": f"fg:{ACCENT} bold",
    "user-msg": f"fg:{TEXT_DIM} italic",
    "assistant-msg": f"fg:{TEXT_PRIMARY}",
    "system-msg": f"fg:{TEXT_GHOST} italic",
    "error-msg": "fg:#ef4444",
    "gate-border": "fg:#f59e0b",
    "streaming": f"fg:{TEXT_PRIMARY}",
    "stream-cursor": f"fg:{ACCENT} blink",
})


def startup_messages() -> list[dict]:
    """The messages the console seeds its view with before it connects.

    Only one at present: the model attribution the payload license requires.
    The console is its own renderer — it talks to the daemon over its own
    WebSocket and never loads the web conversation view — so a line rendered
    there does not reach a person working at the terminal.

    Returns an empty list when no attribution is owed, so a machine serving a
    non-Qwen model opens on a clean view rather than a blank system line.
    """
    from intergen.attribution import attribution_line
    line = attribution_line()
    if not line:
        return []
    # The same five keys _add_message writes. A seeded message that carried a
    # different shape would render wrong or not at all, because _render_chat
    # reads role, content, source and timestamp off every entry.
    return [{
        "role": "system",
        "content": line,
        "source": "",
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "telemetry": None,
    }]


class ConsoleShell:
    """prompt_toolkit Application wrapping the InterGen WebSocket client.

    Preceding-project layout pattern:
      ┌─ HUD ──────────────────────────────────────┐
      │ ● connected  │  Tier: PROPOSE  │  uptime: 4h│
      ├─────────────────────────────────────────────┤
      │                                             │
      │  Chat messages (Rich-rendered, scrollable)  │
      │                                             │
      ├─────────────────────────────────────────────┤
      │ > _                                         │
      └─────────────────────────────────────────────┘
    """

    def __init__(self) -> None:
        self._client: ConsoleClient | None = None
        # Seeded, not appended after connecting: the attribution belongs on the
        # view from the moment it opens, including on a machine whose daemon
        # never comes up.
        self._messages: list[dict[str, Any]] = startup_messages()
        self._hud_text = "InterGen — connecting..."
        self._streaming_buffer: str = ""
        self._is_streaming = False
        self._session = {"turns": 0, "llm_hits": 0, "tool_hits": 0}
        # Perceived-latency: the thinking indicator speaks the hop-1/hop-2
        # filler (tool_ack / tool_progress) instead of a static "thinking...".
        self._thinking_text = "thinking..."
        self._running = False
        self._system_status: dict[str, Any] = {}
        self._governance_snapshot: dict[str, Any] = {}
        self._gate_pending: dict[str, Any] | None = None
        self._paste_mode = False
        self._paste_lines: list[str] = []
        self._doc_buffer: str = ""

        # Rich console for rendering markdown/panels
        self._rich = RichConsole(
            force_terminal=True, color_system="truecolor",
            width=80,
        )

        # Build layout
        self._input_buffer = Buffer(
            history=FileHistory(str(HISTORY_FILE)),
            completer=WordCompleter([
                "/new", "/clear",
                "/model small", "/model medium", "/model large",
                "/health", "/governance", "/status", "/tier", "/metrics",
                "/audit", "/sessions", "/switch", "/paste", "/file",
                "/clipboard", "/context", "/quit", "/help",
            ]),
        )
        self._hud_control = FormattedTextControl(text=self._render_hud)
        self._chat_control = FormattedTextControl(text=self._render_chat)
        self._chat_pane = ScrollablePane(content=Window(
            content=self._chat_control, wrap_lines=True,
        ))

        self._app = Application(
            layout=self._build_layout(),
            key_bindings=self._build_keybindings(),
            style=STYLE,
            full_screen=True,
            mouse_support=False,
            color_depth=ColorDepth.TRUE_COLOR,
        )
        self._app.before_render += self._before_render

    # -- Layout -------------------------------------------------------------

    def _build_layout(self) -> Layout:
        return Layout(HSplit([
            Window(content=self._hud_control, height=2, align=WindowAlign.LEFT),
            Window(height=1, char="─", style="class:hud-label"),
            self._chat_pane,
            Window(height=1, char="─", style="class:hud-label"),
            Window(
                content=BufferControl(
                    buffer=self._input_buffer,
                    lexer=None,
                ),
                height=2,
            ),
        ]))

    def _build_keybindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("enter")
        def _(event: Any) -> None:
            text = self._input_buffer.text.strip()
            self._input_buffer.text = ""
            if text:
                asyncio.create_task(self._on_user_input(text))

        @kb.add("c-c")
        def _(event: Any) -> None:
            if self._is_streaming:
                self._is_streaming = False
                self._streaming_buffer = ""
                self._add_message("system", "[stream cancelled]", "")
            else:
                event.app.exit()

        @kb.add("c-d")
        def _(event: Any) -> None:
            if not self._input_buffer.text:
                event.app.exit()

        @kb.add("c-l")
        def _(event: Any) -> None:
            self._messages.clear()
            self._app.invalidate()

        @kb.add("escape")
        def _(event: Any) -> None:
            if self._gate_pending:
                asyncio.create_task(self._send_gate_decision("deny"))
                self._gate_pending = None

        return kb

    # -- HUD rendering ------------------------------------------------------

    def _render_hud(self) -> list[tuple[str, str]]:
        parts: list[tuple[str, str]] = []
        # Connected means the console is still READING the connection, not just
        # that the client's flag is set. A fault in the read loop ends the
        # session while that flag stays as it was, and the header used to go on
        # saying connected above a pane that said the session had ended.
        if self._client and self._client.connected and self._running:
            parts.append(("class:status-green", " ● connected "))
        else:
            parts.append(("class:status-red", " ● disconnected "))

        tier = self._governance_snapshot.get("autonomy_tier_name", "?")
        parts.append(("class:hud-label", " │ Tier: "))
        parts.append(("class:hud-value", f"{tier} "))

        uptime = self._system_status.get("uptime_seconds", 0)
        parts.append(("class:hud-label", "│ uptime: "))
        parts.append(("class:hud-value", f"{self._fmt_uptime(uptime)} "))

        connections = self._system_status.get("connections", {})
        total = connections.get("total", 0) if isinstance(connections, dict) else 0
        parts.append(("class:hud-label", "│ connections: "))
        parts.append(("class:hud-value", f"{total} "))

        if self._is_streaming:
            parts.append(("class:stream-cursor", f" █ {self._thinking_text}"))

        return parts

    # -- Chat rendering -----------------------------------------------------

    def _render_chat(self) -> AnyFormattedText:
        r = self._rich
        if not self._messages and not self._streaming_buffer:
            return [("class:system-msg", "No messages yet. Type a question or /help.\n")]
        # Render the chat with Rich (truecolor ANSI), then bridge to
        # prompt_toolkit via ANSI(), which parses the escape codes — including
        # 24-bit truecolor — back into native formatted text, so colour is
        # preserved end to end. Text.from_ansi() parses any ANSI already
        # embedded in message content (e.g. the /health, /governance, /metrics
        # report bodies) instead of rendering the escape codes literally.
        with r.capture() as cap:
            for msg in self._messages[-50:]:
                role = msg.get("role", "system")
                content = msg.get("content", "")
                source = msg.get("source", "")
                ts = msg.get("timestamp", "")

                if role == "user":
                    r.print(Panel(
                        Text.from_ansi(content, style="italic dim"),
                        title=f"You  {ts}",
                        border_style=TEXT_GHOST,
                        padding=(0, 1),
                    ))
                elif role == "assistant":
                    title = "InterGen"
                    if source:
                        title += f"  [{source}]"
                    # The reply's own declaration, when it made one: a turn it
                    # says it did not answer is not titled as an answer.
                    unanswered = msg.get("handled") is False
                    if unanswered:
                        title += "  — not an answer to this turn"
                    # Route by the message's KNOWN source, never by sniffing the
                    # model's own bytes (a model that emits an ESC could route
                    # ITSELF to the more-permissive from_ansi path and spoof a
                    # system/governance line — turn the assumption into a gate).
                    # The report bodies (/health, /governance, /metrics) are
                    # Rich-captured ANSI → from_ansi; every LLM reply renders as
                    # markdown, with any stray ESC stripped so model output can
                    # never reach the ANSI path.
                    if source in ("health", "governance", "metrics"):
                        body = Text.from_ansi(content)
                    else:
                        body = Markdown(content.replace("\x1b", ""))
                    r.print(Panel(
                        body,
                        title=f"{title}  {ts}",
                        border_style="#f59e0b" if unanswered else ACCENT,
                        padding=(0, 1),
                    ))
                    tele = msg.get("telemetry")
                    if tele:
                        r.print(self._render_stats(tele))
                else:
                    r.print(Text.from_ansi(f"  {content}", style="italic dim"))

            if self._streaming_buffer:
                r.print(Panel(
                    Text.from_ansi(self._streaming_buffer) + Text("█", style=f"bold {ACCENT} blink"),
                    title="InterGen",
                    border_style=ACCENT_BRIGHT,
                    padding=(0, 1),
                ))
        return ANSI(cap.get())

    def _before_render(self, app: Application) -> None:
        self._hud_control.text = self._render_hud
        self._chat_control.text = self._render_chat

    # -- Message helpers ----------------------------------------------------

    def _add_message(self, role: str, content: str, source: str = "",
                     telemetry: dict | None = None,
                     handled: bool | None = None) -> None:
        entry = {
            "role": role,
            "content": content,
            "source": source,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "telemetry": telemetry,
        }
        # Only a turn whose reply carried the declaration gets the key, so an
        # entry without it is "nothing was declared" rather than "declared
        # answered" — the distinction the command line's own reader keeps.
        if handled is not None:
            entry["handled"] = handled
        self._messages.append(entry)
        self._app.invalidate()

    def _build_telemetry(self, msg: dict) -> dict:
        """Extract the per-turn telemetry InterGen already sends over the wire
        (route/source, used_llm, and the stats block) and roll it into the
        running session totals. Rendered per-turn by _render_stats — the same
        data the web panel's stats row uses, surfaced in the console."""
        stats = msg.get("stats") or {}
        used_llm = bool(msg.get("used_llm", False))
        tools = stats.get("tool_calls_count", 0)
        # The reply's own declaration that it did not answer the turn. The web
        # delivery stamps it on every non-streamed response frame; the command
        # line reads it (cli._deliver_answer) and exits non-zero on False. The
        # console is its own renderer over the same wire, so it keeps the
        # declaration too — dropping it here is what let an unanswered turn be
        # filed as an answer and counted in the rollup.
        handled = msg.get("handled", True)
        if handled is not False:
            self._session["turns"] += 1
        if used_llm:
            self._session["llm_hits"] += 1
        if tools:
            self._session["tool_hits"] += 1
        return {
            "source": msg.get("source", ""),
            "used_llm": used_llm,
            "handled": handled,
            "total_ms": stats.get("total_ms"),
            "tokens": stats.get("tokens"),
            "tools": tools,
            # Per-turn routing confidence (semantic cosine score) or None for a
            # deterministic route that resolved before semantic matching.
            "confidence": msg.get("confidence"),
        }

    def _render_stats(self, tele: dict) -> Panel:
        """Per-turn telemetry panel — route, LLM, timing, tokens, tools, tier,
        and the session rollup — modeled on the preceding-project console's
        Stats panel. Adaptive columns: 3 pairs/row when wide, else 2, else 1."""
        pairs: list[tuple[str, str]] = [
            # escape the daemon string cells so a Rich Table cell can never
            # interpret bracket markup, even if the route enum ever grows a
            # derived label (defense-in-depth; today's enum is fixed-safe).
            ("Route", escape(tele.get("source") or "-")),
            ("LLM", "yes" if tele.get("used_llm") else "no"),
        ]
        # Confidence is a numeric cosine score (0..1) or None — no escape needed
        # (it can never carry markup). "n/a" for a deterministic pre-semantic route.
        conf = tele.get("confidence")
        pairs.append(("Confidence",
                      f"{conf:.2f}" if isinstance(conf, (int, float)) else "n/a"))
        ms = tele.get("total_ms")
        pairs.append(("Time", f"{ms:.0f}ms" if ms is not None else "-"))
        tok = tele.get("tokens")
        pairs.append(("Tokens", str(tok) if tok is not None else "-"))
        pairs.append(("Tools", str(tele.get("tools", 0))))
        pairs.append(("Tier", escape(self._governance_snapshot.get("autonomy_tier_name", "?"))))
        s = self._session
        pairs.append(("Session", f"{s['turns']} turns | {s['llm_hits']} LLM | {s['tool_hits']} tool"))

        width = self._rich.width
        cols = 3 if width >= 120 else 2 if width >= 80 else 1
        table = Table(show_header=False, box=None, padding=(0, 0), expand=True)
        for c in range(cols):
            table.add_column(style=f"dim {ACCENT}", no_wrap=True, ratio=1)
            table.add_column(style=TEXT_PRIMARY, no_wrap=True, ratio=3)
            if c < cols - 1:
                table.add_column(width=3, style="dim")
        for i in range(0, len(pairs), cols):
            row: list[str] = []
            for j in range(cols):
                if i + j < len(pairs):
                    row.extend(pairs[i + j])
                else:
                    row.extend(("", ""))
                if j < cols - 1:
                    row.append("│" if i + j < len(pairs) else "")
            table.add_row(*row)
        return Panel(table, title="[dim]Stats[/dim]", border_style="dim", padding=(0, 1))

    @staticmethod
    def _fmt_uptime(seconds: float) -> str:
        seconds = int(seconds)
        h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
        if h > 0:
            return f"{h}h{m:02d}m"
        if m > 0:
            return f"{m}m{s:02d}s"
        return f"{s}s"

    # -- Input handling -----------------------------------------------------

    # The keys the consent card tells the person to press, and the decision each
    # one sends. A consent surface is a trust surface: every key its card names
    # has to be a key the console acts on, so the card is rendered FROM this map
    # and the input path decides the gate FROM this map — one table, so the two
    # cannot drift apart again.
    _GATE_KEYS: "dict[str, tuple[str, str]]" = {
        "a": ("allow", "allow"),
        "y": ("allow_conversation", "allow conversation"),
        "n": ("deny", "deny"),
    }

    async def _on_user_input(self, text: str) -> None:
        # A card is on screen and the person typed exactly one of the keys it
        # names: that is the decision, not a chat message. Only while a card is
        # pending, and only for those exact keys, so nothing else a person types
        # changes meaning.
        if self._gate_pending:
            key = text.strip().lower()
            if key in self._GATE_KEYS:
                decision, spoken = self._GATE_KEYS[key]
                await self._send_gate_decision(decision)
                self._gate_pending = None
                self._add_message("system", f"Gate decision sent: {spoken}", "gate")
                return

        if text.startswith("/"):
            await self._handle_slash_command(text)
            return

        if self._paste_mode:
            if not text.strip():
                # Empty line finalizes paste mode
                self._doc_buffer = "\n".join(self._paste_lines)
                tokens = max(1, len(self._doc_buffer) // 4)
                self._paste_mode = False
                self._paste_lines = []
                self._add_message("system",
                    f"Buffer loaded ({len(self._doc_buffer)} chars, ~{tokens} tokens). Use /context to see contents.",
                    "")
            else:
                self._paste_lines.append(text)
            return

        if not self._client or not self._client.connected:
            self._add_message("system", "Not connected to InterGen. Is the daemon running?")
            return

        self._add_message("user", text)
        # Include document buffer context if present
        msg = text
        if self._doc_buffer:
            msg = f"[Document buffer ({len(self._doc_buffer)} chars)]\n{self._doc_buffer}\n\n[User message]\n{text}"
        await self._client.send_message(msg)

    async def _handle_slash_command(self, text: str) -> None:
        cmd = text.lower().split()[0]
        args = text[len(cmd):].strip()

        if cmd == "/quit":
            self._app.exit()
        elif cmd == "/help":
            self._show_help()
        elif cmd == "/clear":
            await self._request_clear()
        elif cmd == "/new":
            if self._client:
                await self._client.send({"type": "new_session", "session_id": ""})
                self._messages.clear()
                # The document buffer is conversation content: it is prepended to
                # every outbound message, so leaving it loaded carried the old
                # conversation's document — possibly private — into the first and
                # every message of the new one, silently. /new exists to END a
                # conversation, and the server's own handler ends everything it
                # owns; this is the piece the console owns.
                if self._doc_buffer:
                    chars = len(self._doc_buffer)
                    self._doc_buffer = ""
                    self._paste_lines = []
                    self._paste_mode = False
                    self._add_message(
                        "system",
                        f"New conversation. The document buffer ({chars} chars) "
                        "was cleared with it.", "")
        elif cmd == "/health":
            if self._client:
                await self._client.send({"type": "request_health"})
        elif cmd == "/governance":
            if self._client:
                await self._client.send({"type": "request_governance"})
        elif cmd == "/metrics":
            if self._client:
                await self._client.send({"type": "request_metrics"})
        elif cmd == "/status":
            if self._client:
                await self._client.send({"type": "slash_command", "command": "/status"})
        elif cmd == "/tier":
            if self._client:
                await self._client.send({"type": "slash_command", "command": "/tier"})
        elif cmd == "/model":
            # An argument outside the three tiers used to be discarded and
            # replaced with "medium": the console sent a switch nobody asked for
            # and reported a request the person never made, and the daemon's own
            # guard sits one hop downstream of the coercion so it could never see
            # the value to refuse it. An unrecognised argument is refused here,
            # naming what was not understood; a bare command is a question.
            tiers = ("small", "medium", "large")
            if not args:
                # The console does not hold the daemon's tier, so it does not
                # claim one: it says how to set one and changes nothing.
                self._add_message(
                    "system",
                    "/model needs a tier to change anything. Nothing was "
                    f"changed. The tiers are: {', '.join(tiers)}.",
                    "")
            elif args in tiers:
                if self._client:
                    await self._client.send({"type": "switch_model", "tier": args})
                self._add_message("system", f"Model tier requested: {args}", "")
            else:
                self._add_message(
                    "system",
                    f"Not a model tier: {args!r}. Nothing was changed. "
                    f"The tiers are: {', '.join(tiers)}.",
                    "error")
        elif cmd == "/paste":
            self._paste_mode = True
            self._paste_lines = []
            self._add_message("system",
                "Paste mode: type or paste your text. Enter a blank line or use /done to finish.",
                "")
        elif cmd == "/done":
            if self._paste_mode:
                self._doc_buffer = "\n".join(self._paste_lines)
                tokens = max(1, len(self._doc_buffer) // 4)
                self._paste_mode = False
                self._paste_lines = []
                self._add_message("system",
                    f"Buffer loaded ({len(self._doc_buffer)} chars, ~{tokens} tokens). Use /context to see contents.",
                    "")
            else:
                self._add_message("system", "No paste in progress. Use /paste to start.", "")
        elif cmd == "/context":
            if self._doc_buffer:
                preview = self._doc_buffer[:500] + ("..." if len(self._doc_buffer) > 500 else "")
                tokens = max(1, len(self._doc_buffer) // 4)
                self._add_message("system",
                    f"Document buffer ({len(self._doc_buffer)} chars, ~{tokens} tokens):\n{preview}",
                    "")
            else:
                self._add_message("system", "Document buffer is empty.", "")
        elif cmd == "/clipboard":
            try:
                import subprocess
                result = subprocess.run(["wl-paste", "-n"], capture_output=True, text=True, timeout=3)
                if result.returncode == 0 and result.stdout.strip():
                    self._doc_buffer = result.stdout.strip()
                    tokens = max(1, len(self._doc_buffer) // 4)
                    self._add_message("system",
                        f"Clipboard loaded ({len(self._doc_buffer)} chars, ~{tokens} tokens).",
                        "")
                else:
                    self._add_message("system", "Clipboard is empty or not accessible.", "")
            except Exception:
                self._add_message("system", "Clipboard access failed. Install wl-clipboard.", "")
        else:
            self._add_message("system", f"Unknown command: {cmd}. Type /help for commands.", "")

    async def _request_clear(self) -> None:
        """Ask the server to clear the conversation's buffer; the pane follows.

        The model's buffer and the offers staged in it live on the server.
        Emptying only this pane left them there, so a bare "yes" typed next
        took up an offer the person could no longer see. The pane is emptied
        on the server's buffer_cleared frame, so it never shows a clear the
        server did not make.
        """
        if not self._client or not self._client.connected:
            self._add_message(
                "system", "Not connected to InterGen: nothing was cleared.",
                "error")
            return
        try:
            await self._client.send_slash_command("/clear")
        except OSError as exc:
            self._add_message(
                "system",
                f"Could not reach InterGen: nothing was cleared ({exc}).",
                "error")

    def _show_help(self) -> None:
        help_text = (
            "InterGen Console — Slash Commands\n"
            "  /new           Start a new conversation\n"
            "  /clear         Clear the chat and the model's buffer of it\n"
            "  /model         Switch model tier (small/medium/large)\n"
            "  /health        Show system health report\n"
            "  /governance    Show governance dashboard\n"
            "  /metrics       Show performance metrics\n"
            "  /status        Show daemon status\n"
            "  /tier          Show current autonomy tier\n"
            "  /quit          Exit console\n"
            "  /help          Show this help\n"
            "\n"
            "Keyboard Shortcuts\n"
            "  Enter          Send message\n"
            "  Ctrl+C         Cancel stream / exit (if idle)\n"
            "  Ctrl+D         Exit console\n"
            "  Ctrl+L         Clear chat display\n"
            "  Esc            Dismiss gate prompt (deny)"
        )
        self._add_message("system", help_text, "help")

    async def _send_gate_decision(self, decision: str) -> None:
        if self._client and self._gate_pending:
            tool_call_id = self._gate_pending.get("tool_call_id", "")
            await self._client.send_gate_decision(tool_call_id, decision)

    # -- WebSocket message processing ---------------------------------------

    async def _process_server_message(self, msg: dict[str, Any]) -> None:
        msg_type = msg.get("type", "")

        if msg_type == "connected":
            self._system_status = msg.get("system_status", {})
            self._governance_snapshot = msg.get("governance", {})
            self._hud_text = "connected"
            self._app.invalidate()

        elif msg_type == "system_status":
            self._system_status = msg
            self._governance_snapshot = msg.get("governance", {})

        elif msg_type == "stream_start":
            self._is_streaming = True
            self._streaming_buffer = ""
            self._thinking_text = "thinking..."
            self._app.invalidate()

        elif msg_type in ("tool_ack", "tool_progress"):
            # The thinking indicator speaks the filler (perceived-latency).
            text = (msg.get("text") or "").strip()
            if text:
                self._is_streaming = True  # ensure the indicator is visible
                self._thinking_text = text
                self._app.invalidate()

        elif msg_type == "stream_token":
            token = msg.get("token", "")
            self._streaming_buffer += token
            # A real token ends the filler stage — back to the neutral label.
            self._thinking_text = "thinking..."
            self._app.invalidate()

        elif msg_type == "stream_end":
            self._is_streaming = False
            # dict.get substitutes its default only for an ABSENT key, so a
            # terminal frame carrying full_response as an explicit empty string
            # — the shape of a turn that produced no text — yielded an empty
            # answer, which was appended as the assistant's reply and counted in
            # the rollup, and then the tokens the person had watched arrive were
            # discarded on the line after. An absent key is the fallback the
            # default was written for; an explicit empty string is the server
            # saying the turn produced nothing, which is not the same thing.
            declared = msg.get("full_response")
            produced_nothing = "full_response" in msg and not (declared or "").strip()
            full = (declared or "") if (declared or "").strip() else self._streaming_buffer
            if produced_nothing:
                self._add_message(
                    "system",
                    "InterGen ended the turn without producing an answer.",
                    "error")
                # Whatever the person watched arrive is still shown, carrying the
                # declaration, so it is not read as the answer and not counted as
                # a turn the assistant answered.
                if full.strip():
                    self._add_message("assistant", full, msg.get("source", ""),
                                      self._build_telemetry({**msg, "handled": False}),
                                      handled=False)
            else:
                self._add_message("assistant", full, msg.get("source", ""),
                                  self._build_telemetry(msg))
            self._streaming_buffer = ""

        elif msg_type == "gate_prompt":
            action = msg.get("action", "")
            tool = msg.get("tool_name", "?")
            blocked = msg.get("blocked_by", "")
            governance = msg.get("governance_check")
            self._gate_pending = msg
            self._is_streaming = False

            # Rendered from the same table the input path decides from, so the
            # card can only ever name keys the console acts on.
            offered = ", ".join(
                f"'{key}' to {spoken}" for key, (_d, spoken) in self._GATE_KEYS.items())
            gate_text = (
                f"⚠️  Gate: {tool} wants to: {action}\n"
                f"    Blocked by: {blocked}\n"
                f"    Press {offered}, then Enter"
            )
            if governance:
                checks = governance.get("checks", [])
                gate_text += "\n    Governance:"
                for c in checks:
                    mark = "✓" if c.get("passed") else "✗"
                    gate_text += f"\n      {mark} {c.get('gate_name')}: {c.get('reason', '')}"

            self._add_message("system", gate_text, "gate")

        elif msg_type == "gate_resolved":
            self._gate_pending = None
            decision = msg.get("decision", "?")
            self._add_message("system", f"Gate decision: {decision}", "gate")

        elif msg_type == "buffer_cleared":
            # The server emptied the conversation's buffer and withdrew the
            # offers staged in it: the pane follows the server, never leads it.
            self._messages.clear()
            self._add_message("system",
                              "Chat cleared, and the model's buffer with it.", "")

        elif msg_type == "tool_executed":
            tool = msg.get("tool_name", "?")
            success = msg.get("success", False)
            summary = msg.get("summary", "")
            status = "✓" if success else "✗"
            self._add_message("system",
                              f"Tool {status} {tool}: {summary[:200]}", "tool")

        elif msg_type == "health_report":
            layers = msg.get("layers", [])
            text = self._format_health_report(layers)
            self._add_message("assistant", text, "health")

        elif msg_type == "governance_report":
            text = self._format_governance_report(msg)
            self._add_message("assistant", text, "governance")

        elif msg_type == "metrics_report":
            text = self._format_metrics_report(msg)
            self._add_message("assistant", text, "metrics")

        elif msg_type == "error":
            code = msg.get("code", "")
            message = msg.get("message", "")
            # An error frame IS the end of the turn: the no-wedge backstop sends
            # it instead of a stream_end when the worker raises mid-stream, so no
            # stream_end ever arrives for that turn. Leaving the stream state set
            # kept the thinking indicator running and kept drawing the partial
            # text with a live cursor, so a failed turn went on looking like an
            # answer still arriving.
            self._is_streaming = False
            self._streaming_buffer = ""
            self._thinking_text = "thinking..."
            self._add_message("system", f"Error [{code}]: {message}", "error")

        elif msg_type == "response":
            content = msg.get("content", "")
            source = msg.get("source", "")
            tele = self._build_telemetry(msg)
            # A terse fast-path summary may carry the full raw output. The
            # console can't collapse it, so show the summary + a hint; the user
            # gets the raw with a "raw …" re-ask (handled server-side).
            if msg.get("full_output"):
                content = f"{content}\n  (full output available — ask for the raw output)"
            # A turn the reply itself declares it did not handle is still shown
            # — the person should read what came back — but the entry keeps the
            # declaration, so neither the stored record nor the view presents it
            # as an answer.
            self._add_message("assistant", content, source, tele,
                              handled=tele.get("handled"))

        elif msg_type == "disconnected":
            # The client distinguishes a read that failed from a clean close and
            # says which on the frame; a console that printed one sentence for
            # both threw that distinction away at the last step.
            reason = msg.get("reason") or msg.get("error") or ""
            if reason:
                self._add_message(
                    "system",
                    f"Disconnected from InterGen server: {reason}", "error")
            else:
                self._add_message("system",
                                  "Disconnected from InterGen server.", "")

    # -- Formatters for Rich views ------------------------------------------

    def _format_health_report(self, layers: list[dict]) -> str:
        r = self._rich
        with r.capture() as cap:
            r.print(Panel("SYSTEM HEALTH", border_style=ACCENT, style="bold"))
            for layer in layers:
                r.print(Text(f"\n{layer.get('name', '')}", style=f"bold {ACCENT}"))
                for check in layer.get("checks", []):
                    status = check.get("status", "?")
                    name = check.get("name", "")
                    summary = check.get("summary", "")
                    dot = {"green": "●", "yellow": "●", "red": "●"}.get(status, "○")
                    style = {
                        "green": ACCENT,
                        "yellow": "#f59e0b",
                        "red": "#ef4444",
                    }.get(status, TEXT_GHOST)
                    r.print(Text(f"  {dot} {name}: {summary}", style=style))
        return cap.get()

    def _format_governance_report(self, msg: dict) -> str:
        r = self._rich
        tier_name = msg.get("autonomy_tier_name", "?")
        hash_ok = msg.get("hash_verified", False)
        active_cooldowns = msg.get("active_cooldowns", 0)
        commandments = msg.get("commandments", [])

        hash_status = "✓ VERIFIED" if hash_ok else "✗ UNVERIFIED — TAMPER DETECTED"
        hash_style = ACCENT if hash_ok else "#ef4444"

        with r.capture() as cap:
            r.print(Panel("GOVERNANCE DASHBOARD", border_style=ACCENT, style="bold"))
            r.print(Text(f"Tier: {tier_name}", style=f"bold {ACCENT}"))
            r.print(Text(f"Hash: {hash_status}", style=hash_style))
            r.print(Text(f"Active cooldowns: {active_cooldowns}"))
            r.print(Text("\nTHE TEN COMMANDMENTS:", style=f"bold {ACCENT}"))
            for c in commandments:
                enf = c.get("enforcement", "prompt_anchored")
                enf_mark = "[code]" if enf == "code_enforced" else "[prompt]"
                r.print(Text(
                    f"  {c.get('num', '?')}. {c.get('title', '')}  {enf_mark}",
                    style=TEXT_PRIMARY,
                ))
        return cap.get()

    def _format_metrics_report(self, msg: dict) -> str:
        r = self._rich
        with r.capture() as cap:
            r.print(Panel("PERFORMANCE METRICS", border_style=ACCENT, style="bold"))

            req = msg.get("requests", 0)
            llm_calls = msg.get("llm_calls", 0)
            escalations = msg.get("escalations", 0)

            r.print(Text(
                f"Requests: {req}   LLM calls: {llm_calls}   Escalations: {escalations}",
                style=TEXT_PRIMARY,
            ))

            # Route breakdown
            for key, label in [
                ("route_keyword", "keyword"), ("route_cache", "cache"),
                ("route_semantic", "semantic"), ("route_llm_tools", "llm_tools"),
                ("route_llm_freeform", "llm_freeform"),
            ]:
                count = msg.get(key, 0)
                r.print(Text(f"  {label}: {count}", style=TEXT_DIM))
        return cap.get()

    # -- Main run loop ------------------------------------------------------

    async def run(self) -> None:
        """Connect to the InterGen web server and start the REPL."""
        # The history file holds everything the user has typed at the
        # console. FileHistory opens it with a plain append, which would
        # create it 0644, so it is pre-created owner-only here — an ordinary
        # open never changes the mode of a file that already exists.
        private_touch(HISTORY_FILE)

        self._client = ConsoleClient(source_interface="console")

        # Start WebSocket reader in background
        connect_task = asyncio.create_task(self._connect_and_read())

        # Run prompt_toolkit Application
        try:
            await self._app.run_async()
        finally:
            self._running = False
            if self._client:
                await self._client.close()
            connect_task.cancel()
            try:
                await connect_task
            except asyncio.CancelledError:
                pass

    async def _connect_and_read(self) -> None:
        """Connect WebSocket and process incoming messages."""
        try:
            connected_msg = await self._client.connect()
            # Process the connected message
            await self._process_server_message(connected_msg)
        except ConnectionRefusedError as exc:
            # The socket itself said no: that IS the question about the daemon,
            # and the refusal's own text does not say so. Both, so neither the
            # diagnosis nor the cause is lost.
            self._add_message(
                "system",
                "Failed to connect to InterGen. Is the daemon running? "
                f"({exc})")
            self._app.invalidate()
            return
        except ConnectionError as exc:
            # The client raises this with the cause AND the remedy already in
            # it — an absent, empty or unreadable web token names itself and
            # names 'intergen setup'. Replacing that with a question about the
            # daemon sent the person to look at a service that is running, so
            # the sentence the client wrote is the sentence they read.
            detail = str(exc).strip()
            self._add_message(
                "system",
                f"Failed to connect to InterGen: {detail}" if detail
                else "Failed to connect to InterGen. Is the daemon running?")
            self._app.invalidate()
            return
        except Exception as exc:
            self._add_message(
                "system",
                "Failed to connect to InterGen. Is the daemon running? "
                f"({type(exc).__name__}: {exc})")
            self._app.invalidate()
            return

        self._running = True
        fault: BaseException | None = None
        try:
            async for msg in self._client.messages():
                await self._process_server_message(msg)
                if msg.get("type") == "disconnected":
                    break
        except Exception as exc:
            # A fault here ended the session. The traceback goes to the log as
            # before, but the log is not the surface the person is looking at:
            # reporting only "Disconnected." told them the session ended and
            # nothing about a failure having ended it, which reads as a clean
            # close. The fault is named in the view as well.
            fault = exc
            logger.exception("Message processing error")
        finally:
            self._running = False
            if fault is not None:
                self._add_message(
                    "system",
                    "Error: the console could not process a message from "
                    f"InterGen and stopped reading ({type(fault).__name__}: "
                    f"{fault}).", "error")
            self._add_message("system", "Disconnected.", "")
            # The HUD reads the client's own flag, and nothing on this path
            # cleared it, so the header kept saying connected above a pane
            # saying the session had ended. The two now agree.
            self._hud_text = "disconnected"
            self._app.invalidate()


def main() -> None:
    """Entry point for 'intergen console'."""
    logging.basicConfig(level=logging.WARNING)

    shell = ConsoleShell()
    try:
        asyncio.run(shell.run())
    except KeyboardInterrupt:
        pass
    finally:
        # Restore terminal
        sys.stdout.write("\033[?25h")  # show cursor
        sys.stdout.flush()


if __name__ == "__main__":
    main()
