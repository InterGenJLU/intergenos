# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""`pkm remove` asks before it removes, and refuses rather than assume.

WHY THIS EXISTS. `pkm upgrade` has had the rule for a long time: with no
terminal attached and no --yes, it stops and says so, naming the flag that
would let it proceed and the flag that would let you preview instead. It never
guesses that silence meant yes. `pkm remove` had no confirmation of any kind —
it removed, immediately, whether or not anybody was there to see it. That is
backwards: removal is the destructive direction, and it is the one that can
unlink a file the package cannot put back.

WHAT IS PINNED HERE:
  * --yes is accepted and proceeds without asking.
  * No terminal and no --yes is a REFUSAL that names both --yes and --dry-run,
    and it happens before anything is unlinked.
  * With a terminal, the person is asked, and answering anything but yes
    leaves the machine untouched.
  * --dry-run needs no confirmation: a preview changes nothing, so there is
    nothing to confirm.
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
from pkm import cli


class _FakeStdin:
    def __init__(self, tty, answer=""):
        self._tty = tty
        self._answer = answer

    def isatty(self):
        return self._tty


class RemoveConfirmationFixture(unittest.TestCase):

    PAYLOAD = "usr/libexec/example/helper"

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="pkm-remove-confirm-")
        self.root = Path(self._tmp) / "root"
        p = self.root / self.PAYLOAD
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("payload\n")
        self.db = PackageDB(Path(self._tmp) / "pkm.db", root=str(self.root))
        pid = self.db.add_installed("example", "1.0", release=1, tier="desktop")
        self.db.add_files(pid, [self.PAYLOAD])

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _args(self, **over):
        base = dict(package="example", force=False, remove_dry_run=False,
                    remove_yes=False, verbose=False, quiet=False)
        base.update(over)
        return SimpleNamespace(**base)

    def _run(self, args, tty, answer=""):
        """Drive cmd_remove with stdin's tty state under our control."""
        import sys as _sys
        buf, err = io.StringIO(), io.StringIO()
        real_stdin, real_input = _sys.stdin, cli.input if hasattr(cli, "input") else None
        _sys.stdin = _FakeStdin(tty, answer)
        import builtins
        real_builtin_input = builtins.input
        builtins.input = lambda *a: answer
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                try:
                    cli.cmd_remove(self.db, args)
                    code = 0
                except SystemExit as e:
                    code = e.code if isinstance(e.code, int) else 1
        finally:
            _sys.stdin = real_stdin
            builtins.input = real_builtin_input
        return code, buf.getvalue() + err.getvalue()

    def _still_installed(self):
        return self.db.get_installed("example") is not None


class TestTheFlagExists(unittest.TestCase):

    def test_remove_accepts_yes(self):
        parser = cli.build_parser()
        args = parser.parse_args(["remove", "example", "--yes"])
        self.assertTrue(getattr(args, "remove_yes", False),
                        "`pkm remove --yes` is not accepted, so there is no "
                        "way to confirm a removal non-interactively")

    def test_remove_accepts_the_short_spelling(self):
        parser = cli.build_parser()
        args = parser.parse_args(["remove", "example", "-y"])
        self.assertTrue(getattr(args, "remove_yes", False))


class TestNoTerminalAndNoYesIsARefusal(RemoveConfirmationFixture):

    def test_it_exits_non_zero(self):
        code, out = self._run(self._args(), tty=False)
        self.assertNotEqual(code, 0,
                            "a removal with nobody watching and no --yes "
                            "proceeded silently:\n" + out)

    def test_nothing_was_removed(self):
        self._run(self._args(), tty=False)
        self.assertTrue((self.root / self.PAYLOAD).is_file(),
                        "the refusal came after the payload was unlinked")
        self.assertTrue(self._still_installed())

    def test_the_refusal_names_both_flags(self):
        _code, out = self._run(self._args(), tty=False)
        self.assertIn("--yes", out,
                      "the refusal did not name the flag that would let it "
                      "proceed:\n" + out)
        self.assertIn("--dry-run", out,
                      "the refusal did not name the flag that would let the "
                      "person preview instead:\n" + out)


class TestYesProceeds(RemoveConfirmationFixture):

    def test_with_yes_and_no_terminal_the_package_is_removed(self):
        code, out = self._run(self._args(remove_yes=True), tty=False)
        self.assertEqual(code, 0, out)
        self.assertFalse((self.root / self.PAYLOAD).exists(), out)
        self.assertFalse(self._still_installed(), out)


class TestWithATerminalThePersonIsAsked(RemoveConfirmationFixture):

    def test_answering_no_leaves_the_machine_untouched(self):
        code, out = self._run(self._args(), tty=True, answer="n")
        self.assertEqual(code, 0, out)
        self.assertTrue((self.root / self.PAYLOAD).is_file(),
                        "answering no still removed the package:\n" + out)
        self.assertTrue(self._still_installed())

    def test_answering_yes_removes_it(self):
        code, out = self._run(self._args(), tty=True, answer="y")
        self.assertEqual(code, 0, out)
        self.assertFalse((self.root / self.PAYLOAD).exists(), out)
        self.assertFalse(self._still_installed(), out)

    def test_the_default_is_no(self):
        """Empty answer = decline. The destructive direction defaults to no."""
        code, out = self._run(self._args(), tty=True, answer="")
        self.assertEqual(code, 0, out)
        self.assertTrue((self.root / self.PAYLOAD).is_file(),
                        "pressing Return removed the package:\n" + out)


class TestAPreviewNeedsNoConfirmation(RemoveConfirmationFixture):

    def test_dry_run_on_a_non_tty_is_not_refused(self):
        code, out = self._run(
            self._args(remove_dry_run=True), tty=False)
        self.assertEqual(code, 0,
                         "a preview was refused for want of a confirmation "
                         "it does not need:\n" + out)
        self.assertIn("would unlink", out, out)
        self.assertTrue(self._still_installed())


class TestTheOrderOfTheGate(RemoveConfirmationFixture):
    """A declined removal must cost the machine nothing at all.

    The restore point is disk: capturing one for a removal the person then
    declines charges them for changing their mind. So the question comes
    first, and the refusal path never reaches the hook either.
    """

    def _record_pretxn(self):
        from pkm import pretxn
        seen = []
        real = pretxn.run_pre_transaction_hook
        pretxn.run_pre_transaction_hook = lambda *a, **k: seen.append(a)
        return seen, (lambda: setattr(pretxn, "run_pre_transaction_hook", real))

    def test_declining_takes_no_restore_point(self):
        seen, restore = self._record_pretxn()
        try:
            self._run(self._args(), tty=True, answer="n")
        finally:
            restore()
        self.assertEqual(seen, [],
                         "declining a removal still took a restore point")

    def test_a_refused_removal_takes_no_restore_point(self):
        seen, restore = self._record_pretxn()
        try:
            self._run(self._args(), tty=False)
        finally:
            restore()
        self.assertEqual(seen, [],
                         "a removal refused for want of a terminal still took "
                         "a restore point")

    def test_a_confirmed_removal_does_take_one(self):
        """The non-masking control: the restore point still happens."""
        seen, restore = self._record_pretxn()
        try:
            code, out = self._run(self._args(remove_yes=True), tty=False)
        finally:
            restore()
        self.assertEqual(code, 0, out)
        self.assertEqual(len(seen), 1,
                         "a confirmed removal skipped its restore point")


if __name__ == "__main__":
    unittest.main()
