# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Request-scoped decision tracer for InterGen.

The harness's foundation: a lightweight span tracer that records *why* the
router/LLM/tool stack made each decision on a request, so the test harness can
assert against the real decision path instead of guessing from the final text.

Design constraints (see the decision-trace harness plan):

* **OFF by default.** Enable with ``INTERGEN_TRACE=1``. When disabled, every
  ``span()`` is a no-op that allocates nothing and writes nothing — the runtime
  pays effectively zero cost in production.
* **Content capture is a SEPARATE opt-in.** Routing/scoring metadata is always
  recorded when tracing is on; prompts, tool arguments, and model outputs are
  only captured with ``INTERGEN_TRACE_CONTENT=1`` (use ``set_content()``).
  Decision metadata is cheap and safe; raw content is neither.
* **No third-party dependency.** Field names follow OpenTelemetry /
  OpenInference span conventions (``trace_id``, ``span_id``,
  ``parent_span_id``, ``name``, ``kind``, ``start_ms``, ``duration_ms``,
  ``status``, ``attributes``) so the records are familiar and convertible — but
  nothing from an OTel SDK is imported or shipped. The harness consumes plain
  JSON lines.
* **asyncio-safe.** Span nesting is tracked with ``contextvars`` so concurrent
  requests on the aiohttp event loop never cross-contaminate. The one place
  ContextVars do NOT auto-propagate is a worker thread (``web_server`` runs the
  LLM off the loop in a ``ThreadPoolExecutor`` with no ``copy_context``); use
  :func:`bind_context` when handing work to a thread so spans stay attached.

The JSONL sink reuses the exact writable-path resolution from
``metrics.EventLogger._setup_log_dir`` (a ``--user`` service under
``ProtectSystem=strict`` cannot write ``/var/log/intergen``), writing to
``decisions.jsonl`` in the per-user state dir.
"""

from __future__ import annotations

import contextlib
import contextvars
import itertools
import json
import logging
import os
import re
import secrets
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any, Iterator

logger = logging.getLogger(__name__)

_LOG_DIR = "/var/log/intergen"
_LOG_FILE = "decisions.jsonl"

# Span "kind" vocabulary. InterGen-meaningful values; the parenthetical is the
# nearest OpenInference span kind, for anyone converting these records later.
#   "request" (CHAIN)  — the root span for one user turn
#   "router"  (CHAIN)  — a routing stage (classify, semantic, eligibility, …)
#   "gate"    (GUARDRAIL) — eligibility / provenance / safety gate
#   "llm"     (LLM)    — a model call (tool-decision or synthesis)
#   "tool"    (TOOL)   — a tool invocation
#   "internal"(CHAIN)  — anything else
_KINDS = ("request", "router", "gate", "llm", "tool", "internal")

# Record schema version — lets the grader/replay evolve the format unambiguously.
_SCHEMA_VERSION = 1

# Process-wide monotonic span counter. Sibling spans born in the same millisecond
# tie on start_ms; this gives the grader a deterministic "which decision came
# first" / replay order independent of clock resolution. (next() is atomic.)
_seq = itertools.count()

# Keys whose VALUE must never be written even under content capture — credentials
# are never persisted to a trace (security-alignment rule: secrets stay out of
# logs). Matched case-insensitively as a substring of the attribute key; the
# value is replaced with _REDACTED at set_content().
_SECRET_KEY_RE = re.compile(
    r"pass(word|wd|phrase)|secret|token|api[_-]?key|authorization|"
    r"credential|private[_-]?key|keyring|bearer",
    re.IGNORECASE,
)
_REDACTED = "[REDACTED]"

# Active span + trace id for the current async/thread context. A ContextVar is
# per-Context, so nested `with` blocks form a parent/child chain and concurrent
# requests on the event loop stay isolated.
_current_span: ContextVar["Span | None"] = ContextVar("intergen_trace_span", default=None)
_current_trace_id: ContextVar[str | None] = ContextVar("intergen_trace_id", default=None)


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _now_ms() -> float:
    return time.time() * 1000.0


def _new_trace_id() -> str:
    return secrets.token_hex(16)  # 32 hex chars, OTel trace-id width


def _new_span_id() -> str:
    return secrets.token_hex(8)   # 16 hex chars, OTel span-id width


@dataclass
class Span:
    """One decision in the request's trace. Field names track OTel/OpenInference."""

    trace_id: str
    span_id: str
    name: str
    kind: str = "internal"
    parent_span_id: str | None = None
    start_ms: float = field(default_factory=_now_ms)
    seq: int = field(default_factory=lambda: next(_seq))
    duration_ms: float | None = None
    status: str = "ok"            # "ok" | "error"
    status_message: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    # Whether THIS span may record raw content (prompts/args/outputs). Set from
    # the tracer at creation so set_content() can no-op without a global lookup.
    _capture_content: bool = field(default=False, repr=False)

    def set_attribute(self, key: str, value: Any) -> None:
        """Record a decision/metadata attribute (always kept when tracing is on)."""
        self.attributes[key] = value

    def set_attributes(self, values: dict[str, Any]) -> None:
        self.attributes.update(values)

    def set_content(self, key: str, value: Any) -> None:
        """Record raw content (prompt, tool args, model output).

        No-op unless content capture is on (``INTERGEN_TRACE_CONTENT=1`` AND not
        running as root — see Tracer). Values under a credential-shaped key are
        redacted even when capture is on: credentials must never reach the file.
        """
        if not self._capture_content:
            return
        self.attributes[key] = _REDACTED if _SECRET_KEY_RE.search(key) else value

    def set_status(self, status: str, message: str = "") -> None:
        self.status = status
        self.status_message = message

    def as_record(self) -> dict[str, Any]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "seq": self.seq,
            "name": self.name,
            "kind": self.kind,
            "start_ms": self.start_ms,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "status_message": self.status_message,
            "attributes": self.attributes,
        }


class _NoopSpan:
    """Returned by ``span()`` when tracing is disabled. All methods are no-ops."""

    __slots__ = ()
    trace_id = ""
    span_id = ""
    parent_span_id = None

    def set_attribute(self, key: str, value: Any) -> None: ...
    def set_attributes(self, values: dict[str, Any]) -> None: ...
    def set_content(self, key: str, value: Any) -> None: ...
    def set_status(self, status: str, message: str = "") -> None: ...


_NOOP = _NoopSpan()


class Tracer:
    """Records spans to ``decisions.jsonl``. Disabled (no-op) unless ``INTERGEN_TRACE``."""

    def __init__(self, log_dir: str = _LOG_DIR) -> None:
        self.enabled = _env_flag("INTERGEN_TRACE")
        # Content capture is dev-only: refuse it while running as root even if the
        # env flag is set (a production marker can extend this guard later).
        _content_requested = _env_flag("INTERGEN_TRACE_CONTENT")
        self.capture_content = _content_requested and os.geteuid() != 0
        if _content_requested and not self.capture_content:
            logger.warning("INTERGEN_TRACE_CONTENT ignored: refusing to capture "
                           "raw content while running as root")
        self._log_dir = Path(log_dir)
        self._log_file: Path | None = None
        self._lock = Lock()
        if self.enabled:
            self._setup_log_dir()

    def _setup_log_dir(self) -> None:
        # Mirrors metrics.EventLogger._setup_log_dir: a `--user` service under
        # ProtectSystem=strict cannot write the root-owned /var/log/intergen, so
        # resolve a per-user writable path up front for a non-root process, with
        # an OSError fallback, and disable file writes if nothing is writable
        # (the tracer stays harmless rather than raising into the request path).
        if os.geteuid() != 0 and str(self._log_dir).startswith(("/var/", "/usr/")):
            state_home = Path(os.environ.get(
                "XDG_STATE_HOME", Path.home() / ".local" / "state"))
            self._log_dir = state_home / "intergen"
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            self._log_file = self._create_log_file(self._log_dir / _LOG_FILE)
        except OSError as e:
            fallback = Path.home() / ".local" / "state" / "intergen"
            try:
                fallback.mkdir(parents=True, exist_ok=True)
                self._log_file = self._create_log_file(fallback / _LOG_FILE)
                logger.warning("Cannot write trace to %s (%s); using %s",
                               self._log_dir, e, fallback)
            except OSError:
                self._log_file = None
                logger.warning("No writable location for the decision trace — "
                               "trace file disabled (tracing stays a no-op sink)")

    @staticmethod
    def _create_log_file(path: Path) -> Path:
        # 0600, owner-only. Even metadata-only this is the right default for a
        # security-forward OS; under content capture decisions.jsonl can hold
        # prompts/args. Enforce the mode regardless of umask or a pre-existing file
        # (append-opens in _write preserve it).
        fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
        return path

    def current_trace_id(self) -> str:
        """The active trace id, or "" when not inside a traced request.

        Used to stamp ``RouteResult.trace_id`` so a turn's result links back to
        its decision trace.
        """
        return _current_trace_id.get() or ""

    def current_span(self) -> Any:
        """The active span, or a no-op span when nothing is open / tracing is off.

        Lets code deep in the cascade annotate the active decision without
        threading a span reference through every call —
        ``get_tracer().current_span().set_attribute(...)`` is always safe.
        """
        return _current_span.get() or _NOOP

    @contextlib.contextmanager
    def span(self, name: str, *, kind: str = "internal",
             attributes: dict[str, Any] | None = None) -> Iterator[Any]:
        """Open a span around a decision. Yields a :class:`Span` (or a no-op).

        The first span in a request (no active trace) starts a new ``trace_id``
        and becomes the root; nested spans inherit it and chain via
        ``parent_span_id``.
        """
        if not self.enabled:
            yield _NOOP
            return

        parent = _current_span.get()
        trace_id = _current_trace_id.get() or _new_trace_id()
        span = Span(
            trace_id=trace_id,
            span_id=_new_span_id(),
            name=name,
            kind=kind if kind in _KINDS else "internal",
            parent_span_id=parent.span_id if parent is not None else None,
            attributes=dict(attributes or {}),
            _capture_content=self.capture_content,
        )
        t0 = time.perf_counter()
        tok_span = _current_span.set(span)
        tok_tid = _current_trace_id.set(trace_id)
        try:
            yield span
        except BaseException as e:  # record the failure, then re-raise unchanged
            span.set_status("error", type(e).__name__)
            raise
        finally:
            span.duration_ms = (time.perf_counter() - t0) * 1000.0
            _current_span.reset(tok_span)
            _current_trace_id.reset(tok_tid)
            self._write(span)

    def _write(self, span: Span) -> None:
        if self._log_file is None:
            return
        try:
            line = json.dumps(span.as_record(), default=str) + "\n"
        except (TypeError, ValueError) as e:  # never let a bad attribute break a request
            logger.error("trace serialize failed for span %s: %s", span.name, e)
            return
        with self._lock:
            try:
                with open(self._log_file, "a") as f:
                    f.write(line)
            except OSError as e:
                logger.error("trace write failed: %s", e)


def bind_context() -> contextvars.Context:
    """Snapshot the current context for running work in a worker THREAD.

    ContextVars do not auto-propagate into threads, and ``web_server`` runs the
    LLM off the event loop (``ThreadPoolExecutor.submit`` / ``run_in_executor``)
    with no ``copy_context``. Capture the context here and run the threaded
    callable through it so the active span/trace stays attached, e.g.::

        ctx = bind_context()
        await loop.run_in_executor(None, lambda: ctx.run(_run_llm, ...))

    Returns a plain ``contextvars.Context``; ``ctx.run(fn, *args)`` executes
    ``fn`` with the captured trace context active.
    """
    return contextvars.copy_context()


_tracer: Tracer | None = None


def get_tracer() -> Tracer:
    """Process-wide tracer singleton (constructed on first use)."""
    global _tracer
    if _tracer is None:
        _tracer = Tracer()
    return _tracer
