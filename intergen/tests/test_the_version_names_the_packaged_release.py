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

import contextlib
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest import mock

import intergen
from intergen import cli
from intergen.dbus_daemon import InterGenDaemon
from intergen.hardware import HardwareTierLevel
from intergen.model_manager import ModelInfo, ModelManager

#: What the package manager records for the assistant on a real machine.
A_RECORD = ("0.1.0", 296)


def _reader():
    """The module under test, imported inside the case that uses it.

    Imported here rather than at the top of this file on purpose. At a tree
    where this module does not exist yet, a module-level import makes every
    case in this file an ERROR AT COLLECTION — the file never runs, so it
    never goes red on what the commands PRINT, which is the whole thing these
    cases exist to pin. Imported inside the case, the same tree is red case by
    case, and the cases below that exercise a COMMAND are red on the output
    that command produced.
    """
    from intergen import package_record
    return package_record


def _reader_or_none():
    """The module under test, or None at a tree that does not have it yet.

    The command cases use this so that such a tree still RUNS the command and
    fails on what it printed. Supplying the record is how those cases avoid
    reading the machine they run on; a tree with no reader has no record to
    supply and no reader to read the machine with, so running the command
    unsupplied is both safe and the point.
    """
    try:
        from intergen import package_record
    except ImportError:
        return None
    return package_record


def _pkm_schema():
    """The package database's real table definitions, from the package manager
    itself.

    Taken from `pkm.database.SCHEMA` rather than written out here, because a
    fixture that carries its own copy of a schema is a copy that drifts. The
    earlier form of these cases built a four-column table with no constraints
    and inserted two rows for one package — a pair the real table forbids,
    since it carries UNIQUE(name) — so the case proved a choice between rows
    that cannot both exist on an installed machine.
    """
    from pkm.database import SCHEMA
    return SCHEMA


@contextlib.contextmanager
def _record_supplied(record):
    """Supply the package record for the duration of a case, where there is a
    reader to supply it to; at a tree without one, change nothing and let the
    path under test answer as it does."""
    reader = _reader_or_none()
    if reader is None:
        yield
        return
    with mock.patch.object(reader, "installed_identity", return_value=record):
        yield


def _database_with(rows, path, wal=False):
    """A package database at `path`, built to the real schema, holding `rows`.

    Each row is (name, version, release, superseded_by).
    """
    import sqlite3
    con = sqlite3.connect(path)
    if wal:
        con.execute("PRAGMA journal_mode = WAL")
    con.executescript(_pkm_schema())
    for name, version, release, superseded_by in rows:
        con.execute("INSERT INTO installed (name, version, release, "
                    "superseded_by) VALUES (?, ?, ?, ?)",
                    (name, version, release, superseded_by))
    con.commit()
    con.close()


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
    reader = _reader_or_none()
    with contextlib.ExitStack() as stack:
        if reader is not None:
            stack.enter_context(mock.patch.object(
                reader, "installed_identity", return_value=record))
        stack.enter_context(mock.patch.object(
            ModelManager, "list_downloaded", return_value=list(downloaded)))
        stack.enter_context(mock.patch.object(
            cli.sys, "argv", ["intergen", "--version"]))
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
        with _record_supplied(A_RECORD):
            status = cli.offline_status()
        self.assertEqual(status["version"], "0.1.0-296",
                         "the daemon-down status names a version with no "
                         "release: " + repr(status["version"]))

    def test_the_running_daemon_carries_the_release(self):
        with _record_supplied(A_RECORD):
            status = json.loads(_status_daemon().status())
        self.assertEqual(status["version"], "0.1.0-296",
                         "the running daemon's status names a version with no "
                         "release: " + repr(status["version"]))

    def test_the_two_paths_agree(self):
        """A reader comparing a running machine with a stopped one must not be
        shown two different answers to the same question."""
        with _record_supplied(("3.2.1", 7)):
            down = cli.offline_status()["version"]
            up = json.loads(_status_daemon().status())["version"]
        self.assertEqual(down, up)
        self.assertEqual(up, "3.2.1-7")


class TheRecordReaderItself(unittest.TestCase):

    def test_a_database_that_is_not_there_answers_none(self):
        self.assertIsNone(
            _reader().installed_identity(db_path="/nonexistent/pkm.db"))

    def test_a_file_that_is_not_a_database_answers_none(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".db") as handle:
            handle.write(b"this is not a database")
            handle.flush()
            self.assertIsNone(
                _reader().installed_identity(db_path=handle.name))

    def test_a_real_database_shape_is_read(self):
        """Built to the package manager's own schema, so the reader is
        exercised against a database of the shape it will meet and not only
        against failures.

        One row, because the real table carries UNIQUE(name): a machine has
        one record per package, and a case that inserts two is describing a
        machine that cannot exist.
        """
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = tmp + "/pkm.db"
            _database_with([("intergen", "0.1.0", 296, None)], path)
            self.assertEqual(_reader().installed_identity(db_path=path),
                             ("0.1.0", 296))

    def test_a_row_still_in_the_write_ahead_log_is_not_answered_with_the_old_one(self):
        """A committed row this read cannot see does not license answering
        with the row underneath it.

        The reader opens the database immutable, and an immutable open ignores
        the write-ahead log. So between a package operation committing a new
        release and that log being checkpointed, the newest row is invisible
        here while the PREVIOUS one reads perfectly — and printing that one is
        printing a stale release as fact, with nothing on it saying so. The
        database below is in the write-ahead mode the package manager itself
        uses, with one release checkpointed into the main file and a newer one
        committed but still in the log: the exact window.
        """
        import os
        import sqlite3
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = tmp + "/pkm.db"
            _database_with([("intergen", "0.1.0", 297, None)], path, wal=True)
            self.assertEqual(_reader().installed_identity(db_path=path),
                             ("0.1.0", 297),
                             "the checkpointed row is not readable, so the "
                             "window this case builds would prove nothing")
            writer = sqlite3.connect(path)
            try:
                writer.execute("UPDATE installed SET release = 298 "
                               "WHERE name = 'intergen'")
                writer.commit()
                self.assertGreater(
                    os.path.getsize(path + "-wal"), 0,
                    "no write-ahead log on disk: this case is not in the "
                    "window it means to test")
                self.assertIsNone(
                    _reader().installed_identity(db_path=path),
                    "release 298 is committed and this read cannot see it, "
                    "yet the reader answered with the release before it")
            finally:
                writer.close()
            self.assertEqual(_reader().installed_identity(db_path=path),
                             ("0.1.0", 298),
                             "once the log is checkpointed the newest row is "
                             "what is read")

    def test_a_superseded_row_alone_answers_none(self):
        """A row that has been replaced is not what is installed.

        One row, on the real schema: this is the reachable case, a machine
        whose only record for this package has been superseded.
        """
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = tmp + "/pkm.db"
            _database_with([("intergen", "0.1.0", 295, "x")], path)
            self.assertIsNone(_reader().installed_identity(db_path=path))


if __name__ == "__main__":
    unittest.main()
