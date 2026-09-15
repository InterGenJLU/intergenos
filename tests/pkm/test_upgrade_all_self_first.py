#!/usr/bin/env python3
"""`pkm upgrade --all` upgrades the package manager first and re-executes
under the new release for the rest of the queue (R001.3 walk row S1-5).

What went wrong (2026-09-03): a queue that carried pkm itself ran in ONE
process in alphabetical order, so cuda-toolkit came before pkm and the OLD
code ran the download helper's destructive step.

What these tests pin:
  * the re-execution argument vector: the interpreter with -P (the current
    directory never on the module path), the package by module name,
    `upgrade --all --yes`, and every queue-shaping flag of the original call
    carried across — nothing else;
  * the re-execution itself: os.execve with that vector and an environment
    carrying PKM_CONTINUED_AFTER_SELF_UPGRADE naming the release move, the
    database closed first; an exec that fails is a loud error that says pkm
    IS upgraded and the rest was NOT touched, exit 1;
  * through the real command line (dry run, non-root — the same harness the
    dry-run tests use): a queue holding pkm and another package puts pkm
    FIRST and says so; a continuation that still finds pkm upgradable
    refuses to loop; a queue of pkm alone announces no re-execution.
"""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pkm import cli  # noqa: E402
from pkm.database import PackageDB  # noqa: E402

CONTINUATION_ENV = "PKM_CONTINUED_AFTER_SELF_UPGRADE"


def _args(**flags):
    fields = {"verbose": False, "quiet": False,
              "upgrade_allow_kernel_replace": False,
              "upgrade_security_only": False, "ignore_holds": False,
              "allow_downgrade": False}
    fields.update(flags)
    return SimpleNamespace(**fields)


class ReexecArgvTest(unittest.TestCase):
    def test_minimal_vector(self):
        argv = cli._upgrade_all_reexec_argv(_args(), python="/usr/bin/python3")
        self.assertEqual(argv, ["/usr/bin/python3", "-P", "-m", "pkm",
                                "upgrade", "--all", "--yes"])

    def test_queue_shaping_flags_are_carried(self):
        argv = cli._upgrade_all_reexec_argv(
            _args(verbose=True, upgrade_allow_kernel_replace=True,
                  upgrade_security_only=True, ignore_holds=True,
                  allow_downgrade=True), python="py")
        self.assertEqual(argv[:4], ["py", "-P", "-m", "pkm"])
        self.assertIn("-v", argv)
        self.assertEqual(argv[argv.index("upgrade"):argv.index("upgrade") + 3],
                         ["upgrade", "--all", "--yes"])
        for flag in ("--allow-kernel-replace", "--security-only",
                     "--ignore-holds", "--allow-downgrade"):
            self.assertIn(flag, argv)
        self.assertNotIn("--dry-run", argv)

    def test_the_interpreter_defaults_to_the_running_one(self):
        argv = cli._upgrade_all_reexec_argv(_args())
        self.assertEqual(argv[0], sys.executable)
        self.assertEqual(argv[1], "-P")


class ReexecTest(unittest.TestCase):
    def setUp(self):
        self.installed = {"name": "pkm", "version": "0.2.0", "release": 76}
        self.remote = {"name": "pkm", "version": "0.2.0", "release": 77}
        self.closed = []
        self.db = SimpleNamespace(close=lambda: self.closed.append(True))

    def test_execve_carries_the_vector_and_the_marker(self):
        seen = {}

        def fake_execve(path, argv, env):
            seen["path"], seen["argv"], seen["env"] = path, argv, env
            raise SystemExit(0)  # a real exec never returns

        out, err = io.StringIO(), io.StringIO()
        with patch.object(os, "execve", fake_execve), \
                redirect_stdout(out), redirect_stderr(err), \
                self.assertRaises(SystemExit):
            cli._reexec_upgrade_all_under_new_pkm(
                _args(), self.installed, self.remote, self.db, remaining=3)
        self.assertEqual(seen["path"], sys.executable)
        self.assertEqual(seen["argv"][:4], [sys.executable, "-P", "-m", "pkm"])
        self.assertIn("--yes", seen["argv"])
        self.assertIn(CONTINUATION_ENV, seen["env"])
        self.assertIn("76", seen["env"][CONTINUATION_ENV])
        self.assertIn("77", seen["env"][CONTINUATION_ENV])
        self.assertEqual(self.closed, [True])
        self.assertIn("Re-executing", out.getvalue() + err.getvalue())
        self.assertIn("3 package(s)", out.getvalue() + err.getvalue())

    def test_a_failed_exec_is_a_loud_error_naming_what_stands(self):
        def fake_execve(path, argv, env):
            raise OSError(13, "Permission denied")

        out, err = io.StringIO(), io.StringIO()
        with patch.object(os, "execve", fake_execve), \
                redirect_stdout(out), redirect_stderr(err), \
                self.assertRaises(SystemExit) as cm:
            cli._reexec_upgrade_all_under_new_pkm(
                _args(), self.installed, self.remote, self.db, remaining=2)
        self.assertEqual(cm.exception.code, 1)
        text = out.getvalue() + err.getvalue()
        self.assertIn("pkm itself IS upgraded", text)
        self.assertIn("were NOT touched", text)
        self.assertIn("remaining 2", text)
        self.assertIn("pkm upgrade --all", text)


class _FakeRepo:
    def __init__(self, remotes):
        self._remotes = remotes

    def has_synced_index(self):
        return True

    def get_package(self, name):
        return self._remotes.get(name)


class QueueOrderThroughTheCommandLineTest(unittest.TestCase):
    """The real `upgrade --all --dry-run` path (non-root), which reaches the
    queue ordering and prints the plan without installing anything."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dbpath = Path(self.tmp.name) / "pkm.db"
        db = PackageDB(str(self.dbpath))
        db.add_installed("pkm", "0.2.0", release=76, tier="core")
        db.add_installed("zlib", "1.3.1", release=1, tier="core")
        db.add_installed("acl", "2.3.2", release=1, tier="core")
        db.close()

    def _run(self, remotes, env=None):
        argv = ["pkm", "--db", str(self.dbpath), "upgrade", "--all", "--dry-run"]
        out, err = io.StringIO(), io.StringIO()
        rc = 0
        environ = dict(os.environ)
        environ.pop(CONTINUATION_ENV, None)
        environ.update(env or {})
        with patch.object(sys, "argv", argv), \
                patch("os.geteuid", return_value=1000), \
                patch.dict(os.environ, environ, clear=True), \
                patch("pkm.cli.RepoManager", return_value=_FakeRepo(remotes)), \
                redirect_stdout(out), redirect_stderr(err):
            try:
                cli.main()
            except SystemExit as e:
                rc = e.code or 0
        return rc, out.getvalue() + err.getvalue()

    def _remotes(self, *names):
        table = {"pkm": {"name": "pkm", "version": "0.2.0", "release": 77},
                 "zlib": {"name": "zlib", "version": "1.3.1", "release": 2},
                 "acl": {"name": "acl", "version": "2.3.2", "release": 2}}
        return {n: table[n] for n in names}

    def test_pkm_goes_first_and_the_plan_says_so(self):
        rc, text = self._run(self._remotes("acl", "pkm", "zlib"))
        self.assertNotIn("must be run as root", text)
        self.assertIn("pkm is in this queue: it upgrades FIRST", text)
        self.assertIn("remaining 2 package(s)", text)
        plan = text[text.index("Upgrade plan"):]
        self.assertLess(plan.index("pkm "), plan.index("acl "))
        self.assertLess(plan.index("pkm "), plan.index("zlib "))

    def test_pkm_alone_announces_no_reexecution(self):
        rc, text = self._run(self._remotes("pkm"))
        self.assertNotIn("re-runs itself", text)
        self.assertNotIn("Continuing the upgrade", text)

    def test_a_queue_without_pkm_is_untouched(self):
        rc, text = self._run(self._remotes("acl", "zlib"))
        self.assertNotIn("pkm is in this queue", text)

    def test_a_continuation_still_finding_pkm_refuses_to_loop(self):
        rc, text = self._run(self._remotes("pkm", "zlib"),
                             env={CONTINUATION_ENV: "0.2.0-76 -> 0.2.0-77"})
        self.assertEqual(rc, 1)
        self.assertIn("refusing to loop", text)
        self.assertIn("Continuing the upgrade under the new package manager", text)

    def test_a_continuation_without_pkm_in_the_queue_proceeds(self):
        rc, text = self._run(self._remotes("zlib"),
                             env={CONTINUATION_ENV: "0.2.0-76 -> 0.2.0-77"})
        self.assertNotEqual(rc, 1)
        self.assertIn("Continuing the upgrade under the new package manager", text)
        self.assertNotIn("refusing to loop", text)


if __name__ == "__main__":
    unittest.main()
