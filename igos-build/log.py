"""Logging infrastructure for igos-build.

Every build action is logged with timestamps, phase markers, and full
untruncated output. Logs are written to both console and per-package
log files. Nothing is hidden, nothing is summarized.
"""

import sys
import time
from datetime import datetime, timezone
from pathlib import Path


class BuildLogger:
    """Logs build output to console and per-package log files.

    Each package gets its own log file at:
        {log_dir}/{package_name}-{timestamp}.log

    The log captures everything: commands run, stdout, stderr, exit codes,
    timing, and phase boundaries. Full output, never truncated.
    """

    def __init__(self, log_dir: Path):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._file = None
        self._pkg_name = None
        self._phase_start = None
        self._build_start = None

    def start_package(self, name: str, version: str, style: str):
        """Open a log file for a new package build."""
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        log_path = self.log_dir / f"{name}-{timestamp}.log"
        self._file = open(log_path, "w")
        self._pkg_name = name
        self._build_start = time.monotonic()

        header = (
            f"{'=' * 72}\n"
            f"  PACKAGE: {name} {version}\n"
            f"  STYLE:   {style}\n"
            f"  STARTED: {datetime.now(timezone.utc).isoformat()}\n"
            f"  LOG:     {log_path}\n"
            f"{'=' * 72}\n"
        )
        self._write(header)
        self._console(header)

    def end_package(self, success: bool):
        """Close the log file for the current package."""
        elapsed = time.monotonic() - self._build_start
        status = "SUCCESS" if success else "FAILED"

        footer = (
            f"\n{'=' * 72}\n"
            f"  {self._pkg_name}: {status} in {elapsed:.1f}s\n"
            f"{'=' * 72}\n\n"
        )
        self._write(footer)
        self._console(footer)

        if self._file:
            self._file.close()
            self._file = None

    def start_phase(self, phase_name: str):
        """Log the start of a build phase."""
        self._phase_start = time.monotonic()
        marker = f"\n--- [{phase_name.upper()}] {self._pkg_name} ---\n"
        self._write(marker)
        self._console(marker)

    def end_phase(self, phase_name: str, exit_code: int):
        """Log the end of a build phase with its exit code."""
        elapsed = time.monotonic() - self._phase_start
        status = "OK" if exit_code == 0 else f"FAILED (exit {exit_code})"
        marker = f"--- [{phase_name.upper()}] {status} ({elapsed:.1f}s) ---\n"
        self._write(marker)
        self._console(marker)

    def command(self, cmd: str):
        """Log a command about to be executed."""
        line = f"\n  $ {cmd}\n"
        self._write(line)
        self._console(line)

    def output(self, text: str):
        """Log command output (stdout or stderr). Never truncated."""
        if text:
            self._write(text)
            self._console_output(text)

    def error(self, message: str):
        """Log an error message."""
        line = f"\n  ERROR: {message}\n"
        self._write(line)
        self._console_error(line)

    def info(self, message: str):
        """Log an informational message."""
        line = f"  {message}\n"
        self._write(line)
        self._console(line)

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


class SummaryLogger:
    """Tracks and reports the overall build summary."""

    def __init__(self):
        self._results: list[tuple[str, str, bool, float]] = []
        self._start = time.monotonic()

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

        print(f"\n{'=' * 72}")
        print(f"  BUILD SUMMARY")
        print(f"{'=' * 72}\n")
        print(f"  Total packages: {len(self._results)}")
        print(f"  Built:          {len(built)}")
        print(f"  Succeeded:      {len(succeeded)}")
        print(f"  Failed:         {len(failed)}")
        if skipped:
            print(f"  Skipped:        {len(skipped)}")
        print(f"  Total time:     {total_time:.1f}s\n")

        if failed:
            print("  FAILURES:")
            for name, version, _, elapsed, _ in failed:
                print(f"    - {name} {version} ({elapsed:.1f}s)")
            print()

        print("  COMPLETED:")
        for name, version, success, elapsed, was_skipped in self._results:
            if was_skipped:
                print(f"    [SKIP] {name} {version}")
            else:
                status = "OK" if success else "FAIL"
                print(f"    [{status:4s}] {name} {version} ({elapsed:.1f}s)")

        print(f"\n{'=' * 72}\n")
