# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Plain Makefile build style — no configure step, just make."""

import shlex

from ..parser import Package
from .base import BuildStyle, BuildPhase


class MakeStyle(BuildStyle):
    """Plain Makefile projects with no configure script."""

    def patch(self, pkg: Package) -> BuildPhase:
        return BuildPhase(
            name="patch",
            commands=self._patch_commands(pkg),
        )

    def configure(self, pkg: Package) -> BuildPhase:
        # No configure step — flags become make variables
        return BuildPhase(
            name="configure",
            commands=[],
        )

    def build(self, pkg: Package) -> BuildPhase:
        flags = " ".join(shlex.quote(f) for f in pkg.configure_flags)
        if flags:
            cmd = f"make -j${{IGOS_JOBS}} {flags}"
        else:
            cmd = "make -j${IGOS_JOBS}"
        # elf_class-32: the sourced lib32 profile provides -m32 CC/CXX and
        # the pinned pkg-config dir (GE arc, G2/T2 — one definition).
        return BuildPhase(
            name="build",
            commands=self._lib32_wrap(pkg, [cmd]),
        )

    def check(self, pkg: Package) -> BuildPhase:
        # tests.jobs bounds suite parallelism for non-parallel-safe test
        # harnesses (a command-line -j overrides env MAKEFLAGS=-jN, GNU
        # make semantics) — e.g. libvorbis, whose BLFS instruction is
        # literally `make -j1 check`. None = inherit MAKEFLAGS.
        cmd = (f"make -j{pkg.tests_jobs} check"
               if pkg.tests_jobs else "make check")
        return BuildPhase(
            name="check",
            commands=self._lib32_wrap(pkg, [cmd]),
        )

    def install(self, pkg: Package) -> BuildPhase:
        # lib32 lane install = ALLOWLIST STAGED-COPY (see the meson style).
        if pkg.elf_class == "32":
            return BuildPhase(
                name="install",
                commands=self._lib32_wrap(pkg, [
                    'make DESTDIR="$PWD/m32root" PREFIX=/usr install',
                    'lib32_stage_libs "$PWD/m32root" && lib32_assert_only_lib32',
                ]),
            )
        return BuildPhase(
            name="install",
            commands=["make DESTDIR=${DESTDIR} PREFIX=/usr install"],
        )
