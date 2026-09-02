#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A failed download helper makes `pkm install` exit non-zero.

Measured on the reference laptop on 2026-09-02, running `pkm install
cuda-toolkit` in a terminal: the helper downloaded NVIDIA's toolkit, laid
8,973 files into /opt/cuda, and aborted while recording them (see
test_cuda_helper_declares_mixed_widths.py). pkm printed the error in full —
and exited 0. The terminal the Welcomer opens keys its closing line on that
status, so it would have read "Installation finished successfully" under
an error that said the application was not tracked.

The flow now reports what happened, and every command that enters it exits
1 on a failure. A DECLINED license is unchanged: it is a choice, not an
error, and the command still exits 0 — the package database says the
application is absent, and the Welcomer asks the database.

Executed against the shipped command code with its collaborators stood in.
"""

import argparse
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

import pkm.cli as cli

APP = "cuda-toolkit"
ENGINE = "llama-cpp-cuda"


def _index_entry(name, **extra):
    entry = {"name": name, "version": "1.0", "release": 1, "sha256": "a" * 64,
             "size": 1000, "installed_size": 4000}
    entry.update(extra)
    return entry


def _install_args(*packages):
    return argparse.Namespace(
        packages=list(packages), archive=None, archive_trust="strict",
        quiet=False, verbose=False, allow_downgrade=False, assume_yes=True,
    )


class _Stdin:
    def isatty(self):
        return True


def _run_named(helper_result):
    """`pkm install cuda-toolkit`, the helper package named on the command
    line, with the helper's outcome given."""
    db = MagicMock()
    db.get_installed.return_value = {"name": APP, "version": "1.0", "release": 1}
    raised = None
    with patch("pkm.cli.PackageInstaller") as Installer, \
         patch("pkm.cli.RepoManager") as Repo, \
         patch("pkm.pretxn.run_pre_transaction_hook"), \
         patch("pkm.cli.helper_is_present", return_value=True), \
         patch("pkm.cli.helper_payload_present", return_value=False), \
         patch("pkm.cli.acceptance_record_exists", return_value=True), \
         patch("pkm.cli.sys.stdin", _Stdin()), \
         patch("builtins.input", return_value="y"), \
         patch("pkm.cli.refresh_available_updates_after_transaction"), \
         patch("pkm.cli._print_transaction_next_steps"):
        Repo.return_value.get_package.side_effect = lambda n: _index_entry(
            n, payload_license="LicenseRef-NVIDIA-CUDA-EULA")
        installer = Installer.return_value
        installer._find_helper.return_value = "/usr/bin/igos-install-cuda-toolkit"
        installer._run_helper.return_value = helper_result
        with redirect_stdout(io.StringIO()):
            try:
                cli.cmd_install(db, _install_args(APP))
            except SystemExit as exc:
                raised = exc
    return raised


def _run_as_dependency(helper_result):
    """`pkm install llama-cpp-cuda` resolving the helper package as a
    dependency, with the helper's outcome given."""
    db = MagicMock()
    db.get_installed.return_value = None
    raised = None
    with patch("pkm.cli.PackageInstaller") as Installer, \
         patch("pkm.cli.RepoManager") as Repo, \
         patch("pkm.pretxn.run_pre_transaction_hook"), \
         patch("pkm.cli.refuse_unrunnable_package_hook", return_value=None), \
         patch("pkm.cli.helper_is_present", side_effect=lambda n: n == APP), \
         patch("pkm.cli.helper_payload_present", return_value=False), \
         patch("pkm.cli.acceptance_record_exists", return_value=True), \
         patch("pkm.cli.sys.stdin", _Stdin()), \
         patch("builtins.input", return_value="y"), \
         patch("pkm.cli.refresh_available_updates_after_transaction"), \
         patch("pkm.cli._print_transaction_next_steps"):
        repo = Repo.return_value
        repo.get_package.side_effect = lambda n: _index_entry(n)
        repo.resolve_dependencies.return_value = (True, [APP, ENGINE])
        repo.download_package.side_effect = lambda n, reporter=None: (True, f"/tmp/{n}.igos.tar.gz")
        installer = Installer.return_value
        installer._find_archive.return_value = None
        installer.install.side_effect = [
            (False, f"No archive found for '{ENGINE}'"),
            (True, "installed"), (True, "installed"),
        ]
        installer._find_helper.return_value = "/usr/bin/igos-install-cuda-toolkit"
        installer._run_helper.return_value = helper_result
        # The helper package's row exists by the time its payload step runs
        # (it was just installed), so the flow does not lay it down again.
        db.get_installed.side_effect = lambda n: (
            {"name": n, "version": "1.0", "release": 1} if n == APP else None)
        with redirect_stdout(io.StringIO()):
            try:
                cli.cmd_install(db, _install_args(ENGINE))
            except SystemExit as exc:
                raised = exc
    return raised


FAILED = (False, "Install helper 'cuda-toolkit' aborted with exit 1 after depositing 347 file(s)", False)
DECLINED = (False, "the vendor license was not accepted", True)
OK = (True, "cuda-toolkit 13.3.1 installed", False)


class NamedOnTheCommandLine(unittest.TestCase):

    def test_a_failed_helper_exits_one(self):
        raised = _run_named(FAILED)
        self.assertIsNotNone(raised, "pkm exited 0 after the helper failed")
        self.assertEqual(raised.code, 1)

    def test_a_declined_license_still_exits_zero(self):
        self.assertIsNone(_run_named(DECLINED))

    def test_a_successful_helper_exits_zero(self):
        self.assertIsNone(_run_named(OK))


class ResolvedAsADependency(unittest.TestCase):

    def test_a_failed_helper_exits_one(self):
        raised = _run_as_dependency(FAILED)
        self.assertIsNotNone(raised, "pkm exited 0 after a dependency's "
                                     "download step failed")
        self.assertEqual(raised.code, 1)

    def test_a_declined_license_still_exits_zero(self):
        self.assertIsNone(_run_as_dependency(DECLINED))

    def test_a_successful_helper_exits_zero(self):
        self.assertIsNone(_run_as_dependency(OK))


class TheFlowReportsWhatHappened(unittest.TestCase):

    def _flow(self, helper_result):
        db = MagicMock()
        db.get_installed.return_value = {"name": APP}
        installer = MagicMock()
        installer._find_helper.return_value = "/usr/bin/x"
        installer._run_helper.return_value = helper_result
        with patch("pkm.cli.helper_payload_present", return_value=False), \
             patch("pkm.cli.acceptance_record_exists", return_value=True), \
             patch("pkm.cli.sys.stdin", _Stdin()), \
             patch("builtins.input", return_value="y"), \
             redirect_stdout(io.StringIO()):
            return cli._proprietary_install(
                db, installer, MagicMock(), cli.Reporter.from_args(_install_args()),
                APP, "a license")

    def test_three_outcomes(self):
        self.assertEqual(self._flow(OK), "ok")
        self.assertEqual(self._flow(DECLINED), "declined")
        self.assertEqual(self._flow(FAILED), "failed")


if __name__ == "__main__":
    unittest.main()
