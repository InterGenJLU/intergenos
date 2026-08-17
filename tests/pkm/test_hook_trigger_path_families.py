#!/usr/bin/env python3
"""Canonical-hook triggers must not go blind to a whole family of paths.

WHY THIS FILE EXISTS. The shared-library cache trigger enumerated the library
directories it knew about. When 32-bit libraries were added under /usr/lib32 and
compute libraries under /opt/rocm/lib, the trigger did not match them, so the
cache was never rebuilt for those installs. Nothing failed and nothing was
reported: a cache that is never rebuilt is indistinguishable, from the outside,
from a cache with nothing to add. It surfaced only when a program that needed a
32-bit library could not start. The unit-definition reload trigger had the same
shape, naming one unit suffix while the tree ships timers, sockets, path units,
target units and drop-in configuration.

So the tests here come in two kinds:
  * INSTANCE tests — one file list per family, asserting the hook fires. These
    are the cases that were broken, and each fails without the fix.
  * CURRENCY tests — the tree's own declared shipped paths are read from every
    recipe, and every path that belongs to a hook's class must select that hook.
    An instance test proves the families we thought of; the currency tests are
    what catch the next family nobody thought of, which is the actual defect
    class. They also fail without the fix, on real declared paths.

The guard direction is asserted too: a path list with nothing relevant must fire
nothing, and user-manager units must NOT select the system reload — a system
daemon-reload cannot refresh a user manager, and a hook that ran without doing
the job is worse than a gap that is written down.
"""

import glob
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from pkm.hooks import CANONICAL_HOOKS, run_canonical_hooks

REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_fake_bin(bindir, name, exit_code=0):
    """Executable stub that logs its argv, so a test can prove the hook RAN."""
    path = Path(bindir) / name
    log = Path(bindir) / f"{name}.log"
    path.write_text(
        "#!/bin/bash\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
        f"exit {exit_code}\n"
    )
    path.chmod(0o755)
    return path, log


def _hook(hook_id):
    for h in CANONICAL_HOOKS:
        if h.id == hook_id:
            return h
    raise AssertionError(f"no canonical hook with id {hook_id!r}")


class LdconfigTriggerFamilies(unittest.TestCase):
    """The shared-library cache hook must fire wherever a library can land."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pkm-hookfam-")
        self.bin = Path(self.tmp) / "bin"
        self.bin.mkdir()
        self.root = Path(self.tmp) / "root"
        self.root.mkdir()
        self._orig_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{self.bin}:{self._orig_path}"

    def tearDown(self):
        os.environ["PATH"] = self._orig_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fires(self, file_list):
        _, log = _make_fake_bin(self.bin, "ldconfig", exit_code=0)
        result = run_canonical_hooks(
            self.root, file_list, "fixture", "1.0", "install")
        self.assertEqual(result.critical_failures, [])
        return log.exists()

    def test_fires_for_a_32bit_library(self):
        # The defect as it was hit: a 32-bit library on the loader path (the
        # 32-bit C library ships the drop-in that puts /usr/lib32 there).
        self.assertTrue(
            self._fires(["usr/lib32/libfoo.so.1"]),
            "a 32-bit library install must rebuild the shared-library cache")

    def test_fires_for_a_library_under_an_opt_prefix(self):
        # The compute stack installs under /opt and ships its own loader
        # drop-in; the directory is searched, so the cache must be rebuilt.
        self.assertTrue(
            self._fires(["opt/rocm/lib/libfoo.so"]),
            "a library under an /opt prefix must rebuild the cache")

    def test_fires_when_a_package_declares_a_new_search_directory(self):
        # A package can make a directory searchable by shipping a loader
        # drop-in while shipping no library of its own. Until the cache is
        # rebuilt, the directory it named is not searched.
        self.assertTrue(
            self._fires(["etc/ld.so.conf.d/fixture.conf"]),
            "declaring a loader search directory must rebuild the cache")

    def test_still_fires_for_the_directories_it_always_knew(self):
        # Guard: the families that worked before must keep working.
        for path in ("usr/lib/libfoo.so.1", "lib/libfoo.so", "usr/lib64/libfoo.so.2"):
            with self.subTest(path=path):
                shutil.rmtree(self.bin, ignore_errors=True)
                self.bin.mkdir()
                self.assertTrue(self._fires([path]), f"{path} must still fire")

    def test_does_not_fire_without_a_library_or_a_loader_config(self):
        # Guard the other direction: an ordinary package fires nothing.
        self.assertFalse(
            self._fires(["usr/bin/foo", "etc/foo.conf", "usr/share/doc/foo/README"]),
            "a package with no library and no loader config must not fire it")


class SystemdReloadTriggerFamilies(unittest.TestCase):
    """The unit-definition reload must fire for every kind of unit definition."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pkm-hookunit-")
        self.bin = Path(self.tmp) / "bin"
        self.bin.mkdir()
        self._orig_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{self.bin}:{self._orig_path}"

    def tearDown(self):
        os.environ["PATH"] = self._orig_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fires(self, file_list):
        # The reload is only meaningful against the live root, which is where
        # the existing hook tests exercise it too.
        shutil.rmtree(self.bin, ignore_errors=True)
        self.bin.mkdir()
        _, log = _make_fake_bin(self.bin, "systemctl", exit_code=0)
        result = run_canonical_hooks(
            Path("/"), file_list, "fixture", "1.0", "install")
        self.assertEqual(result.critical_failures, [])
        return log.exists() and "daemon-reload" in log.read_text()

    def test_fires_for_every_unit_suffix_the_tree_ships(self):
        # Each of these is a real staging site in the recipes: a timer, a path
        # unit, a socket unit and a target unit. None of them selected the
        # trigger while it named one suffix.
        for unit in ("fixture.timer", "fixture.socket",
                     "fixture@.path", "fixture.target"):
            with self.subTest(unit=unit):
                self.assertTrue(
                    self._fires([f"usr/lib/systemd/system/{unit}"]),
                    f"{unit} is a unit definition and must reload systemd's view")

    def test_fires_for_a_drop_in_configuration_file(self):
        # A drop-in changes a unit definition exactly as a unit file does.
        self.assertTrue(
            self._fires(
                ["usr/lib/systemd/system/fixture.service.d/10-fixture.conf"]),
            "a unit drop-in must reload systemd's view")

    def test_fires_for_an_etc_unit(self):
        self.assertTrue(
            self._fires(["etc/systemd/system/fixture.service"]),
            "a unit under etc must reload systemd's view")

    def test_does_not_claim_user_manager_units(self):
        # Written down deliberately: `systemctl daemon-reload` refreshes the
        # system manager only. Matching a user unit here would run a command
        # that cannot do the job and report a hook that ran.
        self.assertFalse(
            self._fires(["usr/lib/systemd/user/fixture.service"]),
            "a user-manager unit must not be claimed by the system reload")

    def test_does_not_fire_for_a_non_unit_file(self):
        self.assertFalse(
            self._fires(["usr/lib/systemd/system/fixture.conf",
                         "usr/lib/systemd/system-generators/fixture"]),
            "a file that is not a unit definition must not fire the reload")


class DepmodTriggerFamilies(unittest.TestCase):
    """The module dependency table must not depend on a prefix set elsewhere."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pkm-hookdep-")
        self.bin = Path(self.tmp) / "bin"
        self.bin.mkdir()
        self.root = Path(self.tmp) / "root"
        self.root.mkdir()
        self._orig_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{self.bin}:{self._orig_path}"

    def tearDown(self):
        os.environ["PATH"] = self._orig_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _invocation(self, file_list):
        shutil.rmtree(self.bin, ignore_errors=True)
        self.bin.mkdir()
        _, log = _make_fake_bin(self.bin, "depmod", exit_code=0)
        result = run_canonical_hooks(
            self.root, file_list, "linux-kernel", "1.0", "install")
        self.assertEqual(result.critical_failures, [])
        return log.read_text() if log.exists() else ""

    def test_fires_with_or_without_the_usr_prefix(self):
        # The kernel's own modules_install writes lib/modules/; it reaches
        # usr/lib/modules/ only through the merged-usr compat symlinks that
        # package staging pre-seeds. Both spellings must select the hook, and
        # both must yield the SAME kernel release — reading the release by
        # position rather than by name is what would break on the short form.
        for path in ("usr/lib/modules/6.6.50-igos/kernel/drivers/foo.ko",
                     "lib/modules/6.6.50-igos/kernel/drivers/foo.ko"):
            with self.subTest(path=path):
                invocation = self._invocation([path])
                self.assertTrue(invocation, f"{path} must fire depmod")
                self.assertIn(
                    "6.6.50-igos", invocation,
                    "depmod must be given the kernel release from the path, "
                    "whichever prefix the path carries")

    def test_does_not_fire_for_an_unrelated_path(self):
        self.assertEqual(
            self._invocation(["usr/share/modules/foo.txt", "usr/bin/foo"]), "",
            "a path that is not a kernel module tree must not fire depmod")


def _declared_shipped_paths():
    """Every path the recipes declare that they ship, from `verify_paths`."""
    out = {}
    for f in sorted(glob.glob(str(REPO_ROOT / "packages/*/*/package.yml"))):
        try:
            data = yaml.safe_load(open(f, encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        for p in data.get("verify_paths") or []:
            if isinstance(p, str):
                out.setdefault(p.lstrip("/"), set()).add(
                    data.get("name") or Path(f).parent.name)
    return out


class TriggerCurrencyAgainstTheTree(unittest.TestCase):
    """Every declared shipped path in a hook's class must select that hook.

    This is the check that was missing. The instance tests above cover the
    families we know about; these cover the ones we do not, by asking the tree
    what it actually ships instead of asking the pattern what it matches.
    """

    @classmethod
    def setUpClass(cls):
        cls.paths = _declared_shipped_paths()

    def test_the_tree_declares_paths_at_all(self):
        # An empty population would make every assertion below vacuous — the
        # zero has to be shown to be a real zero.
        self.assertGreater(
            len(self.paths), 500,
            "the recipes should declare hundreds of shipped paths; a small "
            "number here means the population was not read and the currency "
            "assertions below prove nothing")

    def test_no_declared_shared_library_is_invisible_to_the_cache_hook(self):
        lib_re = re.compile(r"[^/]+\.so(\.|$)")
        hook = _hook("ldconfig")
        missed = sorted(p for p in self.paths
                        if lib_re.search(p) and not hook.pattern.search(p))
        self.assertEqual(
            missed, [],
            "these declared shared libraries do not select the cache hook, so "
            "installing them leaves the cache stale and nothing says so:\n  "
            + "\n  ".join(f"{p}  [{','.join(sorted(self.paths[p]))}]"
                          for p in missed))

    def test_no_declared_unit_definition_is_invisible_to_the_reload_hook(self):
        unit_re = re.compile(
            r"^(usr/lib|etc)/systemd/system/[^/]+\."
            r"(service|socket|timer|path|mount|automount|target|slice|swap)$")
        hook = _hook("systemd-daemon-reload")
        missed = sorted(p for p in self.paths
                        if unit_re.search(p) and not hook.pattern.search(p))
        self.assertEqual(
            missed, [],
            "these declared unit definitions do not select the reload hook:\n  "
            + "\n  ".join(f"{p}  [{','.join(sorted(self.paths[p]))}]"
                          for p in missed))


if __name__ == "__main__":
    unittest.main()
