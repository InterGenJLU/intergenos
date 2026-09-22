# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""`intergen --version` and `intergen status` name the release that is installed.

Both printed "0.1.0" and nothing else. That literal lives in
`intergen/__init__.py` and has not moved in the life of the project, while the
package that places the code is on its 296th release, so neither command could
tell two builds apart — and on a machine where an upgrade half-finished, both
would still say "0.1.0" and sound correct. Every other component is identified
the way the package manager identifies it, version and release together.

The release is a packaging fact the running code cannot know, so it is read
from the package manager's own record and from nowhere else. These cases pin:

* the printed identity carries the release from the record, in the package
  manager's own form;
* it FOLLOWS the record — a second literal typed into the CLI would pass a
  fixed-string assertion and then drift, so the record is moved and the print
  has to move with it;
* with no record (a checkout, a container, a machine where this was never
  installed) the version the running code carries is printed and the reader is
  TOLD the release could not be read, rather than being left to think the bare
  version was the whole answer;
* `intergen status` carries the same identity, from the running daemon and from
  the daemon-down path alike, because a reader comparing the two must not be
  shown two different answers to the same question;
* the model attribution still renders beside it — this change must not disturb
  a license statement.

Every case supplies the record itself, so none of them reads the machine the
tests happen to run on.
"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest import mock

import intergen
from intergen import cli, package_record
from intergen.dbus_daemon import InterGenDaemon
from intergen.hardware import HardwareTierLevel
from intergen.model_manager import ModelInfo, ModelManager

#: What the package manager records for the assistant on a real machine.
A_RECORD = ("0.1.0", 296)


def _model(name: str, tier: HardwareTierLevel) -> ModelInfo:
    return ModelInfo(
        name=name, filename=f"{name}-Q4_K_M.gguf", repo_id=f"test/{name}",
        quant="Q4_K_M", size_gb=1.0, sha256="0" * 64, tier=tier,
        local_path=f"/nonexistent/{name}-Q4_K_M.gguf", downloaded=True,
    )


QWEN_9B = _model("Qwen3.5-9B", HardwareTierLevel.TIER_2)


def _run_version(record=A_RECORD, downloaded=(QWEN_9B,)):
    """`intergen --version` with the package record supplied, not read.

    Returns (stdout, exit code).
    """
    buf = io.StringIO()
    code = None
    with mock.patch.object(package_record, "installed_identity",
                           return_value=record), \
         mock.patch.object(ModelManager, "list_downloaded",
                           return_value=list(downloaded)), \
         mock.patch.object(cli.sys, "argv", ["intergen", "--version"]):
        with redirect_stdout(buf):
            try:
                cli.main()
            except SystemExit as exc:
                code = exc.code
    return buf.getvalue(), code


def _status_daemon(**over):
    """A daemon built by __new__ with only the fields status() reads, the same
    shape the existing daemon-status cases use."""
    d = InterGenDaemon.__new__(InterGenDaemon)
    d._running = True
    d._hardware_tier = None
    d._model_loaded = None
    d._requests_handled = 0
    d._last_error = None
    d._model_server_integrity_failure = None
    d._llama = None
    d._router = None
    d._matcher = None
    d._tools = None
    d._memory = None
    d._watchdog = None
    d._metrics = None
    d._review_autopilot = None
    d._paused = False
    d._pause_holds = []
    for k, v in over.items():
        setattr(d, "_" + k, v)
    return d


class TheVersionCommandNamesTheRelease(unittest.TestCase):

    def test_the_printed_identity_carries_the_release_from_the_record(self):
        out, code = _run_version()
        self.assertIn("0.1.0-296", out,
                      "--version printed no release; a reader cannot tell two "
                      "builds apart from what it printed:\n" + out)
        self.assertIn(code, (None, 0))

    def test_the_printed_identity_follows_the_record(self):
        """A literal typed into the CLI would pass the case above and then
        drift at the next release. Move the record and the print must move."""
        out, _ = _run_version(record=("7.7.7", 4242))
        self.assertIn("7.7.7-4242", out,
                      "the printed identity did not follow the package "
                      "record — it is a second copy, not what is installed:\n"
                      + out)

    def test_with_no_record_the_reader_is_told_the_release_is_unknown(self):
        """Silence would leave a bare version looking like the whole answer."""
        out, code = _run_version(record=None)
        self.assertIn(intergen.__version__, out)
        self.assertIn("release", out.lower(),
                      "with no package record the output says nothing about "
                      "the release being unknown:\n" + out)
        self.assertIn(code, (None, 0))

    def test_the_attribution_still_renders(self):
        """The license statement must survive a change to the line above it."""
        out, _ = _run_version()
        self.assertIn("Powered by Qwen", out)


class StatusCarriesTheSameIdentity(unittest.TestCase):

    def test_the_daemon_down_path_carries_the_release(self):
        with mock.patch.object(package_record, "installed_identity",
                               return_value=A_RECORD):
            status = cli.offline_status()
        self.assertEqual(status["version"], "0.1.0-296",
                         "the daemon-down status names a version with no "
                         "release: " + repr(status["version"]))

    def test_the_running_daemon_carries_the_release(self):
        with mock.patch.object(package_record, "installed_identity",
                               return_value=A_RECORD):
            status = json.loads(_status_daemon().status())
        self.assertEqual(status["version"], "0.1.0-296",
                         "the running daemon's status names a version with no "
                         "release: " + repr(status["version"]))

    def test_the_two_paths_agree(self):
        """A reader comparing a running machine with a stopped one must not be
        shown two different answers to the same question."""
        with mock.patch.object(package_record, "installed_identity",
                               return_value=("3.2.1", 7)):
            down = cli.offline_status()["version"]
            up = json.loads(_status_daemon().status())["version"]
        self.assertEqual(down, up)
        self.assertEqual(up, "3.2.1-7")


class TheRecordReaderItself(unittest.TestCase):

    def test_a_database_that_is_not_there_answers_none(self):
        self.assertIsNone(
            package_record.installed_identity(db_path="/nonexistent/pkm.db"))

    def test_a_file_that_is_not_a_database_answers_none(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".db") as handle:
            handle.write(b"this is not a database")
            handle.flush()
            self.assertIsNone(
                package_record.installed_identity(db_path=handle.name))

    def test_a_real_database_shape_is_read(self):
        """Built here to the shape the package manager uses, so the reader is
        exercised against a database and not only against failures."""
        import sqlite3
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = tmp + "/pkm.db"
            con = sqlite3.connect(path)
            con.execute("CREATE TABLE installed (name TEXT, version TEXT, "
                        "release INTEGER, superseded_by TEXT)")
            con.execute("INSERT INTO installed VALUES ('intergen','0.1.0',296,NULL)")
            con.execute("INSERT INTO installed VALUES ('intergen','0.1.0',295,'x')")
            con.commit()
            con.close()
            self.assertEqual(package_record.installed_identity(db_path=path),
                             ("0.1.0", 296))

    def test_a_superseded_row_alone_answers_none(self):
        """A row that has been replaced is not what is installed."""
        import sqlite3
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = tmp + "/pkm.db"
            con = sqlite3.connect(path)
            con.execute("CREATE TABLE installed (name TEXT, version TEXT, "
                        "release INTEGER, superseded_by TEXT)")
            con.execute("INSERT INTO installed VALUES ('intergen','0.1.0',295,'x')")
            con.commit()
            con.close()
            self.assertIsNone(package_record.installed_identity(db_path=path))


if __name__ == "__main__":
    unittest.main()
