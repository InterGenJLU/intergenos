#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A read command that cannot read the index says so, and never calls it empty.

The index layer used to delete a cached index it could not verify and answer
"nothing here", for two different facts at once: a signature that does not
verify, and bytes the filesystem would not let pkm read. Since 2026-10-01 it
distinguishes them — a read failure leaves the file in place, names the repo and
the errno, and travels to the caller as an OSError (pkm/repo.py
`_ensure_synced`).

What that left was the read commands. Measured at the real mechanism on
2026-10-01 — a real cached index at mode 000 under a throwaway root, the real
gpgv, the shipped commands — at the commit before this one:

  * `pkm search <name>`        exit 1 with a traceback after the layer's lines
  * `pkm list available`       exit 1 with a traceback after the layer's lines
  * `pkm info <name>`          exit 1 saying only "is not installed": its
                               documented bare `except Exception` absorbed the
                               error, so the person was told the index had
                               nothing to say about this package when nobody had
                               been able to ask it
  * `pkm check-updates`        the same class, one step further back: the
                               scheduled check's own probe and comparison both
                               reach the index

and at the commit BEFORE the index layer began to raise, the same commands said
"No repository index yet", "No packages available" and "Everything is up to
date" — with exit 0 — about an index that existed and had just been destroyed.

The rule pinned here: each command reports in its OWN words, naming what it was
doing when the read failed, and exits non-zero ON PURPOSE. Nothing changes for a
genuinely empty or absent index: those sentences and those exit codes are held
by the controls below, because the point is to tell the two apart.

The layer's answer for each cache state is stood in here so that the commands'
words can be held to the line; the real cached file, the real verifier and the
real commands are in this lane's own probe, at both ends.
"""

import argparse
import io
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import MagicMock, patch

import pkm.cli as cli

NAME = "demo-pkg"
THE_ERROR = PermissionError(
    13, "Permission denied: '/var/cache/pkm/db/intergenos-current.db'")


class _Layer:
    """A stand-in index layer: each entry point answers, or raises what the real
    one raises for a cached index the filesystem refuses."""

    def __init__(self, **behaviour):
        self._behaviour = behaviour

    def _answer(self, which, default):
        if which not in self._behaviour:
            return default
        value = self._behaviour[which]
        if isinstance(value, BaseException):
            raise value
        return value

    def search(self, _term):
        return self._answer("search", [])

    def has_synced_index(self):
        return self._answer("has_synced_index", True)

    def list_available(self, tier=None):
        return self._answer("list_available", [])

    def get_package(self, _name):
        return self._answer("get_package", None)

    def cache_dir(self):
        return Path("/var/cache/pkm")


def _run(command, db, args, layer, **kwargs):
    """Run a command with the layer stood in; return (rc, raised, said)."""
    out, err = io.StringIO(), io.StringIO()
    raised, rc = None, "did not return"
    with patch("pkm.cli.repo_manager", return_value=layer):
        with redirect_stdout(out), redirect_stderr(err):
            try:
                rc = command(db, args, **kwargs)
            except BaseException as exc:          # noqa: BLE001 — recorded
                raised = exc
    return rc, raised, " ".join((out.getvalue() + " " + err.getvalue()).split())


def _nonzero(testcase, rc, raised, where):
    """A command failed on purpose: a non-zero return, or SystemExit non-zero,
    and NOT an exception that merely escaped."""
    if isinstance(raised, SystemExit):
        testcase.assertNotEqual(raised.code, 0, f"{where} exited zero")
        return
    testcase.assertIsNone(
        raised,
        f"{where} let {type(raised).__name__} escape instead of reporting it")
    testcase.assertTrue(
        isinstance(rc, int) and rc != 0,
        f"{where} returned {rc!r}; a read command that could not read the index "
        f"has not done its job and must not say it did")


class ASearchThatCouldNotReadTheIndex(unittest.TestCase):

    def _args(self):
        return argparse.Namespace(term=NAME, quiet=False, verbose=False)

    def test_the_search_itself_is_reported_in_the_commands_own_words(self):
        db = MagicMock()
        db.search.return_value = []
        rc, raised, said = _run(cli.cmd_search, db, self._args(),
                                _Layer(search=THE_ERROR))
        _nonzero(self, rc, raised, "search")
        self.assertIn("the search for", said)
        self.assertIn(NAME, said)
        self.assertIn("could not be read", said)
        self.assertIn("Permission denied", said)
        self.assertNotIn("No repository index yet", said)

    def test_the_advisory_probe_is_reported_too(self):
        """Reached when the first read succeeded and a later one did not; the
        advisory would otherwise choose between two false sentences."""
        db = MagicMock()
        db.search.return_value = []
        rc, raised, said = _run(cli.cmd_search, db, self._args(),
                                _Layer(search=[], has_synced_index=THE_ERROR))
        _nonzero(self, rc, raised, "search's advisory")
        self.assertIn("could not be read", said)
        self.assertNotIn("No repository index yet", said)

    def test_an_absent_index_keeps_todays_words_and_todays_exit(self):
        """The control: nothing synced yet is a different fact and still reads
        exactly as it did."""
        db = MagicMock()
        db.search.return_value = []
        rc, raised, said = _run(cli.cmd_search, db, self._args(),
                                _Layer(search=[], has_synced_index=False))
        self.assertIsNone(raised)
        self.assertIn("No repository index yet", said)
        self.assertIn("sudo pkm sync", said)

    def test_a_working_index_with_no_match_keeps_todays_words(self):
        db = MagicMock()
        db.search.return_value = []
        rc, raised, said = _run(cli.cmd_search, db, self._args(),
                                _Layer(search=[], has_synced_index=True))
        self.assertIsNone(raised)
        self.assertIn(f"No packages matching '{NAME}'", said)


class AListingThatCouldNotReadTheIndex(unittest.TestCase):

    @staticmethod
    def _db():
        db = MagicMock()
        db.list_installed.return_value = [
            {"name": NAME, "version": "1.0", "release": 1, "tier": "extra"}]
        return db

    def test_list_available_is_reported_in_the_commands_own_words(self):
        rc, raised, said = _run(
            cli.cmd_list, self._db(),
            argparse.Namespace(what="available", tier=None, quiet=False,
                               verbose=False),
            _Layer(list_available=THE_ERROR))
        _nonzero(self, rc, raised, "list available")
        self.assertIn("available packages could not be listed", said)
        self.assertIn("Permission denied", said)
        self.assertNotIn("No packages available", said)

    def test_list_available_with_an_empty_index_keeps_todays_words(self):
        rc, raised, said = _run(
            cli.cmd_list, self._db(),
            argparse.Namespace(what="available", tier=None, quiet=False,
                               verbose=False),
            _Layer(list_available=[]))
        self.assertIsNone(raised)
        self.assertIn("No packages available", said)
        self.assertIn("pkm sync", said)

    def test_list_upgradable_never_says_up_to_date_for_what_it_could_not_read(self):
        rc, raised, said = _run(
            cli.cmd_list, self._db(),
            argparse.Namespace(what="upgradable", tier=None, quiet=False,
                               verbose=False),
            _Layer(get_package=THE_ERROR))
        _nonzero(self, rc, raised, "list upgradable")
        self.assertIn("could not be compared", said)
        self.assertIn(NAME, said, "the package it stopped at is named")
        self.assertNotIn("Everything is up to date", said)
        self.assertNotIn("Everything the repository index carries is up to date",
                         said)

    def test_list_upgradable_with_an_index_that_has_no_entry_is_unchanged(self):
        """The control for the OTHER 2026-09-17 finding, which must survive: a
        package with no index row is named as uncomparable, not called current."""
        rc, raised, said = _run(
            cli.cmd_list, self._db(),
            argparse.Namespace(what="upgradable", tier=None, quiet=False,
                               verbose=False),
            _Layer(get_package=None))
        self.assertIsNone(raised)
        self.assertIn("no entry in the repository index", said)
        self.assertIn("not checked for upgrades", said)


class AnInfoThatCouldNotReadTheIndex(unittest.TestCase):

    @staticmethod
    def _db():
        db = MagicMock()
        db.get_installed.return_value = None
        return db

    def test_it_is_reported_ahead_of_the_documented_bare_except(self):
        rc, raised, said = _run(cli.cmd_info, self._db(),
                                argparse.Namespace(package=NAME, quiet=False,
                                                   verbose=False),
                                _Layer(get_package=THE_ERROR))
        _nonzero(self, rc, raised, "info")
        self.assertIn("could not be established", said)
        self.assertIn("could not be read", said)
        self.assertIn("Permission denied", said)

    def test_an_index_fault_that_is_not_a_read_failure_still_does_not_break_it(self):
        """The bare `except Exception` below the new handler is a decision with
        its reason in the source; a parse fault still degrades to the plain
        line."""
        class _Parsey(_Layer):
            def get_package(self, _name):
                raise ValueError("a malformed cached row")

        rc, raised, said = _run(cli.cmd_info, self._db(),
                                argparse.Namespace(package=NAME, quiet=False,
                                                   verbose=False),
                                _Parsey())
        self.assertIsNone(raised)
        self.assertEqual(rc, 1)
        self.assertIn(f"Package '{NAME}' is not installed", said)

    def test_a_package_the_index_does_not_carry_keeps_todays_words(self):
        rc, raised, said = _run(cli.cmd_info, self._db(),
                                argparse.Namespace(package=NAME, quiet=False,
                                                   verbose=False),
                                _Layer(get_package=None))
        self.assertIsNone(raised)
        self.assertEqual(rc, 1)
        self.assertIn(f"Package '{NAME}' is not installed", said)


class AScheduledCheckThatCouldNotReadTheIndex(unittest.TestCase):
    """The advisory the desktop notifier and the message of the day read. A zero
    this command did not establish is never published — the same rule the
    read-only cache failure and the unsynced index already have."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.output = Path(self._td.name) / "available-updates.json"

    @staticmethod
    def _db():
        db = MagicMock()
        db.list_installed.return_value = [
            {"name": NAME, "version": "1.0", "release": 1}]
        return db

    def test_the_probe_is_reported_and_nothing_is_written(self):
        rc, raised, said = _run(cli.cmd_check_updates, self._db(),
                                argparse.Namespace(quiet=False),
                                _Layer(has_synced_index=THE_ERROR),
                                output_path=self.output)
        _nonzero(self, rc, raised, "check-updates' probe")
        self.assertIn("could not be READ", said)
        self.assertIn("No update count was written", said)
        self.assertFalse(self.output.exists(),
                         "a count nobody established must not be published")

    def test_the_comparison_is_reported_and_nothing_is_written(self):
        rc, raised, said = _run(cli.cmd_check_updates, self._db(),
                                argparse.Namespace(quiet=False),
                                _Layer(has_synced_index=True,
                                       get_package=THE_ERROR),
                                output_path=self.output)
        _nonzero(self, rc, raised, "check-updates' comparison")
        self.assertIn("could not be READ", said)
        self.assertIn("No update count was written", said)
        self.assertFalse(self.output.exists())
        self.assertNotIn("Everything is up to date", said)

    def test_an_unsynced_index_keeps_todays_refusal(self):
        """The control: the 2026-09-22 refusal is a different fact and is
        unchanged, words and exit."""
        rc, raised, said = _run(cli.cmd_check_updates, self._db(),
                                argparse.Namespace(quiet=False),
                                _Layer(has_synced_index=False),
                                output_path=self.output)
        _nonzero(self, rc, raised, "check-updates with no synced index")
        self.assertIn("no repository index is synced in the local cache", said)
        self.assertFalse(self.output.exists())

    def test_a_working_index_still_writes_its_count(self):
        """The control that the command still does its job."""
        rc, raised, said = _run(
            cli.cmd_check_updates, self._db(), argparse.Namespace(quiet=False),
            _Layer(has_synced_index=True,
                   get_package={"name": NAME, "version": "2.0", "release": 1}),
            output_path=self.output)
        self.assertIsNone(raised)
        self.assertIn(rc, (None, 0),
                      "the happy path returns no code, which main() exits 0 on")
        self.assertTrue(self.output.exists(), "the advisory is written")
        self.assertIn("have updates available", said)


class NoReadCommandBorrowsAnothersWords(unittest.TestCase):
    """Each command names what IT was doing. A shared reporter inside the index
    layer would have forced one sentence on all of them, and a person reading
    `list` output would be told about a search."""

    def test_the_five_reports_are_five_different_sentences(self):
        db_search = MagicMock(); db_search.search.return_value = []
        db_list = MagicMock()
        db_list.list_installed.return_value = [
            {"name": NAME, "version": "1.0", "release": 1, "tier": "extra"}]
        db_info = MagicMock(); db_info.get_installed.return_value = None
        db_check = MagicMock()
        db_check.list_installed.return_value = [
            {"name": NAME, "version": "1.0", "release": 1}]
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)

        runs = {
            "search": _run(cli.cmd_search, db_search,
                           argparse.Namespace(term=NAME, quiet=False,
                                              verbose=False),
                           _Layer(search=THE_ERROR)),
            "list available": _run(
                cli.cmd_list, db_list,
                argparse.Namespace(what="available", tier=None, quiet=False,
                                   verbose=False),
                _Layer(list_available=THE_ERROR)),
            "list upgradable": _run(
                cli.cmd_list, db_list,
                argparse.Namespace(what="upgradable", tier=None, quiet=False,
                                   verbose=False),
                _Layer(get_package=THE_ERROR)),
            "info": _run(cli.cmd_info, db_info,
                         argparse.Namespace(package=NAME, quiet=False,
                                            verbose=False),
                         _Layer(get_package=THE_ERROR)),
            "check-updates": _run(
                cli.cmd_check_updates, db_check,
                argparse.Namespace(quiet=False),
                _Layer(has_synced_index=THE_ERROR),
                output_path=Path(td.name) / "a.json"),
        }
        sentences = {}
        for where, (_rc, _raised, said) in runs.items():
            with self.subTest(command=where):
                self.assertIn("could not be", said)
                head = said.split("Permission denied")[0].strip()
                self.assertTrue(head, f"{where} said nothing of its own")
                sentences[where] = head
        self.assertEqual(
            len(set(sentences.values())), len(sentences),
            f"two read commands print the SAME sentence: {sentences!r}")


if __name__ == "__main__":
    unittest.main()
