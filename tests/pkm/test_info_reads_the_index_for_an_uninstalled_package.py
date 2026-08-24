# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""`pkm info` answers from the repository index when a package is not installed.

WHY THIS FILE EXISTS. The package-management guide tells a reader that
`pkm info <name>` shows a package's metadata, and uses an available-but-
uninstalled package as its worked example. On an installed R001.1 system the
command answered "Package '<name>' is not installed" and stopped, for every
package the machine did not already have — which is every package a person
would run `info` on while deciding whether to install it.

The information was not missing. `pkm search` for the same name printed its
version, its tier and its description in the same session, from the same cached
repository index, because search consults the index and info did not. The
command was refusing to read a source it already had open.

WHAT THIS PINS.

  * an uninstalled package that the index knows is described, not dismissed;
  * the answer says plainly that it is NOT installed, so the fallback can never
    be misread as an installed-package report;
  * an installed package still gets the full installed report, including the
    fields the index cannot supply (install date, files, reverse dependencies);
  * a name that is in neither place still gets the plain "not installed"
    answer, and no index lookup can turn a missing package into a hit;
  * a broken or unsynced index degrades to the old message rather than raising.

Nothing here writes to the tree, reads the network, or needs privilege: the
installed database and the repository index are both replaced with stand-ins, so
the result cannot differ between a developer box and an installed machine.
"""
import io
import unittest
import unittest.mock
from contextlib import redirect_stdout

from pkm import cli


class _Args:
    def __init__(self, package):
        self.package = package


class _FakeDB:
    """The installed database, with only what cmd_info asks of it."""

    def __init__(self, installed=None):
        self._installed = installed or {}

    def get_installed(self, name):
        return self._installed.get(name)

    def get_depends(self, name):
        return []

    def get_reverse_depends(self, name):
        return []

    def get_files(self, name):
        return []


_INDEX_RECORD = {
    "name": "a-package-this-box-does-not-have",
    "version": "9.0.4",
    "release": 1,
    "tier": "extra",
    "description": "High-performance in-memory KV store",
    "license": "BSD-3-Clause",
}


def _run_info(package, index_record):
    """Drive cmd_info with a stand-in index and return everything it printed."""
    repo = unittest.mock.Mock()
    repo.get_package.return_value = index_record
    repo.has_synced_index.return_value = True
    buf = io.StringIO()
    with unittest.mock.patch.object(cli, "RepoManager", return_value=repo):
        with redirect_stdout(buf):
            cli.cmd_info(_FakeDB(), _Args(package))
    return buf.getvalue()


class InfoFallsBackToTheIndexTest(unittest.TestCase):

    def test_an_uninstalled_package_the_index_knows_is_described(self):
        out = _run_info(_INDEX_RECORD["name"], _INDEX_RECORD)
        self.assertIn("9.0.4", out)
        self.assertIn("extra", out)
        self.assertIn("High-performance in-memory KV store", out)

    def test_the_answer_still_says_the_package_is_not_installed(self):
        out = _run_info(_INDEX_RECORD["name"], _INDEX_RECORD)
        self.assertIn("not installed", out.lower(), (
            "the index answer must state that the package is not installed; "
            "without that line it reads as an installed-package report"))

    def test_a_name_in_neither_place_gets_the_plain_answer(self):
        out = _run_info("no-such-package-anywhere", None)
        self.assertIn("not installed", out.lower())
        self.assertNotIn("tier", out.lower())

    def test_a_broken_index_degrades_to_the_plain_answer(self):
        repo = unittest.mock.Mock()
        repo.get_package.side_effect = OSError("index unreadable")
        buf = io.StringIO()
        with unittest.mock.patch.object(cli, "RepoManager", return_value=repo):
            with redirect_stdout(buf):
                cli.cmd_info(_FakeDB(), _Args("anything"))
        self.assertIn("not installed", buf.getvalue().lower())

    def test_an_installed_package_still_gets_the_installed_report(self):
        installed = {
            "sqlite": {
                "name": "sqlite", "version": "3510200", "release": 3,
                "tier": "core", "description": "SQL database engine",
                "license": "LicenseRef-Public-Domain",
                "install_date": "2026-08-22T12:45:18",
                "install_method": "archive",
            }
        }
        repo = unittest.mock.Mock()
        buf = io.StringIO()
        with unittest.mock.patch.object(cli, "RepoManager", return_value=repo):
            with redirect_stdout(buf):
                cli.cmd_info(_FakeDB(installed), _Args("sqlite"))
        out = buf.getvalue()
        self.assertIn("install_date", out)
        self.assertIn("Files:", out)
        self.assertNotIn("not installed", out.lower())
        repo.get_package.assert_not_called()


if __name__ == "__main__":
    unittest.main()
