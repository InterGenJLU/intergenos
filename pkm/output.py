# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""pkm output — user-facing transparency layer.

The PRIME DIRECTIVE says the user must understand what the system is doing.
A package manager that touches the filesystem and reaches the network must
SHOW its work — not collapse every operation into "[OK]" and "(N files)".

This module is the single presentation surface for pkm's mutating commands.
The actual machinery (download, signature verify, sha verify, extract,
deploy, hooks) already happens in repo.py / installer.py / remover.py; the
Reporter makes each step visible, calibrated to apt / pacman / dnf — enough
to know what changed and that it was verified, not a wall of internals that
reads like a crash.

Verbosity levels:
    QUIET  (-q) — summary lines only; for scripts and Forge's own UI.
    NORMAL      — the informative default: phase lines, one count line per
                  deploy/remove, and the per-directory rollup only above
                  DEPLOY_PATH_THRESHOLD. PER-FILE PATHS DO NOT PRINT HERE.
    VERBOSE (-v) — every file path inline, every URL, every hook line.

Design contract: the Reporter is OPT-IN. installer.install() / remover.remove()
take ``reporter=None`` and emit nothing when it is None, so Forge's install
path (installer/backend/packages.py) and the test-suite keep their current
silent (ok, msg) behavior untouched. The CLI builds a Reporter from argv and
threads it in.
"""

import os
import sys

# Verbosity levels.
QUIET = 0
NORMAL = 1
VERBOSE = 2

# The one number for how much a file set prints, imported from pkm.txn so it
# is STATED IN ONE PLACE. At or below it: the bare count and nothing else.
# Above it: the count plus the per-directory rollup.
#
# Decided 2026-08-05, superseding the 2026-06-14 arrangement: PER-FILE PATHS NO
# LONGER PRINT AT DEFAULT VERBOSITY AT ANY COUNT. The old rule listed every
# path inline for sets up to 50, which is how a routine multi-package
# transaction became a screen-filling wall of paths that buried the lines a
# user actually reads — what was installed, and whether it verified. -v
# restores every path, unchanged.
#
# FILE_LIST_INLINE_CAP is kept as a name because callers outside this module
# import it; it is now the same single number rather than a second one.
from .txn import DEPLOY_PATH_THRESHOLD

FILE_LIST_INLINE_CAP = DEPLOY_PATH_THRESHOLD

# Cap the per-directory rollup itself so a package spanning hundreds of
# directories doesn't reintroduce the wall it was meant to avoid.
DIR_BREAKDOWN_CAP = 20

# Aligned label column for phase lines ("Get:", "Verify:", "Deploy:", ...).
# Widest label is "Signature:" (10 chars); +1 guarantees a gutter for every one.
_LABEL_WIDTH = 11

# Wrap free-text (prose) output — error/warn/info/note/done — at this many
# columns, capped by the actual terminal width. A long message is wrapped HERE,
# with a hanging indent, so its continuation lines align under the message
# instead of letting the terminal soft-wrap them to column 0 (which breaks the
# 2-space indent every other line uses). Columnar output (step / file lists) is
# NOT wrapped — it is short and alignment-sensitive.
WRAP_WIDTH = 100

# Severity color — minimal and TTY-aware. pkm has historically been colorless;
# the only color we add is a bold red/yellow on the error:/warning: severity
# prefix, so the two stand out on a terminal exactly like the build pipeline and
# Forge. Auto-off when the target stream is not a terminal or NO_COLOR is set
# (https://no-color.org/), so piped/captured output and the test suite stay
# plain text. The glyph lead (✗ / ⚠) carries the standout when color is off.
_C_RESET = "\033[0m"
_C_BOLD = "\033[1m"
_C_YELLOW = "\033[33m"
_C_RED = "\033[31m"


def _supports_color(stream):
    """True iff ANSI color should be emitted on ``stream`` (a TTY, NO_COLOR unset)."""
    if os.environ.get("NO_COLOR") is not None:
        return False
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def _paint_prefix(wrapped, prefix, color, stream):
    """Colorize the FIRST occurrence of ``prefix`` in already-wrapped text.

    Color is applied AFTER wrapping so the ANSI bytes never count toward the
    wrap column (the prose still wraps at WRAP_WIDTH); no-op when ``stream``
    does not support color.
    """
    if not _supports_color(stream):
        return wrapped
    return wrapped.replace(prefix, f"{_C_BOLD}{color}{prefix}{_C_RESET}", 1)


def _wrap_prose(text, base_indent="  "):
    """Wrap each logical line of a prose message with a hanging indent.

    Splits on embedded newlines, preserves each line's own leading indent on
    top of base_indent, wraps to min(terminal, WRAP_WIDTH), and indents
    continuation lines two spaces past their line's indent. Long unbreakable
    tokens (paths/URLs) are kept whole rather than split mid-token.
    """
    import shutil
    import textwrap
    width = max(40, min(shutil.get_terminal_size((80, 24)).columns, WRAP_WIDTH))
    out = []
    for raw in str(text).split("\n"):
        stripped = raw.strip()
        if not stripped:
            out.append("")
            continue
        embedded = raw[: len(raw) - len(raw.lstrip())]
        ind = base_indent + embedded
        out.extend(
            textwrap.wrap(
                stripped,
                width=width,
                initial_indent=ind,
                subsequent_indent=ind + "  ",
                break_long_words=False,
                break_on_hyphens=False,
            )
        )
    return "\n".join(out)


def _fmt_count(n):
    """Thousands-separated count, e.g. 1284 -> '1,284'."""
    return f"{n:,}"


def format_generated(ts):
    """Render an index 'generated' ISO timestamp as clean UTC.

    InterGenOS-facing output uses UTC, not a local zone: pkm runs on end
    users' machines worldwide and the signed index itself records UTC, so
    UTC is the unambiguous, correct default (the operator-facing CDT
    convention is a chat-surface rule, not an OS-output rule). We just strip
    the sub-second + offset noise: '2026-06-14T12:17:38.870336+00:00' ->
    '2026-06-14 12:17 UTC'. Falls back to the raw string if unparseable.
    """
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(str(ts)).astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return str(ts)


def human_size(num_bytes, precision=None):
    """Human-readable byte size, e.g. 94714 -> '92 KiB'.

    ``precision`` forces a fixed number of decimal places instead of the
    default rule (one decimal below 10, none at or above it). The transaction
    summary and footer pass precision=1 so a 156.7 MiB install does not read
    as "157 MiB": at transaction scale the tenth is the difference between a
    figure a user can check against their free space and one they cannot.
    Every other caller keeps the existing rendering untouched.
    """
    try:
        n = float(num_bytes)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            if unit == "B":
                return f"{int(n)} {unit}"
            if precision is not None:
                return f"{n:.{precision}f} {unit}"
            return f"{n:.0f} {unit}" if n >= 10 else f"{n:.1f} {unit}"
        n /= 1024


def _abs_path(path, root="/"):
    """Normalize a tracked path to a leading-slash absolute target path.

    DB / manifest file entries are POSIX-relative (e.g. 'usr/bin/code');
    a trailing '/' marks a directory. The user wants to see the real
    on-disk target, so we present a leading slash. ``root`` is shown only
    when it is a non-'/' install prefix (Forge target / chroot), so the
    path the user reads matches where the bytes actually landed.
    """
    rel = path.lstrip("/").rstrip("/")
    if root and str(root) not in ("/", ""):
        base = str(root).rstrip("/")
        return f"{base}/{rel}"
    return "/" + rel


class Reporter:
    """User-facing emission for pkm mutating commands.

    All output goes to ``stream`` (stdout by default). Errors/warnings are
    routed to stderr regardless of level so they survive a `> /dev/null`.
    """

    def __init__(self, level=NORMAL, stream=None, err_stream=None):
        self.level = level
        # Store the explicit streams (or None). When None, the stream/err_stream
        # properties resolve sys.stdout/sys.stderr LAZILY at emit time — so the
        # long-lived module-level reporter honors a later redirect (the standard
        # redirect_stdout/stderr test pattern, and any in-process stream swap),
        # instead of binding the import-time stdout/stderr once and ignoring it.
        self._stream = stream
        self._err_stream = err_stream
        # Transaction context for the [N/M] completion counter. None until a
        # caller opens a transaction with begin_transaction().
        self._txn_total = None
        self._txn_index = 0
        self._txn_width = 0

    @property
    def stream(self):
        return self._stream if self._stream is not None else sys.stdout

    @stream.setter
    def stream(self, value):
        self._stream = value

    @property
    def err_stream(self):
        return self._err_stream if self._err_stream is not None else sys.stderr

    @err_stream.setter
    def err_stream(self, value):
        self._err_stream = value

    @classmethod
    def from_args(cls, args, stream=None):
        """Build a Reporter from a parsed argparse namespace.

        Honors top-level ``-v/--verbose`` and ``-q/--quiet`` (mutually
        exclusive at the parser layer). Absence of both → NORMAL.
        """
        if getattr(args, "quiet", False):
            level = QUIET
        elif getattr(args, "verbose", False):
            level = VERBOSE
        else:
            level = NORMAL
        return cls(level=level, stream=stream)

    # -- low-level emit -------------------------------------------------

    def _out(self, text=""):
        print(text, file=self.stream)

    def _err(self, text=""):
        print(text, file=self.err_stream)

    # -- phase lines ----------------------------------------------------

    def step(self, label, detail=""):
        """A phase line, e.g. step('Get', url). Suppressed at QUIET."""
        if self.level <= QUIET:
            return
        lab = (label + ":").ljust(_LABEL_WIDTH)
        self._out(f"  {lab}{detail}".rstrip())

    def step_continuation(self, detail):
        """A wrapped continuation of the previous step line.

        Indented to the same column the step's detail starts at, so a wrapped
        package-name list reads as one block instead of restarting at the
        label column. Distinct from step('') — an empty label would print a
        bare colon.
        """
        if self.level <= QUIET:
            return
        self._out(f"  {' ' * _LABEL_WIDTH}{detail}".rstrip())

    def blank(self):
        """One blank line between package blocks. Suppressed at QUIET."""
        if self.level <= QUIET:
            return
        self._out("")

    def begin_transaction(self, total, name_width=0):
        """Declare that the next ``total`` completions belong to one transaction.

        The per-package ``[N/M]`` counter is a property of the TRANSACTION,
        not of any one install, and the code that installs a package has no
        idea how many others are in flight. Rather than widen every
        install/remove signature with index/total/width arguments, the
        Reporter — which is already threaded through every one of those call
        sites — carries the context. A caller that never opens a transaction
        gets un-numbered completion lines exactly as before.
        """
        self._txn_total = total
        self._txn_index = 0
        self._txn_width = name_width

    def end_transaction(self):
        self._txn_total = None
        self._txn_index = 0
        self._txn_width = 0

    def installed(self, name, vr):
        """The completion signal for one package.

        ``Installed  <name> <version>-<release>      [N/M]`` — the same
        11-column label gutter every phase line uses, with no colon, and the
        counter right-aligned past the widest subject in the transaction so
        the counters form a column.

        This line IS the completion signal. There is deliberately no
        "Deploy: COMPLETE" anywhere: two lines saying the same thing is how a
        transaction's real outcome gets lost inside its own narration.

        Shown at every level including QUIET, like the ``done`` line it
        replaces, so a scripted caller still gets one line per package.
        """
        subject = f"{name} {vr}"
        lab = "Installed".ljust(_LABEL_WIDTH)
        total = getattr(self, "_txn_total", None)
        if not total:
            self._out(f"  {lab}{subject}".rstrip())
            return
        self._txn_index = getattr(self, "_txn_index", 0) + 1
        counter = f"[{self._txn_index}/{total}]"
        pad = max(getattr(self, "_txn_width", 0) - len(subject), 0) + 6
        self._out(f"  {lab}{subject}{' ' * pad}{counter}")

    def get(self, url, size_human=None):
        """Network fetch line. -v shows the full URL; NORMAL trims to host+tail."""
        if self.level <= QUIET:
            return
        shown = url if self.level >= VERBOSE else _short_url(url)
        detail = shown if not size_human else f"{shown}   {size_human}"
        self.step("Get", detail)

    def signature(self, ok, fingerprint=None, key_label=None):
        """The security headline: was the index/artifact signature verified,
        and by which pinned release key. Shown at NORMAL+ so the most
        important fact is never hidden behind '[OK]'.
        """
        if self.level <= QUIET:
            return
        if ok:
            who = key_label or (_short_fp(fingerprint) if fingerprint else "pinned release key")
            self.step("Signature", f"Good — {who} ✓")
        else:
            self.step("Signature", "VERIFICATION FAILED ✗")

    def verify(self, detail):
        """sha256 / integrity line."""
        self.step("Verify", detail)

    def info(self, text):
        if self.level <= QUIET:
            return
        self._out(_wrap_prose(text))

    def note(self, text):
        """A persistent hint (e.g. how to see the full file list). Shown at
        NORMAL+ — it is the 'how to enable full visibility' the operator
        required whenever the list is truncated.
        """
        if self.level <= QUIET:
            return
        self._out(_wrap_prose(text))

    def done(self, text):
        """Terminal success line. Shown at every level (incl. QUIET) so a
        scripted caller still gets the one-line outcome.
        """
        self._out(_wrap_prose(text))

    def warn(self, text):
        wrapped = _wrap_prose(f"⚠ warning: {text}")
        self._err(_paint_prefix(wrapped, "⚠ warning:", _C_YELLOW, self.err_stream))

    def error(self, text):
        wrapped = _wrap_prose(f"✗ error: {text}")
        self._err(_paint_prefix(wrapped, "✗ error:", _C_RED, self.err_stream))

    # -- the file list (operator's explicit requirement) ----------------

    def file_list(self, paths, action="Deploy", pkg=None, root="/"):
        """Report the files an operation touched.

        ``paths`` is the set of tracked entries (relative POSIX, '/'-suffixed
        for dirs). Regular files are counted; intermediate directories are
        not, because what a user wants to know is where bytes landed.

        Behaviour by level / count (decided 2026-08-05):

          QUIET                     -> "Deploy: N files".
          NORMAL, n <= threshold    -> "Deploy: N files" AND NOTHING ELSE.
          NORMAL, n >  threshold    -> "Deploy: N files (M directories)"
                                       + the per-directory count rollup.
          VERBOSE                   -> summary + every path inline, sorted.

        PER-FILE PATHS NEVER PRINT AT DEFAULT VERBOSITY. That is the change
        from the previous rule, which listed every path for sets up to 50 and
        turned an ordinary multi-package transaction into a wall of paths.
        The directory rollup is KEPT above the threshold because it is
        descriptive rather than exhaustive — it says where a large package
        put its bytes in a handful of lines.

        NO HINT IS PRINTED HERE. Hints belong once, in the transaction
        footer (see ``transaction_footer``); a per-package "re-run with -v"
        under every block is itself a wall.

        ``action`` labels the operation ("Deploy" / "Remove"). ``pkg`` is
        accepted for call-site compatibility and is no longer used to print a
        per-package hint.
        """
        files = sorted(_abs_path(p, root) for p in paths if not p.endswith("/"))
        n = len(files)

        if self.level >= VERBOSE:
            self.step(action, f"{_fmt_count(n)} file" + ("" if n == 1 else "s"))
            for f in files:
                self._out(f"      {f}")
            return

        if n <= FILE_LIST_INLINE_CAP:
            self.step(action, f"{_fmt_count(n)} file" + ("" if n == 1 else "s"))
            return

        # n > threshold at NORMAL: the count carries the directory span, and
        # the rollup follows.
        dir_counts = {}
        for f in files:
            d = f.rsplit("/", 1)[0] + "/"
            dir_counts[d] = dir_counts.get(d, 0) + 1
        ordered = sorted(dir_counts.items(), key=lambda kv: kv[0])
        ndirs = len(ordered)
        self.step(
            action,
            f"{_fmt_count(n)} files ({_fmt_count(ndirs)} director"
            + ("y" if ndirs == 1 else "ies") + ")",
        )
        if self.level <= QUIET:
            return
        width = max(len(d) for d, _ in ordered[:DIR_BREAKDOWN_CAP])
        for d, c in ordered[:DIR_BREAKDOWN_CAP]:
            self._out(f"      {d.ljust(width)}  {_fmt_count(c)}")
        if ndirs > DIR_BREAKDOWN_CAP:
            self._out(f"      … and {_fmt_count(ndirs - DIR_BREAKDOWN_CAP)} more directories")

    def transaction_footer(self, count=None, installed_bytes=None):
        """The one place hints are printed, at the end of a transaction.

        Emits the aggregate line when a count is supplied, then the single
        pointer to fuller output. Suppressed at VERBOSE, where every path
        already printed and the advice would be stale, and at QUIET.
        """
        if count is not None:
            pkgs = f"{count} package" + ("" if count == 1 else "s")
            line = f"Installed {pkgs}"
            if installed_bytes is not None:
                line += f" · {human_size(installed_bytes, precision=1)}"
            self._out(f"  {line}")
        if self.level <= QUIET or self.level >= VERBOSE:
            return
        self._out("  File paths: re-run with -v · per-package: pkm files <name>")


# ----------------------------------------------------------------------
# Module-level prose emitters (PKM-A27 / PKM-A28).
#
# Only four of pkm's ~22 commands build a Reporter, so the other commands'
# free-text prose went through raw print() — soft-wrapping to column 0 (the
# "wall of text" the operator hit) and silently ignoring -q/-v. These helpers
# give EVERY command the same wrapped-prose + verbosity-honoring path without
# threading a Reporter through every handler signature: a single process-wide
# Reporter whose level the CLI sets once from argv in main(). Columnar output
# (tables, file lists, aligned rows) is NOT routed here — it stays raw print(),
# because it is short, alignment-sensitive, and must not be wrapped.
# ----------------------------------------------------------------------

_process_reporter = Reporter(level=NORMAL)


def set_process_level(level):
    """Set the process-wide prose verbosity (QUIET / NORMAL / VERBOSE).

    Called once by the CLI after argv is parsed so the module-level emitters
    below honor -q/-v for every command, not just the four that build their
    own Reporter.
    """
    _process_reporter.level = level


def process_level():
    """Current process-wide prose verbosity."""
    return _process_reporter.level


def emit_info(text):
    """Wrapped informational prose. Suppressed at QUIET."""
    _process_reporter.info(text)


def emit_note(text):
    """Wrapped persistent hint. Suppressed at QUIET."""
    _process_reporter.note(text)


def emit_done(text):
    """Wrapped terminal/summary line. Shown at every level (incl. QUIET) so a
    scripted caller still gets the one-line outcome."""
    _process_reporter.done(text)


def emit(text="", err=False):
    """Wrapped prose with NO severity prefix, shown at every level.

    The escape hatch for prose that needs its own lead-in (e.g. a 'CRITICAL:'
    notice) or must go to stderr without the WARNING:/ERROR: prefix that
    emit_warn/emit_error add. Routes to stderr when ``err`` is set.
    """
    if err:
        _process_reporter._err(_wrap_prose(text))
    else:
        _process_reporter._out(_wrap_prose(text))


def emit_warn(text):
    """Wrapped warning to stderr (prefixed ⚠ warning:). Shown at every level."""
    _process_reporter.warn(text)


def emit_error(text):
    """Wrapped error to stderr (prefixed ✗ error:). Shown at every level."""
    _process_reporter.error(text)


def _short_url(url):
    """Trim a URL to 'host/.../<tail>' for NORMAL output; -v shows it whole."""
    try:
        from urllib.parse import urlsplit
        s = urlsplit(url)
        tail = s.path.rsplit("/", 1)[-1] or s.path
        return f"{s.netloc}/…/{tail}" if s.path.count("/") > 1 else f"{s.netloc}{s.path}"
    except Exception:
        return url


def _short_fp(fp):
    """Last 8 hex of a 40-char fingerprint (the short key id users recognize)."""
    f = (fp or "").replace(" ", "")
    return f[-8:] if len(f) >= 8 else f
