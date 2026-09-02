#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A download-helper package that arrives as a DEPENDENCY still gets its download.

Measured on the reference laptop on 2026-09-02. The Welcomer's terminal ran
`sudo pkm install nvidia llama-cpp-cuda`. The CUDA engine depends on the CUDA
toolkit, and the toolkit is a download-helper package: the archive on the
mirror is a small installer script, and installing the toolkit means running
that script, which fetches NVIDIA's toolkit after the person accepts NVIDIA's
license. The resolution installed the toolkit's archive as a dependency and
recorded it as installed; the download step was then asked about ONLY for the
package that had been named — the engine — so it never ran. The package
database showed the toolkit installed and `pkm verify cuda-toolkit` passed
(three script files, all present) while /opt/cuda did not exist and the
engine could not start for want of the CUDA libraries.

Three things are asserted here, each against the shipped command code with
its collaborators stood in for:

  1. every package a resolution installs is asked whether it has a download
     step, in install order — not only the one that was named;
  2. `pkm info` says, for a helper package whose download has not run, that
     the application is not installed — the Welcomer reads that line;
  3. `pkm verify` does not certify such a package as ok.
"""

import argparse
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

import pkm.cli as cli


def _index_entry(name, size=1000, installed_size=4000, **extra):
    entry = {"name": name, "version": "1.0", "release": 1,
             "sha256": "a" * 64, "size": size, "installed_size": installed_size}
    entry.update(extra)
    return entry


def _install_args(*packages):
    return argparse.Namespace(
        packages=list(packages), archive=None, archive_trust="strict",
        quiet=False, verbose=False, allow_downgrade=False, assume_yes=True,
    )


ENGINE = "llama-cpp-cuda"
TOOLKIT = "cuda-toolkit"
QUEUE = [TOOLKIT, ENGINE]          # install order: the dependency first


class EveryInstalledHelperGetsItsDownload(unittest.TestCase):

    def _run_install(self):
        """`pkm install llama-cpp-cuda` with the toolkit resolved as a dep."""
        db = MagicMock()
        db.get_installed.return_value = None
        asked = []
        with patch("pkm.cli.PackageInstaller") as Installer, \
             patch("pkm.cli.RepoManager") as Repo, \
             patch("pkm.pretxn.run_pre_transaction_hook"), \
             patch("pkm.cli.refuse_unrunnable_package_hook", return_value=None), \
             patch("pkm.cli.helper_is_present", return_value=False), \
             patch("pkm.cli.refresh_available_updates_after_transaction"), \
             patch("pkm.cli._print_transaction_next_steps"), \
             patch("pkm.cli._continue_into_payload_if_helper",
                   side_effect=lambda db, inst, repo, rep, name: asked.append(name)):
            repo = Repo.return_value
            repo.get_package.side_effect = lambda n: _index_entry(n)
            repo.resolve_dependencies.return_value = (True, list(QUEUE))
            repo.download_package.side_effect = lambda n, reporter=None: (True, f"/tmp/{n}.igos.tar.gz")
            installer = Installer.return_value
            installer._find_archive.return_value = None
            # No cached archive: the first install() attempt reports none,
            # which is what sends the command into resolution.
            installer.install.side_effect = [
                (False, f"No archive found for '{ENGINE}'"),
                (True, "installed"), (True, "installed"),
            ]
            with redirect_stdout(io.StringIO()):
                cli.cmd_install(db, _install_args(ENGINE))
        return asked

    def test_the_dependency_is_asked_about_its_download_step(self):
        asked = self._run_install()
        self.assertIn(TOOLKIT, asked,
                      "the toolkit was installed as a dependency and never "
                      "asked whether it has a download step — its installer "
                      "script landed, the toolkit did not")

    def test_the_requested_package_is_still_asked(self):
        self.assertIn(ENGINE, self._run_install())

    def test_helpers_are_asked_in_install_order(self):
        asked = self._run_install()
        self.assertEqual(asked, QUEUE,
                         "a helper's download must land before anything "
                         "resolved after it is asked")


class InfoSaysWhenTheDownloadHasNotRun(unittest.TestCase):

    def _info(self, helper, payload):
        db = MagicMock()
        db.get_installed.return_value = {
            "name": TOOLKIT, "version": "13.3.1", "release": 3,
            "tier": "compute", "install_date": "2026-09-02T21:17:52+00:00",
            "install_method": "archive",
        }
        db.get_depends.return_value = []
        db.get_reverse_depends.return_value = []
        db.get_files.return_value = []
        buf = io.StringIO()
        with patch("pkm.cli.helper_is_present", return_value=helper), \
             patch("pkm.cli.helper_payload_present", return_value=payload), \
             redirect_stdout(buf):
            cli.cmd_info(db, argparse.Namespace(package=TOOLKIT))
        return buf.getvalue()

    def test_a_helper_without_its_payload_says_not_installed(self):
        out = self._info(helper=True, payload=False)
        line = next((ln for ln in out.splitlines()
                     if ln.strip().startswith("payload ")), None)
        self.assertIsNotNone(line, f"no payload line in:\n{out}")
        self.assertIn("not installed", line)
        self.assertIn(f"sudo pkm install {TOOLKIT}", line)

    def test_a_helper_with_its_payload_gets_no_such_line(self):
        out = self._info(helper=True, payload=True)
        self.assertNotIn("not installed", out)

    def test_an_ordinary_package_gets_no_such_line(self):
        out = self._info(helper=False, payload=False)
        self.assertNotIn("payload", out)


class VerifyDoesNotCertifyAMissingDownload(unittest.TestCase):

    def _verify(self, helper, payload):
        db = MagicMock()
        db.get_installed.return_value = {"name": TOOLKIT, "degraded": None}
        clean = {"missing": [], "modified": [], "unverifiable": [],
                 "undeterminable": [], "total": 3,
                 "expected_absent_by_class": {}, "generated": []}
        buf = io.StringIO()
        raised = None
        with patch("pkm.cli.PackageVerifier") as Verifier, \
             patch("pkm.cli.helper_is_present", return_value=helper), \
             patch("pkm.cli.helper_payload_present", return_value=payload), \
             redirect_stdout(buf):
            Verifier.return_value.verify.return_value = clean
            try:
                cli.cmd_verify(db, argparse.Namespace(
                    package=TOOLKIT, verify_all=False, verify_mode="strict",
                    verify_detail=False, quiet=False, verbose=False))
            except SystemExit as exc:
                raised = exc
        return raised, buf.getvalue()

    def test_intact_script_and_no_payload_is_not_ok(self):
        raised, out = self._verify(helper=True, payload=False)
        self.assertNotIn(": ok (", out,
                         "verify certified the installer script as the "
                         "application")
        self.assertIn("not installed", out)
        self.assertIsNotNone(raised)
        self.assertNotEqual(raised.code, 0)

    def test_an_ordinary_clean_package_is_still_ok(self):
        raised, out = self._verify(helper=False, payload=False)
        self.assertIn(": ok (", out)
        self.assertIsNone(raised)


if __name__ == "__main__":
    unittest.main()
