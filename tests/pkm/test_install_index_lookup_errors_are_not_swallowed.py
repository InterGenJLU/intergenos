#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""An install's repository-index lookup does not turn an error into "absent".

Measured 2026-09-19 while changing the lookup's call signature: a TypeError
raised inside `repo.get_package(...)` was caught by a bare `except Exception`
in the install path, which set the looked-up entry to None. The archive was
then treated as a package the index has never heard of, so the line that tells
the person WHY the install is being refused —

    ✗ error: archive SHA256 does not match repository index!

— disappeared, along with the two sha256 values under it. The strict refusal
still fired, so the install was still refused and the exit was still non-zero;
what vanished was the reason, and nothing anywhere reported an error. A
failure that reports itself as an ordinary absence is the failure this
project's gates exist to prevent.

The rule pinned here: only the errors that genuinely MEAN "there is no entry
to compare against" are absorbed — an unreadable index cache, which the trust
gate then refuses on — and every other error travels with its own message
instead of being renamed.

Executed against the shipped command code with its collaborators stood in; the
archive is a real file on disk, because the code hashes it before it decides.
"""

import argparse
import hashlib
import io
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import MagicMock, patch

import pkm.cli as cli

NAME = "demo-pkg"
INDEX_SHA = "b" * 64


def _index_entry(name):
    return {"name": name, "version": "1.0", "release": 1,
            "sha256": INDEX_SHA, "size": 1000, "installed_size": 4000}


def _install_args(archive, trust):
    return argparse.Namespace(
        packages=[NAME], archive=str(archive), archive_trust=trust,
        quiet=False, verbose=False, allow_downgrade=False, assume_yes=True,
    )


class TheIndexLookupDoesNotRenameItsErrors(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.archive = Path(self._tmp.name) / f"{NAME}-1.0-1.igos.tar.gz"
        self.archive.write_bytes(b"not the archive the index describes")
        self.assertNotEqual(
            hashlib.sha256(self.archive.read_bytes()).hexdigest(), INDEX_SHA,
            "the fixture archive must not match the index, or it proves "
            "nothing")
        self.addCleanup(self._tmp.cleanup)

    def _run(self, trust, lookup):
        """Run the install command with a stood-in index lookup.

        Returns (raised, stdout, stderr); `raised` is whatever left the
        command — a SystemExit for a refusal, or the error itself."""
        db = MagicMock()
        db.get_installed.return_value = None
        raised = None
        out, err = io.StringIO(), io.StringIO()
        with patch("pkm.cli.PackageInstaller") as Installer, \
             patch("pkm.cli.RepoManager") as Repo, \
             patch("pkm.pretxn.run_pre_transaction_hook"), \
             patch("pkm.cli.refresh_available_updates_after_transaction"), \
             patch("pkm.cli._print_transaction_next_steps"):
            Repo.return_value.get_package.side_effect = lookup
            installer = Installer.return_value
            installer.install.side_effect = AssertionError(
                "the refused archive was handed to install()")
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    cli.cmd_install(db, _install_args(self.archive, trust))
                except BaseException as exc:      # noqa: BLE001 — recorded
                    raised = exc
        return raised, out.getvalue(), err.getvalue()

    def test_an_unexpected_error_is_not_read_as_a_missing_entry(self):
        """THE DEFECT. A TypeError from the lookup used to become None."""
        def _boom(_name):
            raise TypeError("get_package() got an unexpected keyword argument")
        raised, out, err = self._run("strict", _boom)
        self.assertIsInstance(raised, TypeError, (out, err))

    def test_the_mismatch_line_is_there_when_the_lookup_works(self):
        """The control the defect was found by: with a working lookup the
        person is told why, not merely that."""
        _raised, out, err = self._run("strict", _index_entry)
        self.assertIn("does not match repository index", err)
        # The two sha256 values are printed under the error line, on stdout.
        self.assertIn(INDEX_SHA, out + err)

    def test_an_unreadable_index_cache_is_an_absent_entry_and_is_said_so(self):
        """The one error that DOES mean "no entry to compare against". The
        install is still refused under strict trust, and the reason names the
        unreadable index rather than implying the package is unknown."""
        def _unreadable(_name):
            raise OSError(13, "Permission denied")
        raised, out, err = self._run("strict", _unreadable)
        self.assertIsInstance(raised, SystemExit, (out, err))
        self.assertNotEqual(raised.code, 0, err)
        self.assertIn("repository index could not be read", err)
        self.assertIn("Permission denied", err)

    def test_a_missing_entry_is_still_an_ordinary_absence(self):
        """A package the index has never heard of is not an error: no entry,
        no mismatch line, and the trust gate refuses on its own terms."""
        _raised, _out, err = self._run("strict", lambda _name: None)
        self.assertNotIn("does not match repository index", err)
        self.assertNotIn("could not be read", err)
        self.assertIn("--archive-trust=strict requires SHA256 match", err)


if __name__ == "__main__":
    unittest.main()
