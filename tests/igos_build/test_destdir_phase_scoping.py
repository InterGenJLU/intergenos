"""Contract: DESTDIR is scoped to the INSTALL phase only.

The staging root DESTDIR names is written by the install phase and archived by
the tracker. If it leaks into configure/build/check, a build-phase `make install`
(directly, or inside a bundled sub-build / language build-runner) silently
relocates its output under the staging root instead of the tree the build then
reads — the DESTDIR build-phase-redirect class (zig stage3, hadrian in-tree
libffi, julia bundled LLVM, pip/cmake wheel backends). BuildExecutor.phase_env
is the seam that makes that impossible by construction; these tests pin the
contract fail-loud so a future edit cannot quietly re-widen DESTDIR's scope.
"""

import importlib
import inspect
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
_builder_mod = importlib.import_module("igos-build.builder")
BuildExecutor = _builder_mod.BuildExecutor


def _phase_env(env, phase_name):
    # phase_env does not read instance state; exercise it as an unbound method
    # with a dummy self so this contract never couples to BuildExecutor's
    # constructor surface.
    return BuildExecutor.phase_env(None, env, phase_name)


class TestDestdirPhaseScoping(unittest.TestCase):
    BASE = {
        "PATH": "/tmp/staging/foo-1.0/usr/bin:/usr/bin",
        "DESTDIR": "/tmp/staging/foo-1.0",
        "CFLAGS": "-march=x86-64-v2 -O2",
        # multi-pass sysroot pointers — must stay in EVERY phase (they let a
        # multi-pass build compile against its own already-staged output)
        "PKG_CONFIG_LIBDIR": "/tmp/staging/foo-1.0/usr/lib/pkgconfig:/usr/lib/pkgconfig",
        "LD_LIBRARY_PATH": "/tmp/staging/foo-1.0/usr/lib",
    }

    def test_install_phase_keeps_destdir(self):
        out = _phase_env(dict(self.BASE), "install")
        self.assertIn("DESTDIR", out)
        self.assertEqual(out["DESTDIR"], "/tmp/staging/foo-1.0")

    def test_build_phases_drop_destdir(self):
        for ph in ("patch", "configure", "build", "check", "post_install"):
            with self.subTest(phase=ph):
                out = _phase_env(dict(self.BASE), ph)
                self.assertNotIn(
                    "DESTDIR", out,
                    f"the {ph} phase must never carry DESTDIR (build-phase "
                    f"installs would silently redirect under the staging root)")

    def test_non_destdir_keys_preserved_in_every_phase(self):
        for ph in ("patch", "configure", "build", "check", "install", "post_install"):
            with self.subTest(phase=ph):
                out = _phase_env(dict(self.BASE), ph)
                self.assertEqual(out["PATH"], self.BASE["PATH"])
                self.assertEqual(out["CFLAGS"], self.BASE["CFLAGS"])
                # the staging sysroot pointers are NOT DESTDIR and stay put
                self.assertEqual(out["PKG_CONFIG_LIBDIR"], self.BASE["PKG_CONFIG_LIBDIR"])
                self.assertEqual(out["LD_LIBRARY_PATH"], self.BASE["LD_LIBRARY_PATH"])

    def test_caller_env_not_mutated(self):
        src = dict(self.BASE)
        _phase_env(src, "build")
        self.assertIn("DESTDIR", src,
                      "phase_env must return a copy, never mutate the caller's env")

    def test_direct_install_no_destdir_is_a_noop(self):
        # direct_install packages never have DESTDIR in env; scoping is a clean
        # no-op for them in every phase.
        env = {"PATH": "/usr/bin", "CFLAGS": "-O2"}
        for ph in ("build", "install"):
            with self.subTest(phase=ph):
                out = _phase_env(dict(env), ph)
                self.assertNotIn("DESTDIR", out)
                self.assertEqual(out["PATH"], "/usr/bin")

    def test_wiring_build_package_routes_every_phase_through_the_seam(self):
        # Regression guard on the WIRING (keyed to the seam call, not to
        # incidental structure): both the phase loop and the post_install step
        # inside build_package must derive their env via phase_env — otherwise a
        # build-phase install could inherit DESTDIR again despite this contract.
        src = inspect.getsource(BuildExecutor.build_package)
        self.assertIn(
            "self.phase_env(env, phase.name)", src,
            "the build phase loop must scope each phase's env via phase_env")
        self.assertIn(
            'self.phase_env(env, "post_install")', src,
            "post_install must route through the same phase_env seam")


if __name__ == "__main__":
    unittest.main()
