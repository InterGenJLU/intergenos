#!/usr/bin/env python3
"""pkm upgrade-path ordering + kernel-replace gate.

Two changes under test:

  Change 1 — the kernel-replace gate, relocated before the disk preflight +
  plan summary and rewritten so `pkm upgrade --all` EXCLUDES linux-kernel with
  a loud notice and proceeds with the rest of the queue, while a NAMED
  `pkm upgrade linux-kernel` without --allow-kernel-replace still hard-refuses
  (exit 1).

  Change 2 — _topological_upgrade_order: each package's in-queue runtime deps
  upgrade before it (alphabetical tiebreak within a rank); an authorized kernel
  replacement is forced last; a dependency cycle degrades to alphabetical order
  within the cycle group and is reported.

The kernel-gate + topo-integration cases drive cmd_upgrade with a recording
installer, modeled on test_upgrade_rehash_threading.py.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pkm.repo
import pkm.cli as cli
from pkm.cli import _topological_upgrade_order, cmd_upgrade
from pkm.database import PackageDB


# ---------------------------------------------------------------------------
# _topological_upgrade_order — pure-function properties
# ---------------------------------------------------------------------------


def _pair(name, depends=None, version="2.0"):
    """Build one (installed_pkg, remote_pkg) queue entry."""
    return (
        {"name": name, "version": "1.0", "release": 1},
        {"name": name, "version": version, "release": 1,
         "depends": depends or []},
    )


def _order(result):
    return [p["name"] for p, _ in result[0]]


class TopologicalOrderTests(unittest.TestCase):

    def test_dep_upgrades_before_dependent(self):
        # a depends on b -> b must come first.
        q = [_pair("a", depends=["b"]), _pair("b")]
        ordered, cycles = _topological_upgrade_order(q)
        self.assertEqual([p["name"] for p, _ in ordered], ["b", "a"])
        self.assertEqual(cycles, [])

    def test_alphabetical_tiebreak_within_rank(self):
        # Three independent packages -> pure alphabetical order.
        q = [_pair("c"), _pair("a"), _pair("b")]
        ordered, _ = _topological_upgrade_order(q)
        self.assertEqual([p["name"] for p, _ in ordered], ["a", "b", "c"])

    def test_cross_queue_dep_imposes_no_constraint(self):
        # a depends on x, but x is not in the queue (already installed) ->
        # a is a ready root; alphabetical with the other root.
        q = [_pair("a", depends=["x"]), _pair("b")]
        ordered, cycles = _topological_upgrade_order(q)
        self.assertEqual([p["name"] for p, _ in ordered], ["a", "b"])
        self.assertEqual(cycles, [])

    def test_diamond_orders_deps_first(self):
        # d depends on b,c ; b,c depend on a -> a, then b,c (alpha), then d.
        q = [
            _pair("d", depends=["b", "c"]),
            _pair("b", depends=["a"]),
            _pair("c", depends=["a"]),
            _pair("a"),
        ]
        ordered = [p["name"] for p, _ in _topological_upgrade_order(q)[0]]
        self.assertEqual(ordered.index("a"), 0)
        self.assertLess(ordered.index("b"), ordered.index("d"))
        self.assertLess(ordered.index("c"), ordered.index("d"))
        self.assertEqual(ordered[-1], "d")
        self.assertEqual(ordered, ["a", "b", "c", "d"])

    def test_authorized_kernel_forced_last(self):
        # linux-kernel would sort first alphabetically among independents;
        # kernel_last_name forces it to the very end.
        q = [_pair("linux-kernel"), _pair("zlib"), _pair("acl")]
        ordered = [
            p["name"]
            for p, _ in _topological_upgrade_order(
                q, kernel_last_name="linux-kernel"
            )[0]
        ]
        self.assertEqual(ordered, ["acl", "zlib", "linux-kernel"])

    def test_kernel_last_overrides_dependent_edge(self):
        # Even if something depends ON the kernel, the kernel is forced last.
        q = [_pair("kmod-consumer", depends=["linux-kernel"]),
             _pair("linux-kernel")]
        ordered = [
            p["name"]
            for p, _ in _topological_upgrade_order(
                q, kernel_last_name="linux-kernel"
            )[0]
        ]
        self.assertEqual(ordered[-1], "linux-kernel")

    def test_cycle_degrades_to_alpha_and_is_reported(self):
        # a<->b cycle: not topologically orderable; grouped, alpha-sorted,
        # appended, and reported in cycle_groups.
        q = [_pair("b", depends=["a"]), _pair("a", depends=["b"])]
        ordered, cycles = _topological_upgrade_order(q)
        self.assertEqual([p["name"] for p, _ in ordered], ["a", "b"])
        self.assertEqual(cycles, [["a", "b"]])

    def test_acyclic_prefix_then_cycle(self):
        # c is acyclic (ready); a<->b cycle appended after it.
        q = [_pair("c"), _pair("b", depends=["a"]), _pair("a", depends=["b"])]
        ordered, cycles = _topological_upgrade_order(q)
        names = [p["name"] for p, _ in ordered]
        self.assertEqual(names[0], "c")
        self.assertEqual(sorted(names[1:]), ["a", "b"])
        self.assertEqual(cycles, [["a", "b"]])


# ---------------------------------------------------------------------------
# Kernel-replace gate + topo integration through cmd_upgrade
# ---------------------------------------------------------------------------


def _upgrade_args(**overrides):
    base = dict(
        packages=[],
        upgrade_all=True,
        allow_downgrade=False,
        ignore_holds=False,
        upgrade_security_only=False,
        upgrade_dry_run=False,
        upgrade_yes=True,
        upgrade_allow_kernel_replace=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _CmdUpgradeHarness(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.db_root = self.tmp / "root"
        self.db_root.mkdir()
        self.db = PackageDB(
            db_path=str(self.tmp / "pkm.db"), root=str(self.db_root)
        )
        self.cache_dir = self.tmp / "cache"
        self.rollback_dir = self.tmp / "rollback"
        self.cache_dir.mkdir()
        self.dl_path = self.tmp / "dl.igos.tar.gz"
        self.dl_path.write_bytes(b"archive")
        # name -> remote dict, populated per-test
        self.remotes = {}
        self.install_calls = []

    def tearDown(self):
        self._tmpdir.cleanup()

    def _install_pkg(self, name, depends=None):
        """Register `name` as installed@1.0 with an upgradable remote@2.0."""
        self.db.add_installed(name, "1.0", release=1, tier="core")
        self.remotes[name] = {
            "name": name, "version": "2.0", "release": 1,
            "sha256": "a" * 64, "depends": depends or [], "size": 0,
        }

    def _run(self, args):
        def record_install(name, **kwargs):
            self.install_calls.append(name)
            return (True, "ok")

        remotes = self.remotes

        class FakeRepo:
            def __init__(fr):
                pass

            def get_package(fr, name):
                return remotes.get(name)

            def download_package(fr, name):
                return True, str(self.dl_path)

            def resolve_dependencies(fr, name, db):
                return True, []

        with patch.object(cli, "RepoManager", FakeRepo), \
             patch.object(cli.PackageInstaller, "install",
                          side_effect=record_install, autospec=False), \
             patch.object(pkm.repo, "REPO_PKG_CACHE", self.cache_dir), \
             patch.object(pkm.repo, "REPO_ROLLBACK_DIR", self.rollback_dir), \
             patch("pkm.remover.PackageRemover.remove",
                   return_value=(True, "removed")):
            cmd_upgrade(self.db, args)
        return self.install_calls


class KernelGateTests(_CmdUpgradeHarness):

    def test_all_excludes_kernel_and_upgrades_the_rest(self):
        self._install_pkg("linux-kernel")
        self._install_pkg("foo")
        calls = self._run(_upgrade_args(packages=[], upgrade_all=True))
        self.assertIn("foo", calls)
        self.assertNotIn("linux-kernel", calls)

    def test_all_kernel_only_excluded_returns_without_upgrading(self):
        self._install_pkg("linux-kernel")
        calls = self._run(_upgrade_args(packages=[], upgrade_all=True))
        self.assertEqual(calls, [])

    def test_named_kernel_without_flag_hard_refuses(self):
        self._install_pkg("linux-kernel")
        with self.assertRaises(SystemExit) as cm:
            self._run(_upgrade_args(packages=["linux-kernel"],
                                    upgrade_all=False))
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(self.install_calls, [])

    def test_authorized_kernel_upgrades_and_goes_last(self):
        self._install_pkg("linux-kernel")
        self._install_pkg("acl")
        calls = self._run(_upgrade_args(
            packages=[], upgrade_all=True,
            upgrade_allow_kernel_replace=True,
        ))
        self.assertIn("linux-kernel", calls)
        self.assertEqual(calls[-1], "linux-kernel")


class TopoIntegrationTests(_CmdUpgradeHarness):

    def test_dep_installs_before_dependent(self):
        # a depends on b; both upgradable -> b installs before a.
        self._install_pkg("b")
        self._install_pkg("a", depends=["b"])
        calls = self._run(_upgrade_args(packages=[], upgrade_all=True))
        self.assertEqual(calls, ["b", "a"])


if __name__ == "__main__":
    sys.exit(unittest.main())
