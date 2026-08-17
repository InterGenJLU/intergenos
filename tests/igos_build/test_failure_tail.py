"""igos-build BuildLogger: failed-package tail is re-surfaced at the halt.

The per-package output streams live and is on disk, but on a build halt the
operator is looking at the END of a long run where the actual error can be
scrolled away — or, on a resumed/targeted build, the per-package log lives only
in the chroot view. BuildLogger.echo_failure_tail re-prints the failed package's
last lines to stderr AT the halt point, co-located with the halt message.

This is an observability co-location improvement, NOT a stderr-drop fix: every
layer of the build pipeline already surfaces stderr (verified 2026-06-29).
"""

import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
# igos-build has a hyphen (not an importable package name) and log.py's only
# non-stdlib import (`from . import _trace`) is guarded, so load it standalone.
_spec = importlib.util.spec_from_file_location(
    "igos_build_log", REPO_ROOT / "igos-build" / "log.py")
_log = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_log)
BuildLogger = _log.BuildLogger


class FailureTailTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.logger = BuildLogger(Path(self._td.name))

    def tearDown(self):
        self._td.cleanup()

    def test_tail_and_echo_surface_failed_pkg_output(self):
        # Drive a package whose output ends in an error, the way a real failing
        # build does (output streams to the per-package log file).
        with redirect_stdout(io.StringIO()):  # silence the live console mirror
            self.logger.start_package("foo", "1.0", "make")
            self.logger.output("configure: ok\n")
            self.logger.output("error: boom — undefined reference to bar\n")
            self.logger.end_package(False)
        # tail() reads the now-closed per-package log
        self.assertIn("error: boom", self.logger.tail())
        # echo re-surfaces it to stderr, co-located and naming the package
        buf = io.StringIO()
        with redirect_stderr(buf):
            self.logger.echo_failure_tail("foo", "1.0")
        out = buf.getvalue()
        self.assertIn("error: boom", out)
        self.assertIn("foo 1.0", out)

    def test_echo_is_silent_when_no_package_log(self):
        # No start_package -> no log path -> echo is a no-op, never raises.
        buf = io.StringIO()
        with redirect_stderr(buf):
            self.logger.echo_failure_tail("x", "1")
        self.assertEqual(buf.getvalue(), "")

    def test_tail_bounds_lines(self):
        with redirect_stdout(io.StringIO()):
            self.logger.start_package("bar", "2.0", "make")
            for i in range(100):
                self.logger.output(f"line {i}\n")
            self.logger.end_package(False)
        tail = self.logger.tail(n=10)
        self.assertIn("line 99", tail)        # the last body line is present
        self.assertNotIn("line 89", tail)     # bounded — an early line is gone
        self.assertLessEqual(len(tail.splitlines()), 10)  # at most n lines


if __name__ == "__main__":
    unittest.main()
