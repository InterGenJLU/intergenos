# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The module tables depmod writes are hook-managed by rule.

depmod writes eleven tables into usr/lib/modules/<release>/ from the modules
present there, and writes them byte for byte again over the same modules. A
recorder that decides by the bytes therefore kept a kernel's tables
content-checked, and the next module any package added (shipped in its
payload, or built by its own hook, which then runs depmod) rewrote four of
them under that other package's install: `pkm verify` reported the kernel
modified on a healthy machine. The tables are hook-managed content for
whichever package ships them, whether or not a hook changed them, in the
archive recorder, the source-build recorder and the manifest import; every
other file under the release directory keeps the byte check.

The canonical depmod hook is replaced by a stub that records its arguments, so
the tests decide what the tables hold; the release directory is a made-up name,
because the rule is the directory and the eleven names, never a version.
"""

import hashlib
import os
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path

from pkm import hookrecord
from pkm import hooks as hooks_mod
from pkm.database import PackageDB
from pkm.installer import PackageInstaller

RELEASE = "0.0.0-unit"
MODDIR = f"usr/lib/modules/{RELEASE}"
TABLE_NAMES = (
    "modules.alias", "modules.alias.bin", "modules.builtin.alias.bin",
    "modules.builtin.bin", "modules.dep", "modules.dep.bin",
    "modules.devname", "modules.softdep", "modules.symbols",
    "modules.symbols.bin", "modules.weakdep",
)
TABLES = sorted(f"{MODDIR}/{t}" for t in TABLE_NAMES)
MODULE = f"{MODDIR}/kernel/drivers/demo/demo.ko"
INPUTS = sorted(f"{MODDIR}/{n}" for n in (
    "modules.order", "modules.builtin", "modules.builtin.modinfo"))
# The four a module added for the same release changes (measured with kmod 34
# on a real module set: alias, alias.bin, dep, dep.bin).
REWRITTEN = sorted(f"{MODDIR}/{t}" for t in (
    "modules.alias", "modules.alias.bin", "modules.dep", "modules.dep.bin"))
EXTRA = f"{MODDIR}/extra/extra.ko"


def kernel_payload():
    payload = {MODULE: b"a module\n",
               f"{MODDIR}/modules.order": b"kernel/drivers/demo/demo.ko\n",
               f"{MODDIR}/modules.builtin": b"",
               f"{MODDIR}/modules.builtin.modinfo": b""}
    for path in TABLES:
        payload[path] = f"{path.rsplit('/', 1)[1]} for one module\n".encode()
    return payload


def sha(data):
    return hashlib.sha256(data).hexdigest()


class _Harness(unittest.TestCase):
    """A real PackageInstaller over real archives on a scratch root."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "root"
        self.root.mkdir()
        self.db = PackageDB(self.tmp / "t.db", root=str(self.root))
        self.addCleanup(self.db.close)
        self.installer = PackageInstaller(self.db, root=str(self.root))
        self.depmod_calls = self.tmp / "depmod-calls"
        stub = self.tmp / "depmod"
        stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{self.depmod_calls}"\n')
        stub.chmod(0o755)
        original = hooks_mod.DEPMOD
        hooks_mod.DEPMOD = str(stub)
        self.addCleanup(setattr, hooks_mod, "DEPMOD", original)

    def install(self, name, payload, hook=None):
        stg = self.tmp / f"stg-{name}"
        for rel, data in payload.items():
            target = stg / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (stg / ".PKGINFO").write_text(f"pkgname = {name}\npkgver = 1.0\n")
        if hook is not None:
            (stg / ".scripts").mkdir()
            script = stg / ".scripts/post_install.sh"
            script.write_text("#!/bin/bash\nset -e\n" + hook)
            script.chmod(0o755)
        arc = self.tmp / f"{name}-1.0.igos.tar.gz"
        with tarfile.open(arc, "w:gz") as tf:
            tf.add(stg, arcname=".")
        ok, msg = self.installer.install(name, archive_path=str(arc))
        self.assertTrue(ok, msg)
        return msg

    def generated(self, name):
        return {row[0]: row[1] for row in self.db.conn.execute(
            "SELECT f.path, f.is_generated FROM files f JOIN installed i "
            "ON f.package_id = i.id WHERE i.name = ? AND f.is_dir = 0",
            (name,))}

    def manifest_hook_managed(self, name):
        text = (self.root / f"var/lib/igos/packages/{name}-1.0").read_text()
        return sorted(line.split(":", 1)[1].strip()
                      for line in text.splitlines()
                      if line.startswith("HOOK-MANAGED:"))

    def assert_tables_by_rule(self, name):
        rows = self.generated(name)
        self.assertEqual(
            {p: rows.get(p) for p in TABLES}, {p: 1 for p in TABLES},
            "the eleven tables are not hook-managed")
        self.assertEqual(
            {p: rows.get(p) for p in [MODULE] + INPUTS},
            {p: 0 for p in [MODULE] + INPUTS},
            "a file that is not one of the eleven tables lost its byte check")

    def rewrite(self, paths, tag):
        for path in paths:
            (self.root / path).write_bytes(f"{path} {tag}\n".encode())


class TheRuleIsTheDirectoryAndTheNames(unittest.TestCase):

    def test_the_eleven_names_in_a_release_directory_and_nothing_else(self):
        is_table = getattr(hookrecord, "is_depmod_table", None)
        self.assertIsNotNone(is_table, "pkm.hookrecord states no such rule")
        for path in TABLES + [f"/{TABLES[0]}",
                              f"lib/modules/{RELEASE}/modules.dep"]:
            self.assertTrue(is_table(path), path)
        for path in [MODULE, EXTRA] + INPUTS + [
                f"{MODDIR}/kernel/modules.dep",
                "usr/lib/modules/modules.dep",
                f"{MODDIR}/modules.dep/",
                "usr/lib/modules/../modules.dep",
                "usr/share/modules.dep",
                f"usr/lib/modules/{RELEASE}/modules.dep.old"]:
            self.assertFalse(is_table(path), path)


class TheArchiveRecorder(_Harness):

    def test_a_package_shipping_the_tables_with_no_hook_has_them_hook_managed(self):
        msg = self.install("kmodtest", kernel_payload())
        self.assert_tables_by_rule("kmodtest")
        self.assertEqual(self.manifest_hook_managed("kmodtest"), TABLES)
        self.assertIn(
            "11 module table(s) depmod writes — hook-managed by rule",
            " ".join(msg.split()))
        self.assertIn(f"-a -b {self.root} {RELEASE}",
                      self.depmod_calls.read_text())
        clean = self.db.verify_package("kmodtest")
        self.assertEqual(clean["modified"], [], msg)
        self.assertTrue(set(TABLES) <= set(clean["generated"]))

    def test_a_module_added_later_leaves_the_kernel_verified(self):
        self.install("kmodtest", kernel_payload())
        self.install("kmodextra", {EXTRA: b"another module\n"})
        # What the canonical depmod writes for the larger module set.
        self.rewrite(REWRITTEN, "for two modules")
        after = self.db.verify_package("kmodtest")
        self.assertEqual(after["modified"], [],
                         "a module added by another package reported the "
                         "kernel's tables modified")
        self.assertTrue(set(TABLES) <= set(after["generated"]))

    def test_a_second_package_whose_hook_runs_depmod_leaves_the_kernel_verified(self):
        self.install("kmodtest", kernel_payload())
        rewrite = "".join(
            f'printf "%s\\n" "{p} for two modules" > "$PKM_PACKAGE_ROOT/{p}"\n'
            for p in REWRITTEN)
        self.install(
            "kmodhook", {"usr/share/kmodhook/extra.ko": b"another module\n"},
            hook=('install -D -m 0644 "$PKM_PACKAGE_ROOT/usr/share/kmodhook/'
                  f'extra.ko" "$PKM_PACKAGE_ROOT/{EXTRA}"\n' + rewrite))
        self.assertEqual(
            (self.root / REWRITTEN[0]).read_text(),
            f"{REWRITTEN[0]} for two modules\n")
        after = self.db.verify_package("kmodtest")
        self.assertEqual(after["modified"], [])

    def test_a_module_another_hook_rewrites_is_still_reported(self):
        self.install("kmodtest", kernel_payload())
        changed = sorted([MODULE, f"{MODDIR}/modules.order"])
        self.install(
            "kmodswap", {"usr/share/kmodswap/note": b"swap\n"},
            hook="".join(f'printf "%s\\n" "rewritten" > "$PKM_PACKAGE_ROOT/{p}"\n'
                         for p in changed))
        after = self.db.verify_package("kmodtest")
        self.assertEqual(sorted(after["modified"]), changed)
        self.assertEqual(self.generated("kmodtest")[MODULE], 0)


class TheManifestImport(_Harness):

    def lay_down_and_import(self, description="a kernel-shaped payload"):
        payload = kernel_payload()
        lines = ["PACKAGE NAME: kmodtest-1.0", "PACKAGE VERSION: 1.0",
                 "PACKAGE RELEASE: 1"]
        lines += ["DESCRIPTION:", f"kmodtest: {description}", "",
                  "FILE LIST:"]
        dirs = set()
        for rel, data in sorted(payload.items()):
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            parts = rel.split("/")[:-1]
            dirs |= {"/".join(parts[:i + 1]) for i in range(len(parts))}
        lines += [d + "/" for d in sorted(dirs)]
        lines += [f"{rel} sha256:{sha(data)}"
                  for rel, data in sorted(payload.items())]
        manifests = self.tmp / "manifests"
        manifests.mkdir(exist_ok=True)
        (manifests / "kmodtest-1.0").write_text("\n".join(lines) + "\n")
        self.assertEqual(self.db.import_manifests(str(manifests)), 1)
        return manifests

    def test_an_imported_manifest_that_states_no_class_has_the_tables_hook_managed(self):
        self.lay_down_and_import()
        self.assert_tables_by_rule("kmodtest")
        # A later re-register from changed manifest bytes, still stating no
        # class, cannot return them to a byte check.
        self.lay_down_and_import(description="the same payload, re-registered")
        self.assert_tables_by_rule("kmodtest")
        self.rewrite(REWRITTEN, "for two modules")
        self.assertEqual(self.db.verify_package("kmodtest")["modified"], [])


class TheSourceBuildRecorder(_Harness):

    def register(self):
        """Lay the payload down and register its rows the way a builder does
        (igos-build/tracker.py writes them with add_installed and add_files),
        with no import and no recorder."""
        payload = kernel_payload()
        for rel, data in payload.items():
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        pkg = self.db.add_installed("kmodtest", "1.0",
                                    install_method="source-build")
        self.db.add_files(pkg, sorted(payload),
                          hashes={p: sha(d) for p, d in payload.items()})
        return payload

    def test_rows_a_builder_registers_have_the_tables_hook_managed(self):
        self.register()
        self.assert_tables_by_rule("kmodtest")

    def test_the_build_recorder_has_the_tables_hook_managed(self):
        payload = self.register()
        baseline = self.installer.hook_baseline("kmodtest")
        # The kernel's post_install runs depmod over the same modules: the
        # same bytes, written into new files.
        for path in TABLES:
            target = self.root / path
            fresh = target.with_name(target.name + ".new")
            fresh.write_bytes(payload[path])
            os.replace(fresh, target)
        changed, messages = self.installer.record_hook_changes(
            "kmodtest", baseline)
        self.assertEqual(changed, [], messages)
        self.assert_tables_by_rule("kmodtest")
        flat = " ".join(" ".join(messages).split())
        self.assertIn(
            "11 module table(s) depmod writes — hook-managed by rule", flat)
        self.assertNotIn("the content stays checked", flat)
        self.assertEqual(self.manifest_hook_managed("kmodtest"), TABLES)
        self.rewrite(REWRITTEN, "for two modules")
        self.assertEqual(self.db.verify_package("kmodtest")["modified"], [])


if __name__ == "__main__":
    unittest.main()
