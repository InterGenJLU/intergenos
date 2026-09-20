# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Contract: a stage-only build can see the siblings already staged beside it.

A stage-only build (``--stage-only``) writes every package into ONE staging
system root instead of installing into the live filesystem. Nothing else about
the environment pointed at that root: ``PKG_CONFIG_LIBDIR`` named the HOST's
``/usr/lib/pkgconfig``, the compiler searched the HOST's ``/usr/include`` and
the linker the HOST's ``/usr/lib``. So a package whose dependency had been
staged minutes earlier into the same root could not find it, and configure
stopped — measured twice on a live machine (2026-09-19, sane-airscan and
simple-scan against a staged sane-backends).

Three things make the staged sibling visible, and a fourth keeps the staging
path out of what ships:

* the staged ``.pc`` files, COPIED into an overlay directory with their own
  absolute ``/usr`` paths re-prefixed onto the staging root, and that
  directory placed ahead of the host's search path. A staged ``.pc`` says
  ``prefix=/usr`` because that is where the package will live once installed,
  so reading it as-is sends the build to the host's ``/usr``.
  ``PKG_CONFIG_SYSROOT_DIR`` is NOT how this is done: it re-prefixes every
  ``.pc`` pkg-config reads, including the host's. Measured here 2026-09-19 —
  with the sysroot set, the host's ``libxml-2.0.pc`` answered
  ``-I<staging root>/usr/include/libxml2``, which does not exist, and
  sane-backends died on a missing ``libxml/parser.h``.
* ``-I``/``-L`` for the plain (non-pkg-config) probes autotools and cmake make.
* ``-Wl,-rpath-link`` and NOT ``-Wl,-rpath``: rpath-link resolves the
  dependencies of the libraries being linked AT LINK TIME and writes nothing
  into the binary, while ``-rpath`` would bake the staging path into the
  shipped artifact's RUNPATH — a build-host path leaking into a package.

In a chroot the staging root IS ``/``, the host paths already ARE the staged
ones, and none of this must be added — that case is pinned here too.
"""

import importlib
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.igos_build.factories import make_package  # noqa: E402

_builder_mod = importlib.import_module("igos-build.builder")
BuildExecutor = _builder_mod.BuildExecutor


def _executor(tmp: Path, system_root: Path, tracked: bool = False):
    return BuildExecutor(
        work_dir=tmp / "work",
        log_dir=tmp / "log",
        sources_dir=tmp / "sources",
        patches_dir=tmp / "patches",
        system_root=system_root,
        tracked=tracked,
    )


class TestStageOnlySeesStagedSiblings(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "staging-system-root"
        self.addCleanup(self._tmp.cleanup)

    def _env(self, **pkg_kwargs):
        ex = _executor(self.tmp, self.root)
        return ex.build_env(make_package(**pkg_kwargs))

    STAGED_PC = (
        "prefix=/usr\n"
        "exec_prefix=${prefix}\n"
        "libdir=/usr/lib\n"
        "includedir=${prefix}/include\n"
        "sane_libdir=/usr/lib/sane\n"
        "\n"
        "Name: sane-backends\n"
        "Version: 1.4.0\n"
        "Cflags: -I${includedir} -I/usr/include/sane\n"
        "Libs: -L${libdir} -lsane\n"
    )

    def _stage_a_pc(self, name="sane-backends.pc", where="usr/lib/pkgconfig"):
        d = self.root / where
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(self.STAGED_PC)
        return d / name

    def test_the_staged_pc_overlay_comes_before_the_host_search_path(self):
        self._stage_a_pc()
        env = self._env()
        first = Path(env["PKG_CONFIG_LIBDIR"].split(":")[0])
        self.assertTrue(
            (first / "sane-backends.pc").is_file(),
            "the first entry on the search path must be the overlay holding "
            "the staged .pc files, or a sibling staged minutes ago is "
            "invisible")
        self.assertIn("/usr/lib/pkgconfig", env["PKG_CONFIG_LIBDIR"].split(":"),
                      "the host's own search path must still follow")

    def test_the_flags_point_into_the_staging_root(self):
        self._stage_a_pc()
        env = self._env()
        overlay = Path(env["PKG_CONFIG_LIBDIR"].split(":")[0])
        text = (overlay / "sane-backends.pc").read_text()
        self.assertIn(f"Cflags: -I{self.root}/usr/include "
                      f"-I{self.root}/usr/include/sane", text,
                      "the compiler must reach the staged headers, and a "
                      "${prefix}-relative flag has to be expanded to get "
                      "there")
        self.assertIn(f"Libs: -L{self.root}/usr/lib -lsane", text)
        self.assertNotIn("-lsane -lsane", text)

    def test_the_variables_a_consumer_installs_by_are_left_alone(self):
        self._stage_a_pc()
        env = self._env()
        overlay = Path(env["PKG_CONFIG_LIBDIR"].split(":")[0])
        text = (overlay / "sane-backends.pc").read_text()
        for line in ("prefix=/usr", "libdir=/usr/lib",
                     "includedir=${prefix}/include",
                     "sane_libdir=/usr/lib/sane"):
            self.assertIn(
                line + "\n", text,
                "a consumer reads these to compute where it will install and "
                "then prepends DESTDIR: measured 2026-09-19, re-prefixing "
                "them made sane-airscan install its backend into "
                "$DESTDIR/<the whole staging path again>/usr/lib/sane")

    def test_the_host_pc_files_are_not_re_prefixed(self):
        self._stage_a_pc()
        env = self._env()
        self.assertNotIn(
            "PKG_CONFIG_SYSROOT_DIR", env,
            "the sysroot variable re-prefixes the HOST's .pc files too: "
            "measured 2026-09-19, the host libxml-2.0.pc then answered "
            "-I<staging root>/usr/include/libxml2, which does not exist, and "
            "sane-backends died on a missing libxml/parser.h")

    def test_the_overlay_is_not_written_inside_the_staging_root(self):
        self._stage_a_pc()
        env = self._env()
        overlay = Path(env["PKG_CONFIG_LIBDIR"].split(":")[0]).resolve()
        self.assertFalse(
            overlay.is_relative_to(self.root.resolve()),
            "the staging root becomes the package; the overlay must live "
            "outside it")

    def test_the_overlay_describes_what_is_staged_now(self):
        env = self._env()
        overlay = Path(env["PKG_CONFIG_LIBDIR"].split(":")[0])
        self.assertEqual(list(overlay.glob("*.pc")), [],
                         "nothing staged yet, nothing in the overlay")
        self._stage_a_pc()
        env = self._env()
        overlay = Path(env["PKG_CONFIG_LIBDIR"].split(":")[0])
        self.assertTrue((overlay / "sane-backends.pc").is_file(),
                        "a sibling staged since the last package's "
                        "environment was made must appear")

    def test_the_compiler_and_linker_search_the_staging_root(self):
        env = self._env()
        self.assertIn(f"-I{self.root}/usr/include", env["CPPFLAGS"])
        self.assertIn(f"-L{self.root}/usr/lib", env["LDFLAGS"])
        self.assertIn(f"-L{self.root}/usr/lib64", env["LDFLAGS"])

    def test_link_time_search_only_never_a_baked_runpath(self):
        env = self._env()
        self.assertIn(f"-Wl,-rpath-link,{self.root}/usr/lib", env["LDFLAGS"])
        self.assertNotIn(f"-Wl,-rpath,{self.root}", env["LDFLAGS"],
                         "a staging path in RUNPATH would ship inside the "
                         "package")
        self.assertNotIn("-Wl,-rpath=", env["LDFLAGS"])

    def test_a_32_bit_package_takes_the_32_bit_staged_pc_files(self):
        self._stage_a_pc(where="usr/lib32/pkgconfig")
        self._stage_a_pc(name="other.pc", where="usr/lib/pkgconfig")
        env = self._env(elf_class="32")
        overlay = Path(env["PKG_CONFIG_LIBDIR"].split(":")[0])
        self.assertTrue((overlay / "sane-backends.pc").is_file())
        self.assertFalse(
            (overlay / "other.pc").exists(),
            "a 32-bit package searches the 32-bit world only; a 64-bit "
            "staged .pc must not reach it")
        self.assertIn(f"-L{self.root}/usr/lib32", env["LDFLAGS"])
        self.assertNotIn(f"-L{self.root}/usr/lib64", env["LDFLAGS"])

    def test_in_a_chroot_the_host_paths_are_already_the_staged_ones(self):
        ex = _executor(self.tmp, Path("/"))
        env = ex.build_env(make_package())
        self.assertEqual(
            env["PKG_CONFIG_LIBDIR"],
            "/usr/lib/pkgconfig:/usr/lib64/pkgconfig:/usr/share/pkgconfig")
        self.assertNotIn("-rpath-link", env.get("LDFLAGS", ""))

    def test_tracked_builds_keep_their_own_per_package_staging(self):
        ex = _executor(self.tmp, self.root, tracked=True)
        env = ex.build_env(make_package(name="demo", version="1.0"))
        self.assertTrue(
            env["PKG_CONFIG_LIBDIR"].startswith(
                f"{ex.pkg_staging}/demo-1.0/usr/lib/pkgconfig"),
            "a tracked build stages per package and must keep looking in its "
            "own DESTDIR first")


if __name__ == "__main__":
    unittest.main()
