# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The Glass Pipeline — total, always-on observability for InterGen (M1).

requirement (2026-07-06): *"every single byte is logged… we have to see
EVERYTHING."* The requirement is that **every byte of InterGen's processing is
reconstructible from the trace alone**: warmup state, every routing verdict on
BOTH the D-Bus and the streamed-web path, every decision (offer transitions,
decomposer verdicts, memory reads/writes), the EXACT assembled prompt bytes fed
to the model, the model's output bytes, and the final bytes delivered to chat —
all threaded on one ``turn_id`` end to end.

Relationship to :mod:`intergen.trace`
-------------------------------------
``trace.py`` is the *dev harness* decision tracer: OFF by default, content
capture separately gated, written to ``decisions.jsonl``. The Glass Pipeline is
its complement: **ALWAYS ON**, full-fidelity content by default, written to a
dedicated ``glass.jsonl``. This module deliberately reuses trace.py's proven
mechanics — a ``contextvars``-threaded turn id (asyncio-safe; the one seam that
does not auto-propagate is a worker THREAD, so :func:`bind_context` is provided
for the ``web_server`` ThreadPoolExecutor hop), the writable-path resolution a
``--user`` service needs under ``ProtectSystem=strict``, and 0600 file perms —
without disturbing the harness's separate, dev-only semantics.

Security posture (security-only alignment, made explicit)
---------------------------------------------------------
Full content capture is the mandate, NOT the exception. The one hard line:
credential **values** are never written — logging a secret would manufacture the
very vulnerability the security lens exists to prevent. Two predicates enforce
it. By NAME: any ``detail`` key whose name looks credential-shaped has its whole
value replaced with ``<redacted:key-name>`` (recursively). By SHAPE: inside any
string value, a run matching a named secret format — a PEM private-key block, a
URL carrying an inline password, a crypt(3) hash, a JSON web token, a
vendor-prefixed API token — is replaced with ``<redacted:shape-name>``, and only
that run, because the bytes around it are ordinary content. The shape predicate
exists because the name predicate can only see a secret a caller already
labelled as one, while a secret usually arrives as content: in a prompt, a
command line, a tool result, or a file the person asked about. There is
deliberately no entropy heuristic — a threshold that fired on ordinary content
would put unexplained holes in the record while still missing structured
secrets. Redaction is attested, never silent: every byte is accounted for as
either real content or a named redaction, so a reconstructed timeline has no
unexplained holes. The file is 0600, owner-only.

One limit, stated because a reader would otherwise assume it away: a secret with
no distinguishing shape — a bare high-entropy string under an ordinary key name
— is still written. The corpus in ``intergen/tests/
test_glass_secret_shape_redaction.py`` names the case it leaves behind.

Availability posture
--------------------
Always on. ``INTERGEN_GLASS=0`` disables it as a user-control escape hatch — but
disabling must be LOUD (a startup journal banner + ``glass: false`` in D-Bus
Status), never silent (:func:`glass_enabled`). The writer is best-effort: a write
failure degrades observability gracefully and never raises into a request. When
the log rolls, a ``glass/rotation`` marker is emitted into the fresh file so a gap
in a reconstructed timeline is self-explaining rather than mysterious.
"""

from __future__ import annotations

import asyncio
import contextvars
import itertools
import json
import logging
import os
import re
import secrets
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from threading import Lock
from typing import Any, Iterator

from intergen.private_state import private_dir, private_open

logger = logging.getLogger(__name__)

_LOG_DIR = "/var/log/intergen"
_LOG_FILE = "glass.jsonl"

# Schema version — lets a reader/replayer evolve the format unambiguously.
_SCHEMA_VERSION = 1

# In-process size-based rotation (D2, ratified): a --user service's
# ~/.local/state path is not reliably reached by system logrotate, so the writer
# rolls its own files. Roll at 64 MiB, keep 5.
_ROTATE_BYTES = 64 * 1024 * 1024
_ROTATE_KEEP = 5

# THE WORST CASE ON DISK (REC-18 C03). This used to be written in a comment as
# "~320 MB ceiling", which is _ROTATE_KEEP * _ROTATE_BYTES and leaves out the
# LIVE file — and the live file is on the same disk as the five kept ones. The
# real worst case is six files, 384 MiB. It is derived here rather than written
# down, so it cannot drift from the constants it describes the way the comment
# did, and it is a function so a reader or a gate can ask the module what it
# will cost instead of recomputing it and hoping the two agree.
#
# The largest a SINGLE row may be. Rotation is decided BEFORE a row is written,
# so without this bound a row larger than _ROTATE_BYTES rotated the file away
# and was then written whole into the fresh one: the cap meant nothing for that
# row, and a run of such rows rolled the entire retained history out of the
# record in a handful of writes (measured — one 24 KiB row against an 8 KiB cap
# left a 25,051-byte live file). Keeping every row under this bound is what
# makes the ceiling above a fact rather than a hope.
_MAX_ROW_BYTES = _ROTATE_BYTES // 8


def retention_ceiling_bytes() -> int:
    """The most disk this writer's files can occupy at once."""
    return (_ROTATE_KEEP + 1) * _ROTATE_BYTES

# Process-wide monotonic sequence — a total replay order even for rows born in
# the same millisecond (next() is atomic). Mirrors trace.py's _seq.
#
# N-02/N-03: created at import, this restarted at zero every time the daemon
# started, and two runs writing the same file both wrote seq 0, 1, 2. A reader
# ordering by seq then interleaved two runs into one plausible, wrong timeline.
# The logger reseeds it above the highest number already in the file (see
# GlassLogger._resume_sequence), so the order is total across restarts.
_seq = itertools.count()

# Identifies THIS process's rows. The reseed is the mechanism that keeps the
# order total; this is what makes a FAILED reseed visible instead of silently
# plausible — two rows sharing a sequence number can be told apart, and a reader
# can see where one run stopped and the next began.
_RUN_ID = secrets.token_hex(6)

# How much of an existing glass file to read when resuming the counter. The
# counter is monotonic within a run, so the largest number lives near the end,
# and a bounded read means neither a very large file nor a partly corrupt one
# can make startup slow or make it fail.
_SEQ_SCAN_BYTES = 256 * 1024


def _highest_seq_in(path: Path) -> int | None:
    """The largest sequence number in the tail of an existing glass file.

    None when the file is absent, empty, unreadable, or carries no parseable
    sequence number — every one of which is a real state, and each is reported
    to the reader in the ``glass/sequence_resumed`` row rather than guessed at.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size == 0:
        return None
    highest: int | None = None
    try:
        with open(path, "rb") as f:
            if size > _SEQ_SCAN_BYTES:
                f.seek(size - _SEQ_SCAN_BYTES)
                f.readline()  # discard the line this offset landed inside
            for raw in f:
                try:
                    row = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    continue  # a torn tail line must not stop the scan
                seq = row.get("seq")
                if isinstance(seq, int) and not isinstance(seq, bool):
                    if highest is None or seq > highest:
                        highest = seq
    except OSError:
        return None
    return highest

# Credential-shaped attribute keys whose VALUE must never be persisted. Matched
# case-insensitively as a substring of the key; the value becomes an attested
# in-place placeholder. Kept in lockstep with trace.py's _SECRET_KEY_RE (the
# security rule is identical); declared locally so glass.py has no dependency on
# a private symbol in another module.
_SECRET_KEY_RE = re.compile(
    r"pass(word|wd|phrase)|secret|token|api[_-]?key|authorization|"
    r"credential|private[_-]?key|keyring|bearer",
    re.IGNORECASE,
)

# Credential-shaped CONTENT. The key-name predicate above can only see a secret
# a caller already labelled as one; a secret usually arrives as CONTENT — inside
# a prompt, a command line, a tool result, or a file the person asked about.
# Measured on the R001.1 tree with an eight-case corpus: seven secret-shaped
# values written under ordinary key names reached the record byte-identical, and
# only the one placed under the key name "api_key" was redacted.
#
# NAMED SHAPES ONLY, DELIBERATELY — no entropy threshold. This writer's mandate
# is full-fidelity capture: a heuristic that fires on ordinary content would put
# unexplained holes in the record while still missing structured secrets. Every
# shape below is a published format with a fixed marker, each is proved in both
# directions against the corpus in
# intergen/tests/test_glass_secret_shape_redaction.py, and each leaves an
# attested placeholder naming WHAT was removed.
#
# Order matters: a URL carrying inline credentials is matched as a URL before a
# token prefix inside it can be matched on its own, so the placeholder names the
# larger, more informative shape.
SECRET_SHAPES: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    # A PEM private key of any flavour, header through footer.
    ("private-key-block", re.compile(
        r"-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY-----"
        r".*?"
        r"-----END [A-Z0-9 ]{0,40}PRIVATE KEY-----",
        re.DOTALL)),
    # scheme://user:password@host — the password is in the authority section.
    ("url-with-password", re.compile(
        r"\b[a-z][a-z0-9+.\-]{1,31}://[^\s/:@]{1,128}:[^\s/@]{1,128}@[^\s/?#]{1,255}")),
    # $N$salt$hash — the crypt(3) format used by /etc/shadow.
    ("crypt-hash", re.compile(
        r"\$[0-9a-z]{1,3}\$[^\s:$]{1,80}\$[^\s:]{10,}")),
    # header.payload.signature, base64url. "eyJ" is the base64 of the opening
    # of a JSON object, which is what makes this specific; the signature segment
    # is allowed to be short or empty because an unsigned token has none.
    ("json-web-token", re.compile(
        r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]*")),
    # Vendor-prefixed API tokens with a published, fixed prefix.
    ("provider-token", re.compile(
        r"\b(?:sk-[A-Za-z0-9\-_]{16,}"
        r"|ghp_[A-Za-z0-9]{20,}"
        r"|gho_[A-Za-z0-9]{20,}"
        r"|ghs_[A-Za-z0-9]{20,}"
        r"|github_pat_[A-Za-z0-9_]{20,}"
        r"|AKIA[0-9A-Z]{16})\b")),
)

# No shape above can match a string shorter than this, so a shorter one skips the
# scan entirely. Derived from the shortest possible match (AKIA plus sixteen
# characters), not chosen — a wrong guess here would silently stop redacting.
_MIN_SHAPE_LEN = 20

# The names above are PUBLIC because intergen/trace.py imports them. The older
# key-name pattern in this module was deliberately declared as a second copy of
# trace.py's "so glass.py has no dependency on a private symbol in another
# module", and that duplication is precisely what let the two writers drift: this
# module grew a content predicate and the tracer did not. One definition, shared,
# is the correction; the lockstep is now true by construction rather than by
# comment. Anything added here changes BOTH writers, which is the point.
_SECRET_SHAPES = SECRET_SHAPES  # the private name kept for readers of this file

# THE TERMINAL VOCABULARY (REC-17). A turn ends exactly once, and these are the
# ways it may end. Named in ONE place so every interface ends a turn the same
# way and a reader can ask "how did this turn finish?" without knowing which
# interface served it.
#
#   final      the turn produced an answer
#   refused    the turn was declined — an empty message, an unavailable router,
#              a denied tool. A refusal is an outcome, not an absence of one.
#   error      the turn raised
#   cancelled  the client went away mid-turn
#   timeout    a deadline fired
#   unreported the turn ended without any of the above; see _synthesize_terminal
#
# NOTE: "timeout" is what the web server's whole-turn route deadline ends a turn
# with. The deadline decides the ending; this module only names it, so the two
# stay in one vocabulary and a reader asking "how did this turn finish?" gets
# the same answer whichever interface served it.
_TERMINAL_PHASE = "delivery"
_TERMINAL_EVENTS = ("final", "refused", "error", "cancelled", "timeout",
                    "unreported")

#: Written when a turn ends with no terminal event of its own.
_TERMINAL_FALLBACK = "unreported"

#: Written INSTEAD of a second terminal, so a call site that ends a turn twice
#: stays findable rather than silently winning or silently losing.
_TERMINAL_AFTER_TERMINAL = "terminal_after_terminal"


def is_terminal_event(phase: str, event: str) -> bool:
    """Whether a (phase, event) pair ends a turn.

    One predicate, so the writer's enforcement and any reader's reconstruction
    cannot drift apart — a gate and a reader disagreeing about what "the end"
    means is how an exactly-once guarantee quietly stops holding.
    """
    return phase == _TERMINAL_PHASE and event in _TERMINAL_EVENTS


# Per-context turn state. A ContextVar is per-Context, so concurrent turns on the
# aiohttp event loop stay isolated and nested calls inherit the same turn id.
_current_turn_id: ContextVar[str | None] = ContextVar("intergen_glass_turn", default=None)
_current_turn_start: ContextVar[float | None] = ContextVar("intergen_glass_start", default=None)
_current_iface: ContextVar[str | None] = ContextVar("intergen_glass_iface", default=None)

# Whether the ACTIVE turn has already ended. A ContextVar and not a set keyed by
# turn id: turns overlap on the event loop, and process-wide state would make one
# turn's ending look like another's.
#
# The VALUE is a one-element list rather than a bool, and that is load-bearing.
# :func:`bind_context` hands work to a worker thread through
# ``contextvars.copy_context()``, which copies the MAPPING. A bool written in the
# copy would be invisible to the turn that made the copy, so a terminal emitted
# from the worker thread would not count as the turn's end — and the turn's own
# exit would then synthesize a SECOND one, breaking the exactly-once guarantee in
# precisely the seam the bind exists to hold together. A shared mutable cell is
# seen from both sides. It stays per-turn because :func:`turn` installs a fresh
# cell, and it is absent (None) outside any turn.
_current_turn_ended: ContextVar[list[bool] | None] = ContextVar(
    "intergen_glass_turn_ended", default=None)

# Guards the claim below. A terminal can be attempted from the event loop and
# from a worker thread bound into the same turn; without this both could read the
# cell as unclaimed and both would write a terminal.
_TURN_END_LOCK = Lock()


def _claim_turn_end() -> bool:
    """Claim the active turn's single terminal slot.

    True when this caller may write the terminal; False when the turn has
    already ended, in which case the caller records the ATTEMPT instead of
    writing a second terminal. Outside a turn there is nothing to claim and
    nothing to refuse, so an emission that carries its own turn id (startup,
    warmup) is never affected.
    """
    cell = _current_turn_ended.get()
    if cell is None:
        return True
    with _TURN_END_LOCK:
        if cell[0]:
            return False
        cell[0] = True
        return True


def _turn_has_ended() -> bool:
    """Whether the active turn already has its terminal row."""
    cell = _current_turn_ended.get()
    return bool(cell and cell[0])


def _env_disabled() -> bool:
    return os.environ.get("INTERGEN_GLASS", "").strip().lower() in ("0", "false", "no", "off")


def _now_ms() -> float:
    return time.time() * 1000.0


def new_turn_id() -> str:
    """A fresh turn id (16 hex chars). One per user turn; threads the whole chain."""
    return secrets.token_hex(8)


def current_turn_id() -> str:
    """The active turn id, or "" outside a turn. Lets code join delivery to a turn."""
    return _current_turn_id.get() or ""


@contextmanager
def turn(turn_id: str, iface: str) -> Iterator[str]:
    """Bind a turn's id + interface for the duration of a block.

    Everything emitted inside — however deep in router/decomposer/memory/llm —
    shares this ``turn_id`` and is timestamped relative to the turn's start, with
    no signature threading (deep code reads the ContextVar via :func:`emit`).
    """
    t0 = _now_ms()
    tok_id = _current_turn_id.set(turn_id)
    tok_start = _current_turn_start.set(t0)
    tok_iface = _current_iface.set(iface)
    tok_ended = _current_turn_ended.set([False])
    try:
        yield turn_id
    except asyncio.CancelledError:
        # The client went away mid-turn. That is an OUTCOME and the record owes
        # the reader the fact, not a record that simply stops.
        _synthesize_terminal("cancelled")
        raise
    except BaseException as exc:
        # Includes the crashes that reach web_server's no-wedge backstop, which
        # answers the client and emits nothing of its own.
        _synthesize_terminal("error", exc=exc)
        raise
    else:
        # The turn ended without saying how: an early refusal (empty message,
        # router unavailable) or a path that simply returns. Still an ending.
        _synthesize_terminal(_TERMINAL_FALLBACK)
    finally:
        _current_turn_ended.reset(tok_ended)
        _current_turn_id.reset(tok_id)
        _current_turn_start.reset(tok_start)
        _current_iface.reset(tok_iface)


def _synthesize_terminal(event: str, exc: BaseException | None = None) -> None:
    """Write the terminal row the call site did not write.

    Marked ``synthesized`` in its detail so a reader can tell an ending an
    interface MEANT from one this module supplied. Without that mark the
    guarantee would convert a silent gap into a silent pass, which is the exact
    failure it exists to remove.

    Best effort by construction: recording how a turn ended must never change
    how it ended, so a failure here is logged and swallowed and the original
    exception, when there is one, propagates unchanged.
    """
    if _turn_has_ended():
        return
    detail: dict[str, Any] = {"synthesized": True}
    if exc is not None:
        detail["exception"] = type(exc).__name__
    try:
        emit(_TERMINAL_PHASE, event, detail=detail)
    except Exception:  # the writer is best-effort; the turn's outcome is not ours
        logger.debug("glass could not synthesize the %s terminal for turn %s",
                     event, current_turn_id(), exc_info=True)


def bind_context() -> contextvars.Context:
    """Snapshot the current context for work handed to a worker THREAD.

    ContextVars do not auto-propagate into threads, and ``web_server`` runs the
    LLM off the event loop in a ThreadPoolExecutor. Capture here and run the
    threaded callable through it so the active ``turn_id`` stays attached::

        ctx = bind_context()
        executor.submit(lambda: ctx.run(_run_llm))

    copy_context() snapshots the whole context, so one bind carries both the
    glass turn id and any trace.py span in flight.
    """
    return contextvars.copy_context()


def _bound_row(record: dict[str, Any]) -> str:
    """Re-serialize an oversized row so it fits under :data:`_MAX_ROW_BYTES`.

    THE SHORTENING IS ATTESTED, NEVER SILENT. The row keeps its turn id, its
    sequence number, its phase and its event, so it is still joinable and still
    in order; its detail is replaced by a note saying how large the row was,
    what the limit is, and how much of the original detail was kept — followed
    by that kept prefix. A trace that quietly drops what it could not fit would
    be deciding on its own what the record does not have to say, which is the
    one thing this module exists not to do.

    The kept prefix is halved until the whole row fits, because a prefix
    re-embedded as a JSON string can grow when its quotes and backslashes are
    escaped, so a single subtraction is not enough to guarantee the bound.
    """
    try:
        original = json.dumps(record.get("detail", {}), default=str)
    except (TypeError, ValueError):
        original = "<detail could not be serialized>"
    original_bytes = len(json.dumps(record, default=str)) + 1
    budget = _MAX_ROW_BYTES // 2
    while budget >= 0:
        bounded = dict(record)
        kept = original[:budget]
        bounded["detail"] = {
            "glass_oversized_row": {
                "original_bytes": original_bytes,
                "limit_bytes": _MAX_ROW_BYTES,
                "kept_bytes": len(kept),
                "reason": "a single row must stay smaller than the rotation "
                          "size, or one row rolls the retained history out of "
                          "the record",
            },
            "truncated_detail": kept,
        }
        try:
            line = json.dumps(bounded, default=str) + "\n"
        except (TypeError, ValueError):
            budget = budget // 2 if budget else -1
            continue
        if len(line) <= _MAX_ROW_BYTES:
            return line
        budget = budget // 2 if budget else -1
    # Nothing fit, which means the bound is smaller than the row's own skeleton.
    # Say that, rather than write something larger than the module promised.
    return json.dumps({
        "v": _SCHEMA_VERSION,
        "turn_id": record.get("turn_id", "no-turn"),
        "seq": record.get("seq"),
        "run": _RUN_ID,
        "ts": record.get("ts"),
        "t_rel_ms": None,
        "iface": record.get("iface", "daemon"),
        "phase": record.get("phase", "glass"),
        "event": record.get("event", "unknown"),
        "detail": {"glass_oversized_row": {
            "original_bytes": original_bytes,
            "limit_bytes": _MAX_ROW_BYTES,
            "kept_bytes": 0,
            "reason": "the row did not fit under the size bound even with its "
                      "detail removed",
        }},
        "dur_ms": None,
    }, default=str) + "\n"


def redact_secret_shapes(text: str) -> str:
    """Replace each secret-SHAPED run inside ``text`` with a named placeholder.

    In place and only the match: the bytes around a secret are ordinary content
    and this writer's mandate is to keep them. A caller reading the record sees
    ``<redacted:provider-token>`` where the token was and can tell what kind of
    thing stood there, which is the same attestation the key-name predicate
    gives — never a silent hole.
    """
    if len(text) < _MIN_SHAPE_LEN:
        return text
    for name, pattern in SECRET_SHAPES:
        text = pattern.sub(f"<redacted:{name}>", text)
    return text


# The private name this module used before the tracer needed to share it.
_redact_shapes = redact_secret_shapes


def _redact(value: Any, key: str = "") -> Any:
    """Attested in-place redaction: a credential-shaped key's value becomes
    ``<redacted:key-name>``; a secret-shaped run inside any string becomes
    ``<redacted:shape-name>``; dicts/lists are scrubbed recursively. Content is
    never silently dropped — a redaction is a named, visible placeholder."""
    if key and _SECRET_KEY_RE.search(key):
        return f"<redacted:{key}>"
    if isinstance(value, dict):
        return {k: _redact(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    if isinstance(value, str):
        return redact_secret_shapes(value)
    return value


class GlassLogger:
    """Always-on full-fidelity turn tracer writing ``glass.jsonl``.

    Disabled only by ``INTERGEN_GLASS=0`` (loud, never silent). Best-effort: a
    write/rotate failure is logged and swallowed — observability degrades, the
    request never does.
    """

    def __init__(self, log_dir: str = _LOG_DIR):
        self.enabled = not _env_disabled()
        self._log_dir = Path(log_dir)
        self._log_file: Path | None = None
        self._lock = Lock()
        if self.enabled:
            self._setup_log_dir()
            self._resume_sequence()

    def _resume_sequence(self) -> None:
        """Continue the sequence above what the file already holds (N-02/N-03).

        Called once, at construction, before any row of this run is written and
        before any other thread can reach the logger — get_glass() serializes
        construction for exactly that reason.

        The reseed is ATTESTED: a ``glass/sequence_resumed`` row says what the
        counter continued from, or says plainly that there was nothing to
        continue from. A restart is a real discontinuity in the record, and a
        reader is owed the boundary rather than left to infer it from a jump.
        """
        global _seq
        if self._log_file is None:
            return
        highest = _highest_seq_in(self._log_file)
        if highest is not None:
            _seq = itertools.count(highest + 1)
        self._write_row({
            "v": _SCHEMA_VERSION,
            "turn_id": "glass-sequence",
            "seq": next(_seq),
            "ts": _now_ms() / 1000.0,
            "t_rel_ms": None,
            "run": _RUN_ID,
            "iface": "daemon",
            "phase": "glass",
            "event": "sequence_resumed",
            "detail": {
                "resumed_from": highest,
                "reason": (
                    "continued above the highest sequence number already in "
                    "this file" if highest is not None else
                    "nothing to continue from: this file holds no readable "
                    "sequence number"),
            },
            "dur_ms": None,
        })

    def _setup_log_dir(self) -> None:
        # Mirrors metrics.EventLogger / trace.Tracer: a `--user` service under
        # ProtectSystem=strict cannot write the root-owned /var/log/intergen, so
        # resolve a per-user writable path for a non-root process, with an
        # OSError fallback; if nothing is writable, disable file writes (emit()
        # guards on _log_file) rather than raising into the daemon.
        if os.geteuid() != 0 and str(self._log_dir).startswith(("/var/", "/usr/")):
            state_home = Path(os.environ.get(
                "XDG_STATE_HOME", Path.home() / ".local" / "state"))
            self._log_dir = state_home / "intergen"
        try:
            private_dir(self._log_dir)
            self._log_file = self._create_log_file(self._log_dir / _LOG_FILE)
        except OSError as e:
            fallback = Path.home() / ".local" / "state" / "intergen"
            try:
                private_dir(fallback)
                self._log_file = self._create_log_file(fallback / _LOG_FILE)
                logger.warning("Cannot write glass to %s (%s); using %s",
                               self._log_dir, e, fallback)
            except OSError:
                self._log_file = None
                logger.warning("No writable location for the glass trace — "
                               "file disabled (glass emits become no-ops)")

    @staticmethod
    def _create_log_file(path: Path) -> Path:
        # 0600, owner-only: glass holds prompt + model + delivered bytes.
        fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
        return path

    def emit(self, phase: str, event: str, *,
             detail: dict[str, Any] | None = None,
             dur_ms: float | None = None,
             turn_id: str | None = None,
             iface: str | None = None) -> None:
        """Write one glass row for the active turn. No-op when disabled/unwritable.

        turn_id / iface may be passed explicitly for emissions that run OUTSIDE a
        turn contextvar scope — chiefly the startup/warmup sequence, some of which
        runs in a daemon thread that a ContextVar cannot reach. Turn-scoped
        callers (web/dbus) omit both and inherit the active turn.
        """
        if not self.enabled or self._log_file is None:
            return
        # EXACTLY ONE TERMINAL PER TURN (REC-17). A second terminal is not
        # dropped: it is written under a different event name, so a call site
        # that ends an already-ended turn stays findable instead of silently
        # winning or silently losing. Only turn-scoped emissions take part — one
        # carrying its own turn_id runs outside the turn ContextVars (startup,
        # warmup) and has no turn to end.
        if (turn_id is None and _current_turn_id.get() is not None
                and is_terminal_event(phase, event)):
            if not _claim_turn_end():
                detail = dict(detail or {})
                detail["refused_terminal"] = event
                event = _TERMINAL_AFTER_TERMINAL
        now = _now_ms()
        start = None if turn_id else _current_turn_start.get()
        record = {
            "v": _SCHEMA_VERSION,
            "turn_id": turn_id or _current_turn_id.get() or "no-turn",
            "seq": next(_seq),
            "run": _RUN_ID,
            "ts": now / 1000.0,
            "t_rel_ms": round(now - start, 3) if start is not None else None,
            "iface": iface or _current_iface.get() or "daemon",
            "phase": phase,
            "event": event,
            "detail": _redact(detail or {}),
            "dur_ms": round(dur_ms, 3) if dur_ms is not None else None,
        }
        self._write_row(record)

    def _write_row(self, record: dict[str, Any]) -> None:
        """Append one already-built row. The single place bytes reach the file,
        so every row — an ordinary emission, the rotation marker, the sequence
        resume — gets the same locking, the same rotation check, the same size
        bound and the same file mode."""
        if self._log_file is None:
            return
        try:
            line = json.dumps(record, default=str) + "\n"
        except (TypeError, ValueError) as e:  # never let a bad payload break a turn
            logger.error("glass serialize failed for %s/%s: %s",
                         record.get("phase"), record.get("event"), e)
            return
        if len(line) > _MAX_ROW_BYTES:
            line = _bound_row(record)
        with self._lock:
            try:
                self._rotate_if_needed_locked(len(line))
                # private_open, not open: if the file is rotated away or removed
                # out from under us, a plain append RE-CREATES it at 0644 and
                # the trace — prompt, model and delivered bytes — silently
                # becomes world-readable with no signal to anyone.
                with private_open(self._log_file, "a") as f:
                    f.write(line)
            except OSError as e:
                logger.error("glass write failed: %s", e)

    def _rotate_if_needed_locked(self, incoming_bytes: int) -> None:
        """Roll glass.jsonl -> .1 .. .N (keep _ROTATE_KEEP) when it would exceed
        the size cap, and drop a self-explaining marker into the fresh file so a
        reconstructed timeline's gap is attested, not mysterious. Caller holds
        the lock. Best-effort: a rotation failure keeps writing to the old file."""
        assert self._log_file is not None
        try:
            size = self._log_file.stat().st_size
        except OSError:
            return
        if size + incoming_bytes < _ROTATE_BYTES:
            return
        base = self._log_file
        try:
            oldest = base.with_name(f"{base.name}.{_ROTATE_KEEP}")
            if oldest.exists():
                oldest.unlink()
            for i in range(_ROTATE_KEEP - 1, 0, -1):
                src = base.with_name(f"{base.name}.{i}")
                if src.exists():
                    src.rename(base.with_name(f"{base.name}.{i + 1}"))
            base.rename(base.with_name(f"{base.name}.1"))
            self._create_log_file(base)
            marker = {
                "v": _SCHEMA_VERSION, "turn_id": "glass-rotation",
                "seq": next(_seq), "run": _RUN_ID,
                "ts": _now_ms() / 1000.0, "t_rel_ms": None,
                "iface": "daemon", "phase": "glass", "event": "rotation",
                "detail": {"rolled_prev_to": f"{base.name}.1",
                           "keep": _ROTATE_KEEP, "cap_bytes": _ROTATE_BYTES},
                "dur_ms": None,
            }
            with private_open(base, "a") as f:
                f.write(json.dumps(marker) + "\n")
        except OSError as e:
            logger.error("glass rotation failed (continuing on current file): %s", e)


_glass: GlassLogger | None = None

# Construction is serialized. Two threads reaching an unbuilt logger at once
# would each construct one and each reseed the sequence counter, and the second
# reseed would hand out numbers the first had already promised — the exact
# collision the reseed exists to remove.
_GLASS_INIT_LOCK = Lock()


def get_glass() -> GlassLogger:
    """Process-wide GlassLogger singleton (constructed on first use)."""
    global _glass
    if _glass is None:
        with _GLASS_INIT_LOCK:
            if _glass is None:
                _glass = GlassLogger()
    return _glass


def glass_enabled() -> bool:
    """True unless INTERGEN_GLASS=0. Surfaced in D-Bus Status so a disabled glass
    is never silent (the operator's loud-kill-switch rider)."""
    return get_glass().enabled


def emit(phase: str, event: str, *,
         detail: dict[str, Any] | None = None,
         dur_ms: float | None = None,
         turn_id: str | None = None,
         iface: str | None = None) -> None:
    """Module-level convenience — emit one glass row for the active turn."""
    get_glass().emit(phase, event, detail=detail, dur_ms=dur_ms,
                     turn_id=turn_id, iface=iface)


# ── Reader (user-control: the user gets to see everything their agent did) ──

def default_glass_path() -> Path:
    """The canonical glass.jsonl path per XDG Base Directory spec (the reader's
    view of what `intergen glass` shows)."""
    xdg = os.environ.get("XDG_STATE_HOME") or str(
        Path.home() / ".local" / "state")
    return Path(xdg) / "intergen" / _LOG_FILE


def read_rows(path: Path | None = None) -> "Iterator[dict[str, Any]]":
    """Yield each parsed glass row. Skips malformed lines (append-only log; a
    rare torn tail line must not stop the reconstruction of the rest)."""
    p = path if path is not None else default_glass_path()
    if not p.exists():
        return
    with open(p, "r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                continue
