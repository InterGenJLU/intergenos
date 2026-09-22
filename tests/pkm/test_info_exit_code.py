# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""`pkm info` exits 0 whenever it printed a report, and non-zero only when it
could not find the package at all.

WHY THIS EXISTS. `pkm info <a package the mirror carries but this machine has
not installed>` printed a correct, useful report — the name, the version and
release, the tier, the description, the status line "available, not installed",
and the command that would install it — and then exited 1. A script that asks
"does this machine know about X?" and reads the status is told the query
failed, when it did not: the answer was produced, in full, and is right there
on stdout.

An exit status answers "did the command do its job". Describing a package that
is not installed IS the job: `pkm info` is the verb for asking about a package,
installed or not. The only failure is a name pkm cannot find anywhere — no
installed record and nothing in the index — and that still exits non-zero,
which is the case a script actually needs to distinguish.
"""
from __future__ import annotations

import io
import contextlib
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pkm.database import PackageDB
from pkm import cli


class InfoFixture(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="pkm-info-exit-")
        self.root = Path(self._tmp) / "root"
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = PackageDB(Path(self._tmp) / "pkm.db", root=str(self.root))
        pid = self.db.add_installed("installed-one", "1.0", release=3,
                                    tier="core", description="an installed one")
        self.db.add_files(pid, ["usr/lib/installed-one/payload"])

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _info(self, name, available=None):
        """Run cmd_info with the repository index under our control."""
        args = SimpleNamespace(package=name, verbose=False, quiet=False,
                               json=False)
        repo = SimpleNamespace(get_package=lambda n: available)
        out = io.StringIO()
        with patch.object(cli, "repo_manager", lambda: repo), \
                contextlib.redirect_stdout(out):
            code = cli.cmd_info(self.db, args)
        return (0 if code is None else code), out.getvalue()


class TestAnInstalledPackage(InfoFixture):

    def test_it_exits_zero(self):
        code, said = self._info("installed-one")
        self.assertEqual(code, 0, said)


class TestAPackageTheIndexCarriesButThisMachineHasNot(InfoFixture):

    AVAILABLE = {"name": "available-one", "version": "2.1", "release": 4,
                 "tier": "desktop", "description": "in the index only"}

    def test_the_report_is_printed_in_full(self):
        _code, said = self._info("available-one", available=self.AVAILABLE)
        self.assertIn("available-one", said)
        self.assertIn("available, not installed", said,
                      "the report did not state the status:\n" + said)
        self.assertIn("sudo pkm install available-one", said, said)

    def test_it_exits_zero_because_it_answered_the_question(self):
        code, said = self._info("available-one", available=self.AVAILABLE)
        self.assertEqual(
            code, 0,
            "pkm info printed a correct, complete report and then reported "
            "failure, so a script reading the status is told the query failed "
            "when the answer is on stdout:\n" + said)


class TestANameNothingKnows(InfoFixture):
    """The non-masking control: a real failure must still be a failure."""

    def test_it_exits_non_zero(self):
        code, said = self._info("no-such-package", available=None)
        self.assertNotEqual(
            code, 0,
            "a name pkm cannot find anywhere reported success:\n" + said)

    def test_it_says_so(self):
        _code, said = self._info("no-such-package", available=None)
        self.assertIn("no-such-package", said, said)


if __name__ == "__main__":
    unittest.main()
