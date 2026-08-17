#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""PKM-A19 regression: reinstalling a download-helper re-fetches the payload.

_proprietary_install tells the user `pkm reinstall <app>` to "replace" a
proprietary-download app (vscode/chrome/...). But cmd_reinstall ran the generic
acquire+remove(force=True)+install on the STUB archive — re-acquiring only the
stub, removing the package (deleting the downloaded payload), and laying the
stub back: the opposite of "replace".

Fixed: cmd_reinstall detects is_download_helper and routes through
_proprietary_install(replace=True), which skips the "already installed" refusal
and RE-RUNS the helper to re-fetch the payload.
"""

import argparse
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch, MagicMock

import pkm.cli as cli


def _args(*packages):
    return argparse.Namespace(packages=list(packages), quiet=True, verbose=False)


class ReinstallHelperRoutingTest(unittest.TestCase):

    def test_helper_reinstall_routes_to_proprietary_replace(self):
        db = MagicMock()
        db.get_installed.return_value = {"name": "vscode", "version": "1.0"}
        with patch("pkm.cli.is_download_helper", return_value=True), \
             patch("pkm.cli._proprietary_install") as prop, \
             patch("pkm.cli.PackageInstaller"), \
             patch("pkm.cli.PackageRemover") as Remover, \
             patch("pkm.cli.RepoManager") as Repo:
            Repo.return_value.get_package.return_value = {
                "payload_license": "MS-EULA"}
            cli.cmd_reinstall(db, _args("vscode"))
        prop.assert_called_once()
        # routed with replace=True...
        self.assertTrue(prop.call_args.kwargs.get("replace"))
        # ...and the DESTRUCTIVE generic remove was NOT used (the A19 bug).
        Remover.return_value.remove.assert_not_called()

    def test_non_helper_reinstall_uses_generic_not_proprietary(self):
        db = MagicMock()
        db.get_installed.return_value = {"name": "bash", "version": "5.2"}
        with patch("pkm.cli.is_download_helper", return_value=False), \
             patch("pkm.cli._proprietary_install") as prop, \
             patch("pkm.cli.PackageInstaller") as Installer, \
             patch("pkm.cli.PackageRemover") as Remover, \
             patch("pkm.cli._sha256", return_value="deadbeef"), \
             patch("pkm.cli.RepoManager") as Repo:
            Installer.return_value._find_archive.return_value = Path(
                "/var/lib/igos/archives/bash-5.2.igos.tar.gz")
            # S5-1: the cached archive is trusted only when its sha256 matches
            # the signed index — mock a matching index entry so reinstall uses
            # the cache and proceeds to the generic remove+install.
            Repo.return_value.get_package.return_value = {"sha256": "deadbeef"}
            Remover.return_value.remove.return_value = (True, "removed")
            Installer.return_value.install.return_value = (True, "installed")
            cli.cmd_reinstall(db, _args("bash"))
        prop.assert_not_called()
        Remover.return_value.remove.assert_called_once()


class ProprietaryReplaceGuardTest(unittest.TestCase):

    def _mocks(self):
        db = MagicMock()
        db.get_installed.return_value = {"name": "vscode", "version": "1.0"}
        return db, MagicMock(), MagicMock(), MagicMock()

    def test_replace_false_refuses_when_payload_installed(self):
        db, installer, repo, reporter = self._mocks()
        with patch("pkm.cli.payload_installed", return_value=True):
            cli._proprietary_install(db, installer, repo, reporter, "vscode",
                                     "MS-EULA", replace=False)
        msgs = " ".join(str(c.args[0]) for c in reporter.info.call_args_list
                        if c.args)
        self.assertIn("already installed", msgs)
        installer._run_helper.assert_not_called()

    def test_replace_true_skips_guard_and_proceeds(self):
        db, installer, repo, reporter = self._mocks()
        stdin = MagicMock()
        stdin.isatty.return_value = True
        with patch("pkm.cli.payload_installed", return_value=True), \
             patch("pkm.cli.sys.stdin", stdin), \
             patch("builtins.input", return_value="n"):
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli._proprietary_install(db, installer, repo, reporter, "vscode",
                                         "MS-EULA", replace=True)
        # Did NOT refuse with "already installed" — it got past the guard to the
        # EULA pause (we answered 'n' to cancel cleanly, proving the guard was
        # skipped without running a real helper download).
        msgs = " ".join(str(c.args[0]) for c in reporter.info.call_args_list
                        if c.args)
        self.assertNotIn("already installed", msgs)
        # A27: the EULA-pause cancellation now routes through reporter.info
        # (the wrapped-prose surface), not a raw print() to stdout.
        self.assertIn("cancelled", msgs.lower())


if __name__ == "__main__":
    unittest.main()
