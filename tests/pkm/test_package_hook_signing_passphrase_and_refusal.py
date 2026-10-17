#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""A package's own post-install hook receives the signing passphrase, and a
hook that refused to sign leaves the install not finished.

Two defects, both measured on a real R001.3 install on 2026-10-16:

1. The system installer puts the machine owner's signing-key passphrase in its
   environment for the length of the package phase, so the kernel's
   post-install hook can sign the first boot image. pkm builds a package's own
   hook environment from HELPER_ENV_ALLOWLIST, which does not carry it, so the
   hook asked an empty room, waited out the two-minute prompt and refused to
   sign (install trace rows 7201-7202: rc 1 at 00:12:08 after a fire at
   00:10:08). The hook environment now carries IGOS_MOK_PASSPHRASE when it is
   set, and still nothing else a helper may not receive.

2. A post-install hook that refused to sign was a warning: `pkm install
   nvidia` reported success while the boot image carrying the driver's
   cmdline had been refused, and the machine rebooted into a dark output. A
   hook that exits with the refusal status (77, the signing helper's
   MOK_REFUSED_EXIT) now marks the package degraded and the install returns
   not-finished; every other non-zero status keeps its warning.

The three tests that state the change failed on the tree before it (the
passphrase absent from the hook, the refusal installed as a success, no
refusal status defined); the other five hold what must not change with it.
"""

import os
import re
import tarfile
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

import pkm.installer as installer_mod  # noqa: E402
from pkm.database import PackageDB  # noqa: E402
from pkm.hooks import HookResult  # noqa: E402
from pkm.installer import PackageInstaller  # noqa: E402

PASS_VAR = "IGOS_MOK_PASSPHRASE"


def _hook_script(root, name, body):
    hook = Path(root) / "var" / "lib" / "pkm" / "hooks" / name / "post-install"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\n" + textwrap.dedent(body))
    hook.chmod(0o755)
    return hook


def _run_directly(root, hook):
    """The hook command without chroot, which a test cannot use."""
    return [str(hook)]


class PackageHookEnvironmentTest(unittest.TestCase):

    def _fire(self, environ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            dump = Path(tmp) / "env.txt"
            _hook_script(root, "kpkg", f"env > {dump}\nexit 0\n")
            inst = PackageInstaller(db=MagicMock(), root=str(root))
            with patch.dict(os.environ, environ, clear=True), \
                 patch.object(installer_mod, "_post_install_hook_cmd",
                              _run_directly), \
                 patch.object(installer_mod, "_TRACE_AVAILABLE", False):
                rc = inst._run_post_install_hook("kpkg", "1.0")
            return rc, dump.read_text()

    def test_the_passphrase_reaches_a_package_hook(self):
        rc, env = self._fire({PASS_VAR: "the-owner-passphrase",
                              "PATH": "/usr/bin:/bin"})
        self.assertIn(f"{PASS_VAR}=the-owner-passphrase", env.splitlines())
        self.assertEqual(rc, 0)

    def test_nothing_else_a_helper_may_not_receive_does(self):
        _, env = self._fire({PASS_VAR: "p", "LD_PRELOAD": "/tmp/x.so",
                             "PYTHONPATH": "/tmp", "PATH": "/usr/bin:/bin"})
        self.assertNotIn("LD_PRELOAD=", env)
        self.assertNotIn("PYTHONPATH=", env)

    def test_absent_stays_absent(self):
        _, env = self._fire({"PATH": "/usr/bin:/bin"})
        self.assertNotIn(PASS_VAR, env)

    def test_helpers_still_never_receive_it(self):
        self.assertNotIn(PASS_VAR, installer_mod.HELPER_ENV_ALLOWLIST)


def _build_archive(tmp, name, version, hook_body):
    staging = Path(tmp) / f"b-{name}"
    (staging / "usr" / "bin").mkdir(parents=True)
    (staging / "usr" / "bin" / name).write_text("#!/bin/sh\n")
    hook_rel = f"var/lib/pkm/hooks/{name}/post-install"
    (staging / hook_rel).parent.mkdir(parents=True)
    (staging / hook_rel).write_text("#!/bin/sh\n" + hook_body)
    (staging / hook_rel).chmod(0o755)
    (staging / ".PKGINFO").write_text(
        f"pkgname={name}\npkgver={version}\npkgrel=1\npkgdesc=x\nlicense=GPL\n"
        f"tier=core\nbuilddate=2026-10-16T00:00:00Z\nsize=8\nfilecount=2\n")
    archive = Path(tmp) / f"{name}-{version}.igos.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(staging / ".PKGINFO", arcname=".PKGINFO")
        tf.add(staging / "usr" / "bin" / name, arcname=f"usr/bin/{name}")
        tf.add(staging / hook_rel, arcname=hook_rel)
    return str(archive)


class RefusedSigningIsNotFinishedTest(unittest.TestCase):

    def _install(self, hook_body):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        tmp = Path(self._td.name)
        root = tmp / "root"
        root.mkdir()
        archive = _build_archive(tmp, "kpkg", "1.0", hook_body)
        db = PackageDB(tmp / "pkm.db", root=str(root))
        self.addCleanup(db.close)
        inst = PackageInstaller(db, root=str(root))
        with patch.object(installer_mod, "_post_install_hook_cmd",
                          _run_directly), \
             patch.object(installer_mod, "_TRACE_AVAILABLE", False), \
             patch("pkm.installer.run_canonical_hooks",
                   return_value=HookResult([], [], [])), \
             patch("pkm.installer.run_archive_lifecycle_hook",
                   return_value=HookResult([], [], [])):
            ok, msg = inst.install("kpkg", archive_path=archive)
        return ok, msg, db.get_installed("kpkg")

    def test_a_refusal_marks_the_package_degraded_and_not_finished(self):
        ok, msg, row = self._install("exit 77\n")
        self.assertFalse(ok)
        self.assertIn("refused to sign", msg)
        self.assertIn("sudo pkm reinstall kpkg", msg)
        self.assertNotIn("Rollback recommended", msg)
        self.assertTrue(row["degraded"])
        self.assertIn("refused to sign", row["degraded"])

    def test_any_other_failure_keeps_its_warning(self):
        ok, msg, row = self._install("exit 1\n")
        self.assertTrue(ok, msg)
        self.assertFalse(row["degraded"])

    def test_success_is_success(self):
        ok, msg, row = self._install("exit 0\n")
        self.assertTrue(ok, msg)
        self.assertFalse(row["degraded"])


class RefusalStatusIsOneNumberTest(unittest.TestCase):

    def test_pkm_and_the_signing_helper_agree(self):
        text = (REPO_ROOT / "scripts" / "mok-signing.sh").read_text()
        m = re.search(r"^MOK_REFUSED_EXIT=(\d+)$", text, re.M)
        self.assertIsNotNone(m, "scripts/mok-signing.sh defines no MOK_REFUSED_EXIT")
        self.assertEqual(int(m.group(1)), installer_mod.SIGNING_REFUSED_EXIT)


if __name__ == "__main__":
    unittest.main()
