# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""pkm progress — the per-part progress standard for long-running operations.

A package manager that works for a minute while printing nothing is
indistinguishable from a package manager that has hung, and a user who
cannot tell those apart cannot trust either. Decided 2026-08-06: every
user-visible pkm operation announces every part while it runs, and this
module states that rule ONCE so no part has to re-invent it.

THE SHAPE, which the one-time database update established and this module
generalizes rather than replacing:

    announce   — what is about to happen, before it starts
    step       — which part is running now
    heartbeat  — while a part is still running, that it is still working
    finish     — what happened, when it is over

VERBOSITY. The announce and finish lines print at EVERY level, ``-q``
included. A request for less output is not a request for a silent
multi-minute pause: the two lines that bracket the wait are the ones that
tell a script's reader, and a person watching a terminal, that the pause is
work rather than a freeze. The step and heartbeat detail between them is
ordinary prose and is suppressed at ``-q`` as usual.

EVERY CONSOLE WRITE IS GUARDED. This reporting exists to make a long
operation visible; it must never be able to STOP one. A closed stdout, a
broken pipe or a console module that raises would otherwise propagate out
of a heartbeat and abort the very work it was added to describe — turning a
reporting improvement into the more serious failure. Same reasoning as the
one-time update report this module generalizes.

THE PARTS. ``PART_LABELS`` names the parts a user-visible operation can be
made of. It is here so that the vocabulary is one list rather than a
per-command invention, and so a test can ask which parts a command claims
to have.
"""

import time

# The named parts of a user-visible pkm operation. Stated once, in the order
# a transaction meets them, so every command reports the same part by the
# same name. A part that is not in this list is not a part of the standard —
# add it here first, deliberately, rather than letting a new label appear in
# one command's output only.
PART_SYNC = "sync"
PART_DOWNLOAD = "download"
PART_SIGNATURE = "signature"
PART_EXTRACT = "extract"
PART_DEPLOY = "deploy"
PART_HOOKS = "hooks"
PART_SCAN = "scan"          # reading the installed corpus (verify, import)
PART_REMOVE = "remove"      # unlinking a package's files
PART_DATABASE = "database"  # SQLite maintenance (vacuum, migrations)

PART_LABELS = (
    PART_SYNC,
    PART_DOWNLOAD,
    PART_SIGNATURE,
    PART_EXTRACT,
    PART_DEPLOY,
    PART_HOOKS,
    PART_SCAN,
    PART_REMOVE,
    PART_DATABASE,
)

# How long a part may run before it must say it is still working, and how
# often it repeats afterwards. A part that finishes quickly never prints a
# heartbeat: the first one waits HEARTBEAT_AFTER seconds, which is longer
# than any part takes on a system small enough for the question not to
# arise. These are the r47 values, unchanged — the shape is being extended,
# not re-tuned.
HEARTBEAT_AFTER = 2.0
HEARTBEAT_EVERY = 3.0

# A long operation must not be judged by wall time alone: a part that
# processes a hundred thousand items in bursts can sit inside one burst for
# longer than HEARTBEAT_AFTER. Callers that iterate call tick() per item;
# this is how often tick() is allowed to consult the clock, so the cost of
# the heartbeat itself stays far below the cost of the work.
TICK_CLOCK_EVERY = 64


def _emitters():
    """Resolve pkm.output's emitters, or no-ops if it cannot be imported.

    Imported lazily, inside the call, for the same two reasons the one-time
    database update does it: this module is used from the bottom of pkm's
    import graph and must not add a top-level dependency there, and an
    operation driven by a non-pkm caller (a test, a script) must not fail
    because a console module could not be imported.
    """
    try:
        from . import output as _o
        return (_o.emit_done, _o.emit_info, _o.process_level, _o.VERBOSE)
    except Exception:
        noop = lambda *_a, **_k: None       # noqa: E731
        return (noop, noop, lambda: 0, 99)


class LongOperation:
    """Announce → step → heartbeat → finish, for one long-running operation.

    Use it as a context manager so the closing line is emitted on every
    exit path, including an exception:

        with LongOperation("Verifying installed packages",
                           detail="1,006 packages to check") as op:
            for pkg in packages:
                op.tick()
                ...
            op.finish("1,006 ok")

    A caller that does not call finish() gets a truthful closing line
    anyway; a caller that leaves via an exception gets the did-not-complete
    line, so a user is never left looking at a step line that never ends.
    """

    def __init__(self, title, detail=None, parts=None):
        """
        Args:
            title: what the operation is, in plain words. Printed at every
                verbosity level.
            detail: an optional second sentence — the scale of the work, so
                a user can judge whether the wait is reasonable. Ordinary
                prose; suppressed at -q.
            parts: the PART_LABELS this operation is made of, when it has
                named parts. Recorded so a caller (and a test) can ask.
        """
        self.title = title
        self.detail = detail
        self.parts = tuple(parts or ())
        self._step = None
        self._step_started = None
        self._last_beat = 0.0
        self._started = None
        self._ticks = 0
        self._finished = False
        self._out = None

    # -- guarded output -------------------------------------------------

    def _resolve(self):
        if self._out is None:
            self._out = _emitters()
        return self._out

    def _say_always(self, text):
        try:
            self._resolve()[0](text)
        except Exception:
            pass

    def _say(self, text):
        try:
            self._resolve()[1](text)
        except Exception:
            pass

    def _verbose(self):
        try:
            _done, _info, level, verbose = self._resolve()
            return level() >= verbose
        except Exception:
            return False

    # -- the report -----------------------------------------------------

    def announce(self):
        """Say what is about to happen, before it starts."""
        self._started = time.monotonic()
        self._say_always(f"{self.title}…")
        if self.detail:
            self._say(f"  {self.detail}")
        return self

    def step(self, text):
        """Begin a named part, arming its heartbeat."""
        self.end_step()
        self._step = text
        self._step_started = time.monotonic()
        self._last_beat = self._step_started
        self._ticks = 0
        self._say(f"  {text}")

    def end_step(self):
        """Close the current part. Reports its duration only at -v."""
        if self._step is None:
            return
        if self._verbose() and self._step_started is not None:
            self._say(
                f"    {self._step} took "
                f"{time.monotonic() - self._step_started:.1f}s"
            )
        self._step = None

    def tick(self, note=None):
        """Report liveness from inside a loop.

        Called once per item. Consults the clock only every
        TICK_CLOCK_EVERY calls, so a tight loop pays a counter increment
        rather than a syscall, and prints only once a part has been running
        longer than HEARTBEAT_AFTER.

        ``note`` is an optional short description of where the work is —
        the current package name, say — so the heartbeat says what it is
        working ON and not only that it is working.
        """
        self._ticks += 1
        if self._ticks % TICK_CLOCK_EVERY:
            return
        self.beat(note)

    def beat(self, note=None):
        """Emit a heartbeat if this part has been running long enough.

        Never raises: a heartbeat that could throw would be able to abort
        the work it is describing, which is the failure this reporting
        exists to prevent.
        """
        try:
            if self._step_started is None:
                return
            now = time.monotonic()
            if (now - self._step_started >= HEARTBEAT_AFTER
                    and now - self._last_beat >= HEARTBEAT_EVERY):
                self._last_beat = now
                elapsed = now - self._step_started
                where = f" — {note}" if note else ""
                self._say(
                    f"    still working — {elapsed:.0f}s on this step{where}"
                )
        except Exception:
            pass

    def finish(self, outcome):
        """Close the operation with what happened. Printed at every level."""
        self.end_step()
        self._finished = True
        elapsed = (time.monotonic() - self._started) if self._started else 0.0
        self._say_always(f"{self.title}: {outcome} ({elapsed:.1f}s)")

    def failed(self, reason=None):
        """Close the operation when it did not complete.

        Printed at every level: a user who was told the work started must
        be told it stopped, whatever verbosity they asked for.
        """
        self.end_step()
        self._finished = True
        tail = f" — {reason}" if reason else ""
        self._say_always(f"{self.title}: did not complete{tail}.")

    # -- context manager ------------------------------------------------

    def __enter__(self):
        self.announce()
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._finished:
            return False
        if exc_type is not None:
            self.failed(str(exc) if exc else exc_type.__name__)
        else:
            # A caller that completed without stating an outcome still gets
            # a closing line. Silence here would reintroduce exactly the
            # open-ended pause this module exists to close.
            self.finish("done")
        return False


class ByteProgress:
    """Bytes / percent / rate for a transfer, on one rewritten line.

    The download leg is the part a user watches most closely and the part
    that printed the least: a blocking copy of a half-gigabyte archive
    showed a bare cursor for minutes. This reports how much has arrived, how
    far along that is when the total is known, and how fast it is moving.

    ON A TERMINAL the line is rewritten in place with a carriage return, so
    a long download occupies one line rather than a scrolling column. OFF a
    terminal — a log file, a systemd unit, a captured build — carriage
    returns produce an unreadable single line, so progress is emitted as
    ordinary separate lines and far less often. The distinction is made from
    the stream itself, never assumed.

    Suppressed entirely at -q: unlike the announce/finish pair, an
    in-progress percentage has no value to a script and would flood a log.
    """

    # How often a rewritten terminal line may update, and how often a
    # non-terminal log line may be written. The log interval is much longer
    # on purpose: its job is to prove liveness in a captured log, not to
    # animate.
    TTY_INTERVAL = 0.2
    LOG_INTERVAL = 10.0

    def __init__(self, label, total_bytes=None, stream=None, level=None):
        self.label = label
        self.total = int(total_bytes) if total_bytes else None
        self._stream = stream
        self._level = level
        self._done = 0
        self._started = time.monotonic()
        self._last = 0.0
        self._wrote_tty_line = False

    def _resolve_stream(self):
        if self._stream is not None:
            return self._stream
        import sys
        return sys.stdout

    def _resolve_level(self):
        if self._level is not None:
            return self._level
        try:
            from . import output as _o
            return _o.process_level()
        except Exception:
            return 1

    def _is_tty(self, stream):
        try:
            return bool(stream.isatty())
        except Exception:
            return False

    def advance(self, n_bytes):
        """Record ``n_bytes`` more transferred, printing if it is time."""
        try:
            self._done += int(n_bytes)
            now = time.monotonic()
            stream = self._resolve_stream()
            tty = self._is_tty(stream)
            interval = self.TTY_INTERVAL if tty else self.LOG_INTERVAL
            if self._last and (now - self._last) < interval:
                return
            self._last = now
            self._emit(stream, tty, now)
        except Exception:
            pass

    def _render(self, now):
        from .output import human_size
        elapsed = max(now - self._started, 1e-6)
        rate = self._done / elapsed
        got = human_size(self._done, precision=1)
        speed = f"{human_size(rate, precision=1)}/s"
        if self.total:
            pct = min(100.0, 100.0 * self._done / self.total)
            of = human_size(self.total, precision=1)
            return f"  {self.label}  {got} of {of}  {pct:5.1f}%  {speed}"
        return f"  {self.label}  {got}  {speed}"

    def _emit(self, stream, tty, now):
        if self._resolve_level() <= 0:      # QUIET
            return
        line = self._render(now)
        if tty:
            print(f"\r{line}", end="", file=stream, flush=True)
            self._wrote_tty_line = True
        else:
            print(line, file=stream, flush=True)

    def close(self, outcome=None):
        """Finish the transfer line.

        On a terminal the in-place line is terminated with a newline so the
        next output starts cleanly; the final figures are printed either
        way, because the completed size and average rate are the part of
        this report worth keeping in a log.
        """
        try:
            if self._resolve_level() <= 0:
                return
            stream = self._resolve_stream()
            now = time.monotonic()
            line = self._render(now)
            if outcome:
                line += f"  {outcome}"
            if self._is_tty(stream) and self._wrote_tty_line:
                print(f"\r{line}", file=stream, flush=True)
            else:
                print(line, file=stream, flush=True)
        except Exception:
            pass
