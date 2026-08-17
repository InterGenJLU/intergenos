#!/usr/bin/env python3
"""pkm pre-transaction hook (pkm/pretxn.py) + its wiring into the mutating
cmd_install / cmd_upgrade / cmd_remove verbs.

The pre-transaction hook is the new point Chronicle's restore-point layer
depends on: it fires BEFORE a package transaction mutates the live filesystem
(the existing pkm/hooks.py framework fires only after deploy + DB commit).

Coverage:
  - transaction_footprint: outgoing-file paths for installed packages, empty
    for a fresh install, pkm.db path, reason + package de-dup.
  - list_handlers: absent dir -> []; executables sorted; non-executables
    skipped.
  - run_pre_transaction_hook: no handler = no-op (ran=False); a handler runs
    with the footprint JSON on stdin and PKM_TXN_* env; a non-zero handler is a
    loud, non-fatal failure; an unrunnable handler is likewise non-fatal.
  - Wiring: the hook fires before the mutation on all three verbs.
"""

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pkm.repo
import pkm.cli as cli
import pkm.pretxn as pretxn
from pkm.database import PackageDB


class _FakeDB:
    """Duck-typed db for the pure-function tests: get_installed, get_files,
    root — exactly what transaction_footprint consults."""

    def __init__(self, installed, files, root="/"):
        self._installed = installed          # name -> row dict (or absent)
        self._files = files                  # name -> [{"path","is_dir"}]
        self.root = Path(root)

    def get_installed(self, name):
        return self._installed.get(name)

    def get_files(self, name):
        return self._files.get(name, [])


class TransactionFootprintTests(unittest.TestCase):

    def test_installed_package_yields_absolute_outgoing_paths(self):
        db = _FakeDB(
            installed={"nginx": {"name": "nginx"}},
            files={"nginx": [
                {"path": "usr/bin/nginx", "is_dir": False},
                {"path": "etc/nginx/", "is_dir": True},   # dir excluded
                {"path": "etc/nginx/nginx.conf", "is_dir": False},
            ]},
            root="/",
        )
        fp = pretxn.transaction_footprint(db, "remove", ["nginx"], "why")
        self.assertEqual(fp["verb"], "remove")
        self.assertEqual(fp["reason"], "why")
        self.assertEqual(fp["packages"], ["nginx"])
        self.assertEqual(
            fp["paths"], ["/etc/nginx/nginx.conf", "/usr/bin/nginx"]
        )  # sorted, dir dropped
        self.assertEqual(fp["db_path"], "/var/lib/igos/pkm.db")

    def test_fresh_install_has_no_outgoing_paths(self):
        db = _FakeDB(installed={}, files={})
        fp = pretxn.transaction_footprint(db, "install", ["newpkg"], "r")
        self.assertEqual(fp["paths"], [])
        self.assertEqual(fp["packages"], ["newpkg"])
        self.assertTrue(fp["db_path"].endswith("var/lib/igos/pkm.db"))

    def test_paths_honor_db_root(self):
        db = _FakeDB(
            installed={"foo": {"name": "foo"}},
            files={"foo": [{"path": "usr/bin/foo", "is_dir": False}]},
            root="/mnt/target",
        )
        fp = pretxn.transaction_footprint(db, "upgrade", ["foo"], "r")
        self.assertEqual(fp["paths"], ["/mnt/target/usr/bin/foo"])
        self.assertEqual(fp["db_path"], "/mnt/target/var/lib/igos/pkm.db")

    def test_packages_deduped_preserving_order(self):
        db = _FakeDB(installed={}, files={})
        fp = pretxn.transaction_footprint(
            db, "install", ["a", "b", "a", "c"], "r"
        )
        self.assertEqual(fp["packages"], ["a", "b", "c"])


class ListHandlersTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / "pre-transaction.d"

    def tearDown(self):
        self._tmp.cleanup()

    def test_absent_dir_returns_empty(self):
        self.assertEqual(pretxn.list_handlers(self.dir), [])

    def test_executables_sorted_nonexec_skipped(self):
        self.dir.mkdir()
        exe_b = self.dir / "b-handler"
        exe_a = self.dir / "a-handler"
        plain = self.dir / "notes.txt"
        for p in (exe_a, exe_b):
            p.write_text("#!/bin/sh\n")
            p.chmod(0o755)
        plain.write_text("not executable")
        plain.chmod(0o644)
        handlers = pretxn.list_handlers(self.dir)
        self.assertEqual([h.name for h in handlers], ["a-handler", "b-handler"])


def _write_handler(directory, name, body):
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / name
    p.write_text(body)
    p.chmod(0o755)
    return p


class RunPreTransactionHookTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.hdir = self.tmp / "pre-transaction.d"
        self.db = _FakeDB(
            installed={"nginx": {"name": "nginx"}},
            files={"nginx": [{"path": "usr/sbin/nginx", "is_dir": False}]},
            root="/",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_handler_is_noop(self):
        res = pretxn.run_pre_transaction_hook(
            self.db, "remove", ["nginx"], "r", handler_dir=self.hdir
        )
        self.assertFalse(res.ran)
        self.assertEqual(res.handlers, [])
        self.assertEqual(res.failures, [])

    def test_handler_receives_footprint_on_stdin_and_env(self):
        capture = self.tmp / "captured.json"
        env_out = self.tmp / "env.txt"
        _write_handler(
            self.hdir, "chronicle-restore-point",
            f"#!/bin/bash\ncat > {capture}\n"
            f'echo "$PKM_TXN_VERB $PKM_TXN_REASON" > {env_out}\nexit 0\n',
        )
        res = pretxn.run_pre_transaction_hook(
            self.db, "remove", ["nginx"], "pre-transaction remove: nginx",
            handler_dir=self.hdir,
        )
        self.assertTrue(res.ran)
        self.assertEqual(res.failures, [])
        self.assertEqual(res.handlers, ["chronicle-restore-point"])
        footprint = json.loads(capture.read_text())
        self.assertEqual(footprint["verb"], "remove")
        self.assertEqual(footprint["paths"], ["/usr/sbin/nginx"])
        self.assertEqual(footprint["reason"], "pre-transaction remove: nginx")
        self.assertEqual(
            env_out.read_text().strip(),
            "remove pre-transaction remove: nginx",
        )

    def test_failing_handler_is_loud_but_nonfatal(self):
        _write_handler(
            self.hdir, "bad-handler",
            "#!/bin/bash\necho 'target volume vanished' >&2\nexit 1\n",
        )
        # Must NOT raise, and must record the failure.
        res = pretxn.run_pre_transaction_hook(
            self.db, "upgrade", ["nginx"], "r", handler_dir=self.hdir
        )
        self.assertTrue(res.ran)
        self.assertEqual(res.failures, ["bad-handler"])
        self.assertTrue(any("WARNING" in m for m in res.messages))

    def test_unrunnable_handler_is_nonfatal(self):
        # A file marked executable but not a valid program: exec failure path.
        p = _write_handler(self.hdir, "not-a-program", "definitely not a script")
        # Strip the shebang-less content's interpreter chance by making it
        # non-parseable; OSError/ENOEXEC is caught.
        res = pretxn.run_pre_transaction_hook(
            self.db, "install", ["nginx"], "r", handler_dir=self.hdir
        )
        # Either a non-zero exit or an exec error — both are non-fatal failures.
        self.assertTrue(res.ran)
        self.assertIn("not-a-program", res.failures)


# ---------------------------------------------------------------------------
# Wiring: the hook fires before the mutation on all three verbs
# ---------------------------------------------------------------------------


class _WiringHarness(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db_root = self.tmp / "root"
        self.db_root.mkdir()
        self.db = PackageDB(
            db_path=str(self.tmp / "pkm.db"), root=str(self.db_root)
        )
        self.order = []

    def tearDown(self):
        self._tmp.cleanup()

    def _record_hook(self, *a, **k):
        self.order.append(("hook", k.get("reason") or (a[3] if len(a) > 3 else None)))
        return pretxn.PreTxnResult(ran=False, handlers=[], failures=[], messages=[])


class RemoveWiringTests(_WiringHarness):

    def test_hook_fires_before_remove(self):
        self.db.add_installed("foo", "1.0", release=1, tier="core")
        args = SimpleNamespace(package="foo", force=True, quiet=False,
                               verbose=False)

        def record_remove(self_remover, name, **k):
            self.order.append(("remove", name))
            return (True, "removed")

        with patch.object(pretxn, "run_pre_transaction_hook",
                          side_effect=self._record_hook), \
             patch("pkm.remover.PackageRemover.remove",
                   side_effect=record_remove, autospec=True):
            cli.cmd_remove(self.db, args)

        self.assertEqual([step[0] for step in self.order], ["hook", "remove"])


class InstallWiringTests(_WiringHarness):

    def test_hook_fires_on_install_verb(self):
        # Empty package set: the hook still fires (before the no-op loop),
        # proving the wiring without needing a full repo/installer stack.
        args = SimpleNamespace(packages=[], archive=None, quiet=False,
                               verbose=False)

        class _FakeInstaller:
            def __init__(self, db):
                pass

        class _FakeRepo:
            def __init__(self):
                pass

        with patch.object(pretxn, "run_pre_transaction_hook",
                          side_effect=self._record_hook), \
             patch.object(cli, "PackageInstaller", _FakeInstaller), \
             patch.object(cli, "RepoManager", _FakeRepo):
            cli.cmd_install(self.db, args)

        self.assertEqual(len(self.order), 1)
        self.assertEqual(self.order[0][0], "hook")


class UpgradeWiringTests(_WiringHarness):

    def test_hook_fires_before_upgrade_mutation(self):
        self.db.add_installed("foo", "1.0", release=1, tier="core")
        dl_path = self.tmp / "foo-2.0-1.igos.tar.gz"
        dl_path.write_bytes(b"archive")
        remote = {"name": "foo", "version": "2.0", "release": 1,
                  "sha256": "a" * 64, "depends": [], "size": 0}

        def record_install(name, **k):
            self.order.append(("install", name))
            return (True, "ok")

        class FakeRepo:
            def __init__(fr):
                pass

            def get_package(fr, name):
                return remote if name == "foo" else None

            def download_package(fr, name):
                return True, str(dl_path)

            def resolve_dependencies(fr, name, db):
                return True, []

        args = SimpleNamespace(
            packages=[], upgrade_all=True, allow_downgrade=False,
            ignore_holds=False, upgrade_security_only=False,
            upgrade_dry_run=False, upgrade_yes=True,
            upgrade_allow_kernel_replace=False,
        )
        with patch.object(pretxn, "run_pre_transaction_hook",
                          side_effect=self._record_hook), \
             patch.object(cli, "RepoManager", FakeRepo), \
             patch.object(cli.PackageInstaller, "install",
                          side_effect=record_install, autospec=False), \
             patch.object(pkm.repo, "REPO_PKG_CACHE", self.tmp / "cache"), \
             patch.object(pkm.repo, "REPO_ROLLBACK_DIR", self.tmp / "rb"), \
             patch("pkm.remover.PackageRemover.remove",
                   return_value=(True, "removed")):
            (self.tmp / "cache").mkdir()
            cli.cmd_upgrade(self.db, args)

        # The hook must appear before the first install mutation.
        self.assertIn(("hook", self.order[0][1]), [self.order[0]])
        self.assertEqual(self.order[0][0], "hook")
        self.assertIn(("install", "foo"), self.order)
        self.assertLess(
            self.order.index(("hook", self.order[0][1])),
            self.order.index(("install", "foo")),
        )


class PreTxnHandlerDirIsolationTests(unittest.TestCase):
    """PI-234: a test run must never execute the live machine's handlers.

    pkm's mutating verbs call run_pre_transaction_hook with no handler_dir, so
    it falls back to PRETXN_HANDLER_DIR. On an installed system that directory
    holds the backup engine's restore-point handler, and running it from a test
    takes a real state-changing action against the running engine — the path
    that raises interactive authentication on a desktop session. The project
    conftest redirects the constant to an empty throwaway directory for the
    whole run; these assertions are what keeps that redirect from being
    silently lost.
    """

    LIVE_DIR = Path("/usr/lib/pkm/pre-transaction.d")

    def test_default_handler_dir_is_not_the_live_drop_in_dir(self):
        self.assertNotEqual(Path(pretxn.PRETXN_HANDLER_DIR), self.LIVE_DIR)

    def test_default_handler_dir_holds_no_handlers(self):
        # The fallback path must be a no-op: whatever the machine has
        # installed, the default lookup finds nothing to execute.
        self.assertEqual(pretxn.list_handlers(), [])


if __name__ == "__main__":
    sys.exit(unittest.main())
