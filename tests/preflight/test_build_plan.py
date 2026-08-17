#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""scripts/preflight-build-plan.py — the pre-launch build-vs-skip plan.

Every classification gets a fixture, because the value of this tool is entirely
in being right about the awkward ones. Two in particular are the reason it
exists:

  tree-ahead-of-archive — a bash-tier recipe advanced but nothing rebuilt from
  it. There is no skip-built layer on those tiers, so a git range that does not
  happen to span the recipe's commit will not see it and the build will not
  catch it. That is how a stale package shipped on a candidate image.

  archive-current-but-not-installed — the package's archive is banked and
  perfectly current, and the package is not on the substrate at all. Fifteen
  packages were found in that state: current archives, zero files, zero
  database rows. A currency-only sweep called every one of them fine.

Also asserted, because a planning tool that is wrong in these ways is worse than
none: the Python-tier verdict tracks the builder's real template hash rather
than a local guess; the pkm database's release column is never consulted for
currency (it has been found reset corpus-wide, so a plan that trusted it would
read "current" off corrupted data); nothing outside a recognized tier is
silently dropped; and the run does not mutate the substrate it inspects.
"""
import importlib
import importlib.util
import io
import os
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL = REPO_ROOT / "scripts" / "preflight-build-plan.py"
for _p in (str(REPO_ROOT), str(REPO_ROOT / "igos-build")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_spec = importlib.util.spec_from_file_location("preflight_build_plan", TOOL)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

_content_hash = importlib.import_module("igos-build.content_hash")
_parser = importlib.import_module("igos-build.parser")
from pkm.database import PackageDB  # noqa: E402


RECIPE = """name: {name}
version: "{version}"
release: {release}
description: {name} test package
license: GPL-3.0-or-later
tier: {tier}
build_style: custom
source: []
"""


class BuildPlanTestBase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.tree = self.tmp / "tree"
        self.chroot = self.tmp / "chroot"
        self.igos = self.chroot / "var" / "lib" / "igos"
        self.archives = self.igos / "archives"
        self.manifests = self.igos / "packages"
        self.sources = self.chroot / "sources"
        for d in (self.archives, self.manifests, self.sources):
            d.mkdir(parents=True)
        (self.tree / "packages").mkdir(parents=True)
        self.db_path = self.igos / "pkm.db"

    def tearDown(self):
        self._td.cleanup()

    # ---- fixture builders -------------------------------------------------

    def recipe(self, name, tier, version="1.0", release=1, build_sh="#!/bin/bash\n"):
        d = self.tree / "packages" / tier / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "package.yml").write_text(
            RECIPE.format(name=name, version=version, release=release, tier=tier))
        if build_sh is not None:
            (d / "build.sh").write_text(build_sh)
        return d / "package.yml"

    def archive(self, name, version="1.0", release=1, payload=("usr/bin/x",),
                pkginfo=True):
        """A real .igos.tar.gz carrying a real .PKGINFO header."""
        path = self.archives / f"{name}-{version}.igos.tar.gz"
        with tarfile.open(path, "w:gz") as tf:
            if pkginfo:
                body = (f"pkgname={name}\npkgver={version}\npkgrel={release}\n"
                        f"tier=core\n").encode()
                info = tarfile.TarInfo(".PKGINFO")
                info.size = len(body)
                tf.addfile(info, io.BytesIO(body))
            for rel in payload:
                body = b"payload"
                info = tarfile.TarInfo(rel)
                info.size = len(body)
                tf.addfile(info, io.BytesIO(body))
        return path

    def installed(self, name, version="1.0", files=("usr/bin/x",),
                  on_disk=True, template_hash_marker=None, db_row=True,
                  db_release=1):
        """Deploy a package onto the fixture substrate: manifest, row, files."""
        lines = [f"PACKAGE NAME: {name}-{version}",
                 f"PACKAGE VERSION: {version}"]
        if template_hash_marker:
            lines.append(f"TEMPLATE_HASH: {template_hash_marker}")
        lines += ["DESCRIPTION:", f"{name}: test", "", "FILE LIST:"]
        lines += list(files)
        (self.manifests / f"{name}-{version}").write_text("\n".join(lines) + "\n")
        if on_disk:
            for rel in files:
                p = self.chroot / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("payload")
        if db_row:
            db = PackageDB(self.db_path, root=str(self.chroot))
            db.add_installed(name=name, version=version, release=db_release)
            db.close()

    def plan(self, **kw):
        return _mod.build_plan(self.tree, self.chroot, self.archives,
                              self.manifests, self.db_path, self.sources, **kw)

    def verdict(self, plan, name):
        for row in plan["rows"]:
            if row["name"] == name:
                return row["verdict"], row["reason"]
        self.fail(f"{name} absent from the plan — a dropped package is the "
                  f"failure this tool exists to prevent")


class BashTierTest(BuildPlanTestBase):
    """core / base — no skip-built layer, so currency AND deployment."""

    def test_current(self):
        self.recipe("bash-ok", "core")
        self.archive("bash-ok")
        self.installed("bash-ok")
        v, why = self.verdict(self.plan(), "bash-ok")
        self.assertEqual(v, "CURRENT", why)

    def test_rebuild_when_tree_release_is_ahead(self):
        """The class a git range cannot see and the build will not catch."""
        self.recipe("drifted", "core", version="1.0", release=12)
        self.archive("drifted", version="1.0", release=11)
        self.installed("drifted")
        v, why = self.verdict(self.plan(), "drifted")
        self.assertEqual(v, "REBUILD")
        self.assertIn("1.0-12", why)
        self.assertIn("1.0-11", why)

    def test_rebuild_when_tree_version_is_ahead(self):
        self.recipe("bumped", "core", version="2.0")
        self.archive("bumped", version="1.9")
        self.installed("bumped", version="1.9")
        v, _why = self.verdict(self.plan(), "bumped")
        self.assertEqual(v, "REBUILD")

    def test_deploy_when_archive_is_current_but_nothing_installed(self):
        """Current archive banked, package absent from the substrate entirely."""
        self.recipe("lib32-thing", "core")
        self.archive("lib32-thing")
        v, why = self.verdict(self.plan(), "lib32-thing")
        self.assertEqual(v, "DEPLOY", why)
        self.assertIn("no installed manifest", why)
        self.assertIn("no database row", why)

    def test_deploy_when_manifest_exists_but_files_are_gone(self):
        """A manifest and a row prove registration, not presence."""
        self.recipe("hollow", "core")
        self.archive("hollow")
        self.installed("hollow", on_disk=False)
        v, why = self.verdict(self.plan(), "hollow")
        self.assertEqual(v, "DEPLOY", why)
        self.assertIn("files absent", why)

    def test_deploy_when_row_is_missing(self):
        self.recipe("rowless", "core")
        self.archive("rowless")
        self.installed("rowless", db_row=False)
        v, why = self.verdict(self.plan(), "rowless")
        self.assertEqual(v, "DEPLOY", why)
        self.assertIn("no database row", why)

    def test_missing_when_no_archive_is_banked(self):
        self.recipe("absent", "base")
        v, why = self.verdict(self.plan(), "absent")
        self.assertEqual(v, "MISSING", why)

    def test_currency_ignores_the_database_release_column(self):
        """The DB release has been found reset corpus-wide; it must not decide.

        Archive and tree agree at r7 while the row says r1. A sweep that read
        the database would call this stale and schedule a needless rebuild —
        or, with the comparison the other way, call a stale package current.
        """
        self.recipe("honest", "core", version="1.0", release=7)
        self.archive("honest", version="1.0", release=7)
        self.installed("honest", db_release=1)
        v, why = self.verdict(self.plan(), "honest")
        self.assertEqual(v, "CURRENT", why)

    def test_archive_ahead_of_tree_is_named_not_hidden(self):
        self.recipe("behind", "core", version="1.0", release=2)
        self.archive("behind", version="1.0", release=5)
        self.installed("behind")
        v, why = self.verdict(self.plan(), "behind")
        self.assertEqual(v, "CURRENT")
        self.assertIn("AHEAD of tree", why)

    def test_newest_archive_wins_when_several_are_banked(self):
        """A superseded older twin beside the current one must not decide."""
        self.recipe("twinned", "core", version="1.0", release=3)
        self.archive("twinned", version="0.9", release=1)
        self.archive("twinned", version="1.0", release=3)
        self.installed("twinned")
        v, why = self.verdict(self.plan(), "twinned")
        self.assertEqual(v, "CURRENT", why)

    def test_metadata_less_archive_is_surfaced(self):
        self.recipe("plain", "core")
        self.archive("plain", pkginfo=False)
        plan = self.plan()
        self.assertTrue(plan["metadata_less_archives"],
                        "an archive whose currency cannot be read must be named")
        v, _ = self.verdict(plan, "plain")
        self.assertEqual(v, "MISSING",
                         "a header-less archive is not evidence of currency")


class PythonTierTest(BuildPlanTestBase):
    """desktop / extra / compute / ai — reproduce the builder's own decision."""

    def _hash_for(self, yml_path):
        pkg = _parser.parse_template(yml_path)
        return _content_hash.template_hash(pkg, self.sources)

    def test_skip_when_the_recorded_hash_matches(self):
        yml = self.recipe("gtk-thing", "desktop")
        self.installed("gtk-thing", template_hash_marker=self._hash_for(yml))
        v, why = self.verdict(self.plan(), "gtk-thing")
        self.assertEqual(v, "SKIP", why)

    def test_build_when_the_recipe_changed(self):
        yml = self.recipe("gtk-thing", "desktop")
        stale = self._hash_for(yml)
        self.installed("gtk-thing", template_hash_marker=stale)
        # Edit build.sh — the template hash folds it, so the marker goes stale.
        (yml.parent / "build.sh").write_text("#!/bin/bash\n# changed\n")
        self.assertNotEqual(self._hash_for(yml), stale,
                            "precondition: the edit must move the hash")
        v, why = self.verdict(self.plan(), "gtk-thing")
        self.assertEqual(v, "BUILD")
        self.assertEqual(why, "hash-changed")

    def test_never_built(self):
        self.recipe("brand-new", "ai")
        v, why = self.verdict(self.plan(), "brand-new")
        self.assertEqual((v, why), ("BUILD", "never-built"))

    def test_manifest_less_is_distinguished_from_never_built(self):
        """An archive with no manifest is a different state, and says so."""
        self.recipe("banked", "extra")
        self.archive("banked")
        v, why = self.verdict(self.plan(), "banked")
        self.assertEqual((v, why), ("BUILD", "manifest-less"))

    def test_python_tier_ignores_archive_currency(self):
        """--skip-built keys on the hash, not on versions. Report what it does.

        The archive is two releases behind the tree, which on a bash tier would
        be a REBUILD. Here the recorded hash still matches, so the builder will
        skip — and this tool must say skip, or it is not predicting the build.
        """
        yml = self.recipe("hashy", "compute", version="1.0", release=3)
        self.archive("hashy", version="1.0", release=1)
        self.installed("hashy", template_hash_marker=self._hash_for(yml))
        v, why = self.verdict(self.plan(), "hashy")
        self.assertEqual(v, "SKIP", why)


class ClassificationTest(BuildPlanTestBase):
    def test_toolchain_is_excluded_but_counted(self):
        self.recipe("binutils-pass1", "toolchain")
        v, why = self.verdict(self.plan(), "binutils-pass1")
        self.assertEqual(v, "EXCLUDED", why)

    def test_tier_filter(self):
        self.recipe("a-core", "core")
        self.recipe("a-desk", "desktop")
        names = {r["name"] for r in self.plan(tiers=("desktop",))["rows"]}
        self.assertEqual(names, {"a-desk"})

    def test_absent_database_does_not_abort_the_plan(self):
        """A substrate with no pkm.db still gets a plan, with the gap stated."""
        self.recipe("nodb", "core")
        self.archive("nodb")
        plan = self.plan()
        self.assertFalse(plan["db_present"])
        v, why = self.verdict(plan, "nodb")
        self.assertEqual(v, "DEPLOY", why)

    def test_totals_line_counts_actionable_packages(self):
        self.recipe("c1", "core")                      # MISSING
        self.recipe("d1", "desktop")                   # BUILD
        self.recipe("c2", "core")
        self.archive("c2")
        self.installed("c2")                           # CURRENT
        text = _mod.render(self.plan(), show_all=False)
        self.assertIn("TOTAL PACKAGES REQUIRING ACTION: 2 of 3", text)


class ReadOnlyTest(BuildPlanTestBase):
    def test_the_run_does_not_mutate_the_substrate(self):
        self.recipe("ro", "core")
        self.archive("ro")
        self.installed("ro")
        before = {p: p.stat().st_mtime_ns
                  for p in self.chroot.rglob("*") if p.is_file()}
        self.plan()
        after = {p: p.stat().st_mtime_ns
                 for p in self.chroot.rglob("*") if p.is_file()}
        self.assertEqual(before, after,
                         "the plan must not write to the substrate it inspects")

    def test_the_database_connection_refuses_writes(self):
        self.recipe("ro2", "core")
        self.installed("ro2")
        conn = _mod.open_db_readonly(self.db_path)
        self.assertIsNotNone(conn)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("DELETE FROM installed")
        finally:
            conn.close()

    def test_no_journal_or_wal_sidecar_is_left_behind(self):
        self.recipe("ro3", "core")
        self.installed("ro3")
        self.plan()
        strays = [p.name for p in self.igos.iterdir()
                  if p.name.startswith("pkm.db-")]
        self.assertEqual(strays, [],
                         "an immutable read-only open must leave no sidecar")


class CommandLineTest(BuildPlanTestBase):
    def test_exit_zero_once_a_plan_is_produced(self):
        """A planning tool reports; it does not refuse. MISSING is still a plan."""
        self.recipe("x", "core")
        rc = _mod.main(["--tree", str(self.tree), "--chroot", str(self.chroot)])
        self.assertEqual(rc, 0)

    def test_absent_chroot_exits_two(self):
        rc = _mod.main(["--tree", str(self.tree),
                        "--chroot", str(self.tmp / "nope")])
        self.assertEqual(rc, 2, "a plan that could not be produced is not a "
                                "clean plan")

    def test_json_output_is_parseable(self):
        import json
        self.recipe("j", "core")
        r = subprocess.run(
            [sys.executable, str(TOOL), "--tree", str(self.tree),
             "--chroot", str(self.chroot), "--json"],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual([row["name"] for row in data["rows"]], ["j"])


if __name__ == "__main__":
    unittest.main()
