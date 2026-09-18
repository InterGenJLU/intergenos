#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A refused archive install makes `pkm install` exit non-zero.

Measured on a test machine on 2026-09-18: `pkm install --archive <archive>
--yes <name>` under the default --archive-trust=strict printed

    ✗ error: archive SHA256 does not match repository index!
    ✗ error: --archive-trust=strict requires SHA256 match against repository
      index. Use --archive-trust=loose to override.

installed nothing, left the recorded release where it was — and exited 0.

The trust gate printed its error and `continue`d out of the per-package loop
without recording anything, so the command reached its end with an empty
transaction and reported success. Anything that reads the exit status — a
build step, a script, a person — was told the install had worked. An error
message the exit code contradicts is a silent failure with extra steps.

Both refusing modes are pinned here, and the loose mode's warning-and-proceed
is pinned unchanged beside them, because the fix must not turn a deliberate
local install into a refusal.

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


class RefusedArchiveInstallExitsNonZero(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.archive = Path(self._tmp.name) / f"{NAME}-1.0-1.igos.tar.gz"
        self.archive.write_bytes(b"not the archive the index describes")
        self.assertNotEqual(
            hashlib.sha256(self.archive.read_bytes()).hexdigest(), INDEX_SHA,
            "the fixture archive must not match the index, or it proves nothing")
        self.addCleanup(self._tmp.cleanup)

    def _run(self, trust):
        """Returns (SystemExit or None, stdout, stderr)."""
        db = MagicMock()
        db.get_installed.return_value = None
        raised = None
        out, err = io.StringIO(), io.StringIO()
        with patch("pkm.cli.PackageInstaller") as Installer, \
             patch("pkm.cli.RepoManager") as Repo, \
             patch("pkm.pretxn.run_pre_transaction_hook"), \
             patch("pkm.cli.refresh_available_updates_after_transaction"), \
             patch("pkm.cli._print_transaction_next_steps"):
            Repo.return_value.get_package.side_effect = _index_entry
            installer = Installer.return_value
            # A refusal must never reach the install call. If it does, this
            # says so loudly instead of letting the test pass on a stub.
            installer.install.side_effect = AssertionError(
                "the refused archive was handed to install()")
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    cli.cmd_install(db, _install_args(self.archive, trust))
                except SystemExit as exc:
                    raised = exc
        return raised, out.getvalue(), err.getvalue()

    def test_strict_refusal_exits_non_zero(self):
        raised, _out, err = self._run("strict")
        self.assertIsNotNone(raised, err)
        self.assertNotEqual(raised.code, 0, err)

    def test_strict_refusal_keeps_its_error_text(self):
        _raised, _out, err = self._run("strict")
        self.assertIn("--archive-trust=strict requires SHA256 match", err)
        self.assertIn("does not match repository index", err)

    def test_repo_only_refusal_exits_non_zero(self):
        raised, _out, err = self._run("repo-only")
        self.assertIsNotNone(raised, err)
        self.assertNotEqual(raised.code, 0, err)

    def test_repo_only_refusal_keeps_its_error_text(self):
        _raised, _out, err = self._run("repo-only")
        self.assertIn("--archive-trust=repo-only requires archive SHA256", err)

    def test_the_summary_names_the_refused_package_and_the_reason(self):
        _raised, out, err = self._run("strict")
        both = out + err
        self.assertIn(NAME, both)
        self.assertIn("refused", both)
        self.assertIn("nothing was installed", both)

    def test_loose_still_proceeds_and_is_not_counted_as_a_refusal(self):
        """The override is a deliberate choice, not an error."""
        db = MagicMock()
        db.get_installed.return_value = None
        raised = None
        out, err = io.StringIO(), io.StringIO()
        with patch("pkm.cli.PackageInstaller") as Installer, \
             patch("pkm.cli.RepoManager") as Repo, \
             patch("pkm.pretxn.run_pre_transaction_hook"), \
             patch("pkm.cli.refresh_available_updates_after_transaction"), \
             patch("pkm.cli._print_transaction_next_steps"), \
             patch("pkm.cli._continue_into_payload_if_helper",
                   return_value="not-a-helper"):
            Repo.return_value.get_package.side_effect = _index_entry
            Installer.return_value.install.return_value = (True, "installed")
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    cli.cmd_install(db, _install_args(self.archive, "loose"))
                except SystemExit as exc:
                    raised = exc
        self.assertIsNone(raised, (out.getvalue(), err.getvalue()))
        self.assertIn("--archive-trust=loose", err.getvalue())


if __name__ == "__main__":
    unittest.main()
