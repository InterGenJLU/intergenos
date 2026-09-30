# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A short version is reported on every path this machine actually uses.

The engine already records what a capture could not read, and the `chronicle`
command prints it. That covers the person who runs a capture by hand. It does
not cover the two ways this machine captures on its own, nor the window a person
looks at, nor a script reading `--json`:

  * `chronicle capture --json` returned from its JSON branch before the check
    that sets the status, so a script was told a short version was a complete
    one — while the man page this lane added says the command exits non-zero so
    a script is told, and release 23 set exactly that rule for restore.
  * `chronicled --task capture-userdata` is what the hourly timer runs. It
    printed only the version id and exited 0 for a version that was short of
    its source; nothing in the unit, its journal or its status said so.
    `--task capture-config` printed its result and exited 0 the same way.
  * `--task drain-queue` and the configuration watcher inside the service threw
    the result away entirely, so a capture made in the off-peak window or on a
    configuration change could be short with no trace anywhere.
  * The window's timeline row showed the file count and no omission count, so
    the one surface a person looks at read a short version as a complete one.

Each case below is red at this lane's parent and green after. The controls are
the clean runs beside them: a capture that read everything must keep exit 0, an
empty record and a row that says nothing about omissions, or the reporting would
be noise a person learns to ignore.
"""

import importlib.util
import io
import json
import logging
import os
import sys
import unittest
from contextlib import redirect_stdout, redirect_stderr
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock

import pytest

from chronicle import cli as _cli
from chronicle import config as _config
from chronicle import configstate as _configstate
from chronicle import engine as _engine
from chronicle import gui as _gui
from chronicle import paths as _paths
from chronicle import sentinel as _sentinel

pytestmark = pytest.mark.skipif(
    os.geteuid() == 0,
    reason="the locked directory below is readable by root, so the case cannot "
           "be posed as this user")


def _load_chronicled():
    """The service script, loaded in process — the idiom this directory already
    uses for it (test_chronicled_config_watch.py), so a case can read its exit
    status and its journal lines without a subprocess."""
    assets = Path(__file__).resolve().parents[2] / "assets" / "intergenos-backup"
    loader = SourceFileLoader("chronicled_under_test", str(assets / "chronicled"))
    spec = importlib.util.spec_from_loader("chronicled_under_test", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# A script reading --json
# --------------------------------------------------------------------------


def _capture_json(result):
    """Run `chronicle capture --json` against a backend that returns `result`."""
    out, err = io.StringIO(), io.StringIO()
    backend = mock.Mock()
    backend.call.return_value = result
    with mock.patch.object(_cli, "Backend", return_value=backend), \
            redirect_stdout(out), redirect_stderr(err):
        rc = _cli.main(["capture", "user-data", "--json"])
    return rc, out.getvalue(), err.getvalue()


class ScriptReadingJson(unittest.TestCase):

    def test_a_short_version_exits_nonzero(self):
        rc, out, _err = _capture_json({
            "version_id": "0000000004-6b2dc07a701c",
            "files": 2561,
            "unreadable": [{"path": "/home/p/Documents/folder 07",
                            "error": "PermissionError: [Errno 13]"}],
        })
        self.assertEqual(rc, 1, (
            "a script asked for JSON and was told by the exit status that a "
            f"version short of its source was a complete capture; output {out!r}"
        ))

    def test_the_short_version_is_still_in_the_payload(self):
        """The status is added to the payload, never in place of it."""
        _rc, out, _err = _capture_json({
            "version_id": "0000000004-6b2dc07a701c",
            "files": 2561,
            "unreadable": [{"path": "/home/p/Documents/folder 07",
                            "error": "PermissionError: [Errno 13]"}],
        })
        payload = json.loads(out)
        self.assertEqual(len(payload["unreadable"]), 1)
        self.assertEqual(payload["unreadable"][0]["path"],
                         "/home/p/Documents/folder 07")

    def test_a_complete_capture_still_exits_zero(self):
        """CONTROL. A status that fired on every capture would say nothing."""
        rc, _out, _err = _capture_json({
            "version_id": "0000000005-b15634aa7439", "files": 2761,
            "unreadable": [],
        })
        self.assertEqual(rc, 0)

    def test_a_queued_capture_still_exits_zero(self):
        """CONTROL. An asynchronous capture has committed nothing to be short."""
        rc, _out, _err = _capture_json({"queued": "user-data"})
        self.assertEqual(rc, 0)


# --------------------------------------------------------------------------
# The captures this machine makes on its own
# --------------------------------------------------------------------------


class _ScheduledTaskCase(unittest.TestCase):
    """A real engine over a real store, with one directory the walk cannot read."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory(prefix="chronicle-short-version-")
        root = Path(self.tmp.name)
        self.source = root / "home"
        (self.source / "readable").mkdir(parents=True)
        for i in range(5):
            (self.source / "readable" / f"n{i}.txt").write_bytes(b"x\n")
        self.locked = self.source / "locked"
        self.locked.mkdir()
        (self.locked / "hidden.txt").write_bytes(b"y\n")
        self.store = root / "store"
        self.conf = root / "chronicle.conf"
        self.conf.write_text(
            "[chronicle]\n"
            f"user_data_paths = {self.source}\n"
        )
        # The config-state set is a module constant, not a config key, and it
        # names this machine's real /etc. Point it at the same throwaway tree
        # for the duration of the case, so the scheduled configuration capture
        # under test walks something this user can lock and nothing of the
        # machine's own.
        self._config_set = mock.patch.object(
            _configstate, "DEFAULT_CONFIG_PATHS", (str(self.source),))
        self._config_set.start()
        self.addCleanup(self._config_set.stop)
        cfg = _config.load(str(self.conf))
        eng = _engine.Engine(local_root=self.store, config=cfg)
        eng.target_adopt(str(root / "target"), target_class="directory",
                         cap_bytes=1 << 30)
        self.chronicled = _load_chronicled()

    def tearDown(self):
        if self.locked.exists():
            os.chmod(self.locked, 0o755)
        self.tmp.cleanup()

    def run_task(self, task):
        """Run one scheduled task and return (exit status, stdout, journal)."""
        out, err = io.StringIO(), io.StringIO()
        handler = logging.StreamHandler(err)
        logging.getLogger("chronicle").addHandler(handler)
        previous = logging.getLogger("chronicle").level
        logging.getLogger("chronicle").setLevel(logging.WARNING)
        try:
            with redirect_stdout(out), redirect_stderr(err):
                rc = self.chronicled.main([
                    "--local-root", str(self.store), "--config", str(self.conf),
                    "--task", task])
        finally:
            logging.getLogger("chronicle").removeHandler(handler)
            logging.getLogger("chronicle").setLevel(previous)
        return rc, out.getvalue(), err.getvalue()

    def lock(self):
        os.chmod(self.locked, 0o000)


class TheHourlyUserDataTask(_ScheduledTaskCase):
    """chronicle-userdata.timer runs this every hour; it is how this machine
    backs a person's home directory up when nobody is watching."""

    def test_a_short_version_exits_nonzero_and_names_the_path(self):
        self.lock()
        rc, out, journal = self.run_task("capture-userdata")
        self.assertEqual(rc, 1, (
            "the hourly capture committed a version short of its source and "
            f"exited 0, so the unit is green in systemctl; it printed {out!r}"))
        self.assertIn(str(self.locked), out + journal, (
            "nothing in the task's output or its journal names the path that "
            "could not be read, so the omission is only in the store"))

    def test_a_complete_capture_still_exits_zero_and_says_nothing(self):
        """CONTROL."""
        rc, out, journal = self.run_task("capture-userdata")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("could not be read", out + journal)


class TheScheduledConfigTask(_ScheduledTaskCase):

    def test_a_short_version_exits_nonzero_and_names_the_path(self):
        self.lock()
        rc, out, journal = self.run_task("capture-config")
        self.assertEqual(rc, 1, (
            "the scheduled configuration capture was short of its source and "
            f"exited 0; it printed {out!r}"))
        self.assertIn(str(self.locked), out + journal)

    def test_a_complete_capture_still_exits_zero(self):
        """CONTROL."""
        rc, out, _journal = self.run_task("capture-config")
        self.assertEqual(rc, 0, out)


class TheOffPeakDrain(_ScheduledTaskCase):
    """The queue drain does not decide anything a person sees, so it does not
    change the task's status — but a capture it makes must not vanish."""

    def test_the_record_survives_the_drain_and_reaches_the_journal(self):
        cfg = _config.load(str(self.conf))
        eng = _engine.Engine(local_root=self.store, config=cfg)
        eng.capture(_paths.LAYER_USER_DATA, reason="queued for off-peak",
                    sync=False, estimate=0)
        self.lock()
        drained, _remaining, unreadable = _sentinel.drain_offpeak(
            eng, now_min=_sentinel.minutes_of_day(
                lambda: 0))  # midnight is inside the off-peak window
        self.assertEqual(drained, 1)
        self.assertEqual([u["path"] for u in unreadable], [str(self.locked)], (
            "the drain captured a version short of its source and handed back "
            "nothing about it, so an off-peak capture can be short with no "
            "trace anywhere"))


class TheConfigurationWatcher(unittest.TestCase):
    """The watcher inside the running service captures on a configuration
    change; its result was discarded entirely."""

    def test_it_reports_what_the_capture_could_not_read(self):
        chronicled = _load_chronicled()
        short = {"version_id": "0000000009-aaaabbbbcccc",
                 "unreadable": [{"path": "/etc/locked",
                                 "error": "PermissionError: [Errno 13]"}]}

        class _Engine:
            def capture(self, *_a, **_kw):
                return short

        class _Stop(BaseException):
            pass

        fingerprints = iter(["one", "two"])
        err = io.StringIO()
        handler = logging.StreamHandler(err)
        logging.getLogger("chronicle").addHandler(handler)
        logging.getLogger("chronicle").setLevel(logging.WARNING)
        try:
            with mock.patch.object(chronicled._sentinel, "config_set_fingerprint",
                                   side_effect=lambda *_a, **_k: next(fingerprints)), \
                 mock.patch.object(chronicled.time, "sleep",
                                   side_effect=[None, _Stop()]), \
                 redirect_stderr(err):
                try:
                    chronicled._config_watch_loop(_Engine(), interval=0)
                except _Stop:
                    pass
        finally:
            logging.getLogger("chronicle").removeHandler(handler)
        self.assertIn("/etc/locked", err.getvalue(), (
            "the watcher captured a version short of its source and said "
            "nothing, so a configuration backup can be short with no trace"))


# --------------------------------------------------------------------------
# The window
# --------------------------------------------------------------------------


class TheTimelineRow(unittest.TestCase):
    """The one surface a person looks at. `chronicle list` already carries the
    count; the window's row did not, so the same version read as complete in
    the window and short on the command line."""

    def test_a_short_version_carries_its_count(self):
        subtitle = getattr(_gui, "timeline_subtitle", None)
        self.assertIsNotNone(subtitle, (
            "the window builds its timeline subtitle inline, so there is "
            "nowhere for the count to come from and no way to state what the "
            "row says"))
        text = subtitle({"version_id": "0000000004-6b2dc07a701c", "files": 2561,
                         "reason": "hourly user-data", "unreadable": 1})
        self.assertIn("2561 files", text)
        self.assertIn("1", text.split("files", 1)[1], (
            f"the row does not say the version is short of its source: {text!r}"))
        self.assertIn("could not be read", text)

    def test_a_complete_version_says_nothing_about_omissions(self):
        """CONTROL."""
        subtitle = getattr(_gui, "timeline_subtitle", None)
        self.assertIsNotNone(subtitle)
        text = subtitle({"version_id": "0000000005-b15634aa7439", "files": 2761,
                         "reason": "hourly user-data", "unreadable": 0})
        self.assertIn("2761 files", text)
        self.assertNotIn("could not be read", text)


if __name__ == "__main__":
    unittest.main()
