# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""`pkm remove` takes several packages as ONE transaction.

WHY THIS EXISTS. `pkm install`, `pkm reinstall` and `pkm hold` all take a list.
`pkm remove` took exactly one name, so removing three packages meant three
commands — three restore points, three confirmations, and three separate
reverse-dependency checks that could not see each other.

The last one is the part that bit. Removing a library and the one application
that uses it, in either order, was refused: asked to remove the library first,
the check saw the application still installed and said no; asked to remove the
application first, that worked, and then the library worked — so the operation
was possible all along and the tool simply could not see it. The way through
was `--force`, which is the flag that turns the guard OFF entirely, so a person
doing something ordinary was taught to reach for the blunt instrument.

WHAT IS PINNED HERE: several names are accepted; the reverse-dependency check
is computed across the WHOLE set, so a dependency that is itself being removed
does not block; ONE restore point covers the transaction; one confirmation
covers it; and a dependant OUTSIDE the set still blocks, which is the guard
this must not weaken.
"""
from __future__ import annotations

import io
import shutil
import tempfile
import unittest
import contextlib
from pathlib import Path
from types import SimpleNamespace

from pkm.database import PackageDB
from pkm.remover import PackageRemover
from pkm import cli


class _Stdin:
    def __init__(self, tty):
        self._tty = tty

    def isatty(self):
        return self._tty


class MultiRemovalFixture(unittest.TestCase):
    """libfoo <- appfoo (appfoo depends on libfoo); outsider also uses libfoo."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="pkm-remove-many-")
        self.root = Path(self._tmp) / "root"
        self.paths = {}
        for pkg in ("libfoo", "appfoo", "outsider"):
            rel = f"usr/lib/{pkg}/payload"
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(pkg + "\n")
            self.paths[pkg] = rel
        self.db = PackageDB(Path(self._tmp) / "pkm.db", root=str(self.root))
        lib = self.db.add_installed("libfoo", "1.0", release=1, tier="core")
        self.db.add_files(lib, [self.paths["libfoo"]])
        app = self.db.add_installed("appfoo", "1.0", release=1, tier="desktop")
        self.db.add_files(app, [self.paths["appfoo"]])
        self.db.add_depends(app, [("libfoo", "runtime")])

    def _add_outsider(self):
        out = self.db.add_installed("outsider", "1.0", release=1,
                                    tier="desktop")
        self.db.add_files(out, [self.paths["outsider"]])
        self.db.add_depends(out, [("libfoo", "runtime")])

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _args(self, names, **over):
        base = dict(packages=list(names), force=False, remove_dry_run=False,
                    remove_yes=True, verbose=False, quiet=False)
        base.update(over)
        return SimpleNamespace(**base)

    def _run(self, args, tty=False):
        import sys as _sys
        buf, err = io.StringIO(), io.StringIO()
        real = _sys.stdin
        _sys.stdin = _Stdin(tty)
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                try:
                    cli.cmd_remove(self.db, args)
                    code = 0
                except SystemExit as e:
                    code = e.code if isinstance(e.code, int) else 1
        finally:
            _sys.stdin = real
        return code, buf.getvalue() + err.getvalue()

    def _installed(self, name):
        return self.db.get_installed(name) is not None


class TestTheParserTakesAList(unittest.TestCase):

    def test_several_names_are_accepted(self):
        parser = cli.build_parser()
        args = parser.parse_args(["remove", "appfoo", "libfoo"])
        self.assertEqual(list(getattr(args, "packages", [])),
                         ["appfoo", "libfoo"],
                         "`pkm remove` still takes exactly one name, so a "
                         "person removing three packages runs three "
                         "transactions")

    def test_one_name_still_works(self):
        parser = cli.build_parser()
        args = parser.parse_args(["remove", "appfoo"])
        self.assertEqual(list(getattr(args, "packages", [])), ["appfoo"])


class TestTheDependencyCheckSeesTheWholeSet(MultiRemovalFixture):

    def test_removing_a_library_with_its_only_user_is_not_refused(self):
        code, out = self._run(self._args(["libfoo", "appfoo"]))
        self.assertEqual(
            code, 0,
            "removing a library together with the only package that uses it "
            "was refused, so the way through is --force:\n" + out)
        self.assertFalse(self._installed("libfoo"), out)
        self.assertFalse(self._installed("appfoo"), out)

    def test_the_order_the_names_are_given_in_does_not_matter(self):
        code, out = self._run(self._args(["appfoo", "libfoo"]))
        self.assertEqual(code, 0, out)
        self.assertFalse(self._installed("libfoo"), out)
        self.assertFalse(self._installed("appfoo"), out)

    def test_both_payloads_are_gone(self):
        self._run(self._args(["libfoo", "appfoo"]))
        for pkg in ("libfoo", "appfoo"):
            self.assertFalse((self.root / self.paths[pkg]).exists(),
                             f"{pkg}'s payload survived the removal")


class TestAnOutsideDependantStillBlocks(MultiRemovalFixture):
    """The non-masking control: the guard must not have been weakened."""

    def test_a_dependant_not_in_the_set_refuses_the_removal(self):
        self._add_outsider()
        code, out = self._run(self._args(["libfoo", "appfoo"]))
        self.assertNotEqual(
            code, 0,
            "a package outside the removal set still depends on libfoo and "
            "the removal went ahead anyway:\n" + out)
        self.assertTrue(self._installed("libfoo"),
                        "libfoo was removed while outsider still needs it")

    def test_the_refusal_names_the_package_that_still_needs_it(self):
        self._add_outsider()
        _code, out = self._run(self._args(["libfoo", "appfoo"]))
        self.assertIn("outsider", out,
                      "the refusal did not say which package still needs "
                      "it:\n" + out)


class TestOneTransaction(MultiRemovalFixture):

    def test_one_restore_point_covers_the_whole_set(self):
        from pkm import pretxn
        seen = []
        real = pretxn.run_pre_transaction_hook
        pretxn.run_pre_transaction_hook = lambda *a, **k: seen.append(a)
        try:
            self._run(self._args(["libfoo", "appfoo"]))
        finally:
            pretxn.run_pre_transaction_hook = real
        self.assertEqual(len(seen), 1,
                         "each package took its own restore point; a removal "
                         "of three packages would cost three of them")
        self.assertIn("libfoo", seen[0][2])
        self.assertIn("appfoo", seen[0][2])

    def test_one_confirmation_covers_the_whole_set(self):
        asked = []
        real = cli._confirm_remove
        cli._confirm_remove = lambda args, subject: (asked.append(subject)
                                                     or True)
        try:
            self._run(self._args(["libfoo", "appfoo"], remove_yes=False),
                      tty=True)
        finally:
            cli._confirm_remove = real
        self.assertEqual(len(asked), 1,
                         "the person was asked once per package instead of "
                         "once for the transaction")
        self.assertIn("libfoo", asked[0])
        self.assertIn("appfoo", asked[0])


class TestTheWholeSetIsCheckedBeforeAnythingGoes(MultiRemovalFixture):
    """A set that cannot be removed whole loses nothing, and its preview says so.

    Measured 2026-09-22 by the second reader of this change: with a second
    name whose dependant is outside the set, `pkm remove appfoo libfoo --yes`
    removed appfoo, then refused libfoo and exited 1 -- appfoo gone, libfoo
    still installed, two transactions where the help text promises one. The
    same set under --dry-run printed "Already removed before this refusal:
    appfoo" although nothing had been removed.
    """

    def _add_unrelated(self):
        rel = "usr/lib/unrelated/payload"
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("unrelated\n")
        self.paths["unrelated"] = rel
        pkg = self.db.add_installed("unrelated", "1.0", release=1, tier="desktop")
        self.db.add_files(pkg, [rel])

    def test_a_set_with_an_outside_dependant_loses_nothing(self):
        self._add_outsider()
        code, out = self._run(self._args(["appfoo", "libfoo"]))
        self.assertNotEqual(code, 0, out)
        self.assertTrue(self._installed("appfoo"),
                        "appfoo was removed before the set was refused:\n" + out)
        self.assertTrue((self.root / self.paths["appfoo"]).exists(),
                        "appfoo's payload was unlinked before the set was refused")
        self.assertTrue(self._installed("libfoo"), out)
        self.assertIn("outsider", out, out)
        self.assertIn("Nothing was removed", out, out)
        self.assertNotIn("Already removed", out, out)

    def test_the_preview_of_that_set_claims_nothing_was_removed(self):
        self._add_outsider()
        code, out = self._run(self._args(["appfoo", "libfoo"],
                                         remove_dry_run=True, remove_yes=False))
        self.assertNotEqual(code, 0, out)
        self.assertNotIn("Already removed", out,
                         "the preview reported a removal that did not happen:\n" + out)
        self.assertIn("outsider", out, out)
        self.assertIn("Nothing was removed", out, out)
        self.assertTrue(self._installed("appfoo"), out)

    def test_three_names_lose_nothing_and_the_preview_claims_nothing(self):
        self._add_outsider()
        self._add_unrelated()
        code, out = self._run(self._args(["unrelated", "appfoo", "libfoo"]))
        self.assertNotEqual(code, 0, out)
        for name in ("unrelated", "appfoo", "libfoo"):
            self.assertTrue(self._installed(name), f"{name} was removed:\n{out}")
        code, out = self._run(self._args(["unrelated", "appfoo", "libfoo"],
                                         remove_dry_run=True, remove_yes=False))
        self.assertNotEqual(code, 0, out)
        self.assertNotIn("Already removed", out, out)

    def test_a_name_that_is_not_installed_refuses_the_set_before_anything_goes(self):
        code, out = self._run(self._args(["appfoo", "no-such-package"]))
        self.assertNotEqual(code, 0, out)
        self.assertTrue(self._installed("appfoo"),
                        "appfoo was removed before the unknown name refused the set:\n" + out)
        self.assertIn("no-such-package", out, out)

    def test_a_set_that_cannot_be_removed_is_not_asked_about_and_takes_no_restore_point(self):
        from pkm import pretxn
        self._add_outsider()
        asked, points = [], []
        real_confirm, real_hook = cli._confirm_remove, pretxn.run_pre_transaction_hook
        cli._confirm_remove = lambda args, subject: (asked.append(subject) or True)
        pretxn.run_pre_transaction_hook = lambda *a, **k: points.append(a)
        try:
            code, out = self._run(self._args(["appfoo", "libfoo"], remove_yes=False),
                                  tty=True)
        finally:
            cli._confirm_remove, pretxn.run_pre_transaction_hook = real_confirm, real_hook
        self.assertNotEqual(code, 0, out)
        self.assertEqual(asked, [], "the person was asked to confirm a removal "
                                    "that was going to be refused")
        self.assertEqual(points, [], "a restore point was taken for a removal "
                                     "that was going to be refused")


class TestADryRunOverSeveralPackages(MultiRemovalFixture):

    def test_the_preview_covers_every_named_package_and_changes_nothing(self):
        code, out = self._run(
            self._args(["libfoo", "appfoo"], remove_dry_run=True,
                       remove_yes=False))
        self.assertEqual(code, 0, out)
        self.assertIn("libfoo", out, out)
        self.assertIn("appfoo", out, out)
        self.assertTrue(self._installed("libfoo"), out)
        self.assertTrue(self._installed("appfoo"), out)


if __name__ == "__main__":
    unittest.main()
