# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Autotools build style — ./configure && make && make install.

Handles the vast majority of LFS packages: anything using the standard
GNU autoconf/automake/libtool build pattern.
"""

import shlex

from ..parser import Package
from .base import BuildStyle, BuildPhase

# Mechanism flags the lib32 lane injects into every elf_class-32 autotools
# configure (GE arc, G2/T2 — recipes never restate these): the 32-bit host
# triplet from the sourced profile, the lib32 libdir, and forced-verbose
# compile lines (RT-8/F2-a: the archive-time log assertion refuses a log
# with no visible compile evidence, so silent rules would fail the gate).
LIB32_CONFIGURE_FLAGS = "--host=${LIB32_HOST} --libdir=/usr/lib32 --disable-silent-rules"


class AutotoolsStyle(BuildStyle):
    """Standard autotools: configure, make, make check, make install."""

    def patch(self, pkg: Package) -> BuildPhase:
        return BuildPhase(
            name="patch",
            commands=self._patch_commands(pkg),
        )

    def configure(self, pkg: Package) -> BuildPhase:
        flags = " \\\n    ".join(shlex.quote(f) for f in pkg.configure_flags) if pkg.configure_flags else ""

        if flags:
            cmd = f"./configure \\\n    {flags}"
        else:
            cmd = "./configure --prefix=/usr"

        if pkg.elf_class == "32":
            cmd = f"{cmd} \\\n    {LIB32_CONFIGURE_FLAGS}"

        return BuildPhase(
            name="configure",
            commands=self._lib32_wrap(pkg, [cmd]),
        )

    def build(self, pkg: Package) -> BuildPhase:
        return BuildPhase(
            name="build",
            commands=self._lib32_wrap(pkg, ["make -j${IGOS_JOBS}"]),
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
        # lib32 lane install = ALLOWLIST STAGED-COPY (see the meson style):
        # a full make install would ship headers/binaries colliding with
        # the 64-bit sibling. Private root → stage /usr/lib32 only → assert.
        if pkg.elf_class == "32":
            return BuildPhase(
                name="install",
                commands=self._lib32_wrap(pkg, [
                    'make DESTDIR="$PWD/m32root" install',
                    'lib32_stage_libs "$PWD/m32root" && lib32_assert_only_lib32',
                ]),
            )
        return BuildPhase(
            name="install",
            commands=["make DESTDIR=${DESTDIR} install"],
        )
