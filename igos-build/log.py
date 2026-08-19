# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Logging infrastructure for igos-build.

Every build action is logged with timestamps, phase markers, and full
untruncated output. Logs are written to both console and per-package
log files. Nothing is hidden, nothing is summarized.

When json_log=True, a parallel JSONL stream is written alongside the
text log for machine-parseable build analysis.
"""

import json
import os
import shlex
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# House style for the Python builder's console output.
#
# The builder follows prevailing distro build-output convention (a consistent
# section marker plus a single severity scheme), NOT a forced global indent.
# Volume stays detailed — the build detail is kept — only the VOICE
# is cleaned up. Sanctioned markers / severities / verdicts live here so every
# call site renders the same way.
#
# Color is minimal and TTY-aware: it is emitted only when the destination
# stream is a real terminal and NO_COLOR is unset.
# ---------------------------------------------------------------------------

# One section marker for phase / step lines (Arch makepkg "==>" lineage).
_SECTION = "==>"
_SUBSTEP = "  ->"

# ANSI SGR codes, applied only when the target stream supports color.
_C_RESET = "\033[0m"
_C_BOLD = "\033[1m"
_C_BLUE = "\033[34m"
_C_GREEN = "\033[32m"
_C_YELLOW = "\033[33m"
_C_RED = "\033[31m"


def _color_enabled(stream) -> bool:
    """True when colored output is appropriate for ``stream``.

    Off when NO_COLOR is set, when the stream is not a TTY, or when isatty()
    cannot be determined — the standard, conservative auto-detection.
    """
    if os.environ.get("NO_COLOR") is not None:
        return False
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def _paint(text: str, code: str, stream) -> str:
    """Wrap ``text`` in an SGR ``code`` when ``stream`` supports color."""
    if not code or not _color_enabled(stream):
        return text
    return f"{code}{text}{_C_RESET}"

# Import the shared forensic-trace module via the per-package loader shim.
# When IGOS_BUILD_DEBUG_VERBOSE is unset, every _trace call short-circuits
# at its gate. Both BuildLogger and SummaryLogger remain backward-compatible
# with the legacy text + JSONL outputs; the structured-trace stream is layered
# additively per the lift's "additive, not replacement" posture.
try:
    from . import _trace
    _TRACE_AVAILABLE = True
except ImportError:
    # Shim not importable (unusual — packaging error). Fall back to no-op so
    # the legacy text-logging path keeps working unchanged.
    _trace = None
    _TRACE_AVAILABLE = False


class BuildLogger:
    """Logs build output to console and per-package log files.

    Each package gets its own log file at:
        {log_dir}/{package_name}-{timestamp}.log

    The log captures everything: commands run, stdout, stderr, exit codes,
    timing, and phase boundaries. Full output, never truncated.
    """

    def __init__(self, log_dir: Path, json_log: bool = False):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.json_log = json_log
        self._file = None
        self._json_file = None
        self._log_path = None
        self._pkg_name = None
        self._phase_start = None
        self._build_start = None

    def __del__(self):
        """Ensure log files are closed on garbage collection."""
        for f in (self._file, self._json_file):
            if f:
                try:
                    f.close()
                except Exception:
                    pass
        self._file = None
        self._json_file = None

    def start_package(self, name: str, version: str, style: str):
        """Open a log file for a new package build."""
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        log_path = self.log_dir / f"{name}-{timestamp}.log"
        self._file = open(log_path, "w")
        self._log_path = log_path
        self._pkg_name = name
        self._build_start = time.monotonic()

        # Open the per-package structured-trace sink under the canonical path
        # /mnt/intergenos/build/logs/trace/build-pkg-<name>-<startts>-<runid>.jsonl.
        # When IGOS_BUILD_DEBUG_VERBOSE is unset this short-circuits to no-op.
        if _TRACE_AVAILABLE:
            try:
                _trace.init_package_trace(name)
                _trace.trace_event(
                    "pkg_enter",
                    pkg=name,
                    version=version,
                    style=style,
                    tier="igos-build",
                )
            except Exception:
                # Best-effort: sink-open failures must not break the build.
                pass

        if self.json_log:
            json_path = self.log_dir / f"{name}-{timestamp}.jsonl"
            self._json_file = open(json_path, "w")
            self._json_event("package_start", package=name, version=version, style=style)

        started = datetime.now(timezone.utc).isoformat()
        # Plain text for the log file; colored heading for the console.
        text_header = (
            f"{_SECTION} Building {name} {version}\n"
            f"    Style:   {style}\n"
            f"    Started: {started}\n"
            f"    Log:     {log_path}\n"
        )
        self._write(text_header)
        console_header = (
            f"{_paint(_SECTION, _C_BOLD + _C_BLUE, sys.stdout)} "
            f"{_paint(f'Building {name} {version}', _C_BOLD, sys.stdout)}\n"
            f"    Style:   {style}\n"
            f"    Started: {started}\n"
            f"    Log:     {log_path}\n"
        )
        self._console(console_header)

    def end_package(self, success: bool):
        """Close the log file for the current package."""
        elapsed = time.monotonic() - self._build_start

        # Structured pkg_exit event matches the bash side's trace_pkg_exit so
        # cross-side jq joins on `pkg` work uniformly.
        if _TRACE_AVAILABLE:
            try:
                _trace.trace_event(
                    "pkg_exit",
                    pkg=self._pkg_name,
                    rc=0 if success else 1,
                    duration_ms=int(elapsed * 1000),
                )
            except Exception:
                pass

        self._json_event("package_end", package=self._pkg_name,
                         success=success, elapsed_s=round(elapsed, 1))

        mark = "✓" if success else "✗"
        verdict = "built" if success else "failed"
        text_footer = f"{_SECTION} {mark} {self._pkg_name} {verdict} in {elapsed:.1f}s\n\n"
        self._write(text_footer)
        color = _C_GREEN if success else _C_RED
        console_footer = (
            f"{_paint(_SECTION, _C_BOLD + color, sys.stdout)} "
            f"{_paint(mark, color, sys.stdout)} "
            f"{self._pkg_name} {verdict} in {elapsed:.1f}s\n\n"
        )
        self._console(console_footer)

        if self._file:
            self._file.close()
            self._file = None
        if self._json_file:
            self._json_file.close()
            self._json_file = None

    def start_phase(self, phase_name: str):
        """Log the start of a build phase."""
        self._phase_start = time.monotonic()
        if _TRACE_AVAILABLE:
            try:
                _trace.trace_event("pkg_phase_start",
                                   pkg=self._pkg_name, phase=phase_name)
            except Exception:
                pass
        self._json_event("phase_start", package=self._pkg_name, phase=phase_name)
        label = phase_name.replace("_", " ")
        self._write(f"\n{_SUBSTEP} {label}\n")
        self._console(f"\n{_paint(_SUBSTEP, _C_BLUE, sys.stdout)} {label}\n")

    def end_phase(self, phase_name: str, exit_code: int):
        """Log the end of a build phase with its exit code."""
        elapsed = time.monotonic() - self._phase_start
        # Schema-aligned pkg_phase event matches bash trace_pkg_phase.
        if _TRACE_AVAILABLE:
            try:
                _trace.trace_event(
                    "pkg_phase",
                    pkg=self._pkg_name,
                    phase=phase_name,
                    rc=exit_code,
                    duration_ms=int(elapsed * 1000),
                )
            except Exception:
                pass
        self._json_event("phase_end", package=self._pkg_name, phase=phase_name,
                         exit_code=exit_code, elapsed_s=round(elapsed, 1))
        label = phase_name.replace("_", " ")
        ok = exit_code == 0
        mark = "✓" if ok else "✗"
        outcome = f"done ({elapsed:.1f}s)" if ok else f"failed, exit {exit_code} ({elapsed:.1f}s)"
        self._write(f"{_SUBSTEP} {mark} {label} {outcome}\n")
        color = _C_GREEN if ok else _C_RED
        self._console(
            f"{_paint(_SUBSTEP, color, sys.stdout)} "
            f"{_paint(mark, color, sys.stdout)} {label} {outcome}\n"
        )

    def command(self, cmd: "str | list[str]"):
        """Log a command about to be executed.

        Accepts both str (shell=True) and list[str] (shell=False) to match
        builder.py:run_command which passes either form per the B10
        shell-injection-hardening migration. The structured trace_event
        emits cmd as a JSON array (schema canonical form); the text +
        json_event outputs use a human-readable display string.
        """
        if isinstance(cmd, str):
            argv = [cmd]
            cmd_display = cmd
        else:
            argv = list(cmd)
            cmd_display = shlex.join(cmd)
        if _TRACE_AVAILABLE:
            try:
                _trace.trace_event("subprocess_start",
                                   pkg=self._pkg_name, cmd=argv,
                                   phase="pkg_command")
            except Exception:
                pass
        self._json_event("command", package=self._pkg_name, cmd=cmd_display)
        line = f"\n  $ {cmd_display}\n"
        self._write(line)
        self._console(line)

    def output(self, text: str):
        """Log command output (stdout or stderr). Never truncated.

        Forensic mode (verbose): also emits a `subprocess_output` event with
        the raw text + byte length so the JSONL trail captures the byte-level
        narration the operator needs for triage. This retires the previous
        "log has the text but JSON has the gap" mismatch — when verbose is on,
        every byte of subprocess output is captured in BOTH the text log AND
        the structured JSONL stream.
        """
        if text:
            if _TRACE_AVAILABLE:
                try:
                    _trace.trace_event(
                        "subprocess_output",
                        pkg=self._pkg_name,
                        text=text,
                        bytes=len(text.encode("utf-8")),
                    )
                except Exception:
                    pass
            self._write(text)
            self._console_output(text)

    def error(self, message: str):
        """Log an error message."""
        if _TRACE_AVAILABLE:
            try:
                _trace.trace_event("error",
                                   pkg=self._pkg_name, message=message)
            except Exception:
                pass
        self._json_event("error", package=self._pkg_name, message=message)
        self._write(f"\nerror: {message}\n")
        self._console_error(
            f"\n{_paint('✗ error:', _C_BOLD + _C_RED, sys.stderr)} {message}\n"
        )

    def info(self, message: str):
        """Log an informational message."""
        if _TRACE_AVAILABLE:
            try:
                _trace.trace_event("pkg_info",
                                   pkg=self._pkg_name, message=message)
            except Exception:
                pass
        line = f"  {message}\n"
        self._write(line)
        self._console(line)

    def warning(self, message: str):
        """Log a warning message (non-fatal — execution continues)."""
        if _TRACE_AVAILABLE:
            try:
                _trace.trace_event("warning",
                                   pkg=self._pkg_name, message=message)
            except Exception:
                pass
        self._json_event("warning", package=self._pkg_name, message=message)
        self._write(f"warning: {message}\n")
        self._console(
            f"{_paint('⚠ warning:', _C_BOLD + _C_YELLOW, sys.stdout)} {message}\n"
        )

    def _json_event(self, event_type: str, **data):
        """Write a JSON event to the JSONL log file."""
        if not self._json_file:
            return
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            **data,
        }
        self._json_file.write(json.dumps(event) + "\n")
        self._json_file.flush()

    def _write(self, text: str):
        """Write to the log file."""
        if self._file:
            self._file.write(text)
            self._file.flush()

    def _console(self, text: str):
        """Write to stdout."""
        sys.stdout.write(text)
        sys.stdout.flush()

    def _console_output(self, text: str):
        """Write command output to stdout."""
        sys.stdout.write(text)
        sys.stdout.flush()

    def _console_error(self, text: str):
        """Write error to stderr."""
        sys.stderr.write(text)
        sys.stderr.flush()

    def tail(self, n: int = 40) -> str:
        """Last n lines of the current/last package log file (closed or open).

        Empty string if no package log was opened or it is unreadable.
        """
        if not self._log_path:
            return ""
        try:
            lines = self._log_path.read_text(errors="replace").splitlines()
        except OSError:
            return ""
        return "\n".join(lines[-n:])

    def echo_failure_tail(self, name: str, version: str, n: int = 40) -> None:
        """Re-surface the failed package's last n log lines to stderr AT the halt.

        The per-package output already streamed live and is on disk, but on a halt
        the operator is looking at the END of a long run where the actual error can
        be scrolled away — or, on a resumed/targeted build, the per-package log
        lives only in the chroot view. Re-printing the tail right at the halt point,
        co-located with the halt message and on stderr, makes the failure
        loud-at-halt. Additive + failure-only; NOT a stderr-drop fix — every layer
        of the pipeline already surfaces stderr (verified 2026-06-29).
        """
        tail = self.tail(n)
        if not tail:
            return
        bar = "-" * 60
        self._console_error(
            f"\n{bar}\n  last {n} lines of {name} {version}  ({self._log_path})\n"
            f"{bar}\n{tail}\n{bar}\n"
        )


class SummaryLogger:
    """Tracks and reports the overall build summary."""

    def __init__(self, log_dir: Path | None = None, json_log: bool = False):
        self._results: list[tuple[str, str, bool, float]] = []
        self._start = time.monotonic()
        self._log_dir = Path(log_dir) if log_dir else None
        self._json_log = json_log

    def record(self, name: str, version: str, success: bool, elapsed: float, skipped: bool = False):
        """Record the result of one package build."""
        self._results.append((name, version, success, elapsed, skipped))

    def print_summary(self):
        """Print the final build summary."""
        total_time = time.monotonic() - self._start
        built = [r for r in self._results if not r[4]]  # not skipped
        skipped = [r for r in self._results if r[4]]
        succeeded = [r for r in built if r[2]]
        failed = [r for r in built if not r[2]]

        head = f"{_SECTION} Build summary"
        print(f"\n{_paint(head, _C_BOLD + _C_BLUE, sys.stdout)}")
        print(f"    Total packages: {len(self._results)}")
        print(f"    Built:          {len(built)}")
        print(f"    Succeeded:      {len(succeeded)}")
        print(f"    Failed:         {len(failed)}")
        if skipped:
            print(f"    Skipped:        {len(skipped)}")
        print(f"    Total time:     {total_time:.1f}s\n")

        if failed:
            print(f"{_SUBSTEP} Failures")
            for name, version, _, elapsed, _ in failed:
                mark = _paint("✗", _C_RED, sys.stdout)
                print(f"    {mark} {name} {version} ({elapsed:.1f}s)")
            print()

        print(f"{_SUBSTEP} Results")
        for name, version, success, elapsed, was_skipped in self._results:
            if was_skipped:
                mark = _paint("·", _C_YELLOW, sys.stdout)
                print(f"    {mark} {name} {version} (skipped)")
            elif success:
                mark = _paint("✓", _C_GREEN, sys.stdout)
                print(f"    {mark} {name} {version} ({elapsed:.1f}s)")
            else:
                mark = _paint("✗", _C_RED, sys.stdout)
                print(f"    {mark} {name} {version} ({elapsed:.1f}s)")
        print()

        # Write JSON summary if enabled
        if self._json_log and self._log_dir:
            self._write_json_summary(total_time, built, skipped, succeeded, failed)

    def _write_json_summary(self, total_time, built, skipped, succeeded, failed):
        """Write a JSON build summary file.

        Path naming follows the canonical trace-file convention when verbose
        mode is on (so cross-file jq joins by runid work). When verbose is
        off, falls back to the legacy `build-summary-<ts>.json` naming.
        """
        if _TRACE_AVAILABLE and _trace.is_verbose():
            startts = _trace.get_start_ts() or datetime.now().strftime("%Y%m%dT%H%M%SZ")
            runid = _trace.get_runid() or "norunid"
            summary_path = self._log_dir / f"build-summary-{startts}-{runid}.json"
        else:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            summary_path = self._log_dir / f"build-summary-{timestamp}.json"

        summary = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "total_time_s": round(total_time, 1),
            "counts": {
                "total": len(self._results),
                "built": len(built),
                "succeeded": len(succeeded),
                "failed": len(failed),
                "skipped": len(skipped),
            },
            "failures": [
                {"name": n, "version": v, "elapsed_s": round(e, 1)}
                for n, v, _, e, _ in failed
            ],
            "packages": [
                {
                    "name": n,
                    "version": v,
                    "success": s,
                    "elapsed_s": round(e, 1),
                    "skipped": sk,
                }
                for n, v, s, e, sk in self._results
            ],
        }

        with open(summary_path, "w") as f:
            json.dump(summary, f, indent=2)
            f.write("\n")

        # Also emit a `build_summary` event into the trace trail so any
        # cross-file query can pivot from the summary path to the per-phase
        # / per-package trails by shared runid.
        if _TRACE_AVAILABLE:
            try:
                _trace.trace_event(
                    "build_summary",
                    summary_path=str(summary_path),
                    total_packages=len(self._results),
                    succeeded=len(succeeded),
                    failed=len(failed),
                    skipped=len(skipped),
                    total_time_s=round(total_time, 1),
                )
            except Exception:
                pass
