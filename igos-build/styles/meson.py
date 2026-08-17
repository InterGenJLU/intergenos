# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Meson build style — meson setup, ninja, ninja install."""

import shlex

from ..parser import Package
from .base import BuildStyle, BuildPhase, LIB32_CROSS_FILE, LIB32_ENV_SOURCE


class MesonStyle(BuildStyle):
    """Meson + Ninja build system."""

    def patch(self, pkg: Package) -> BuildPhase:
        return BuildPhase(
            name="patch",
            commands=self._patch_commands(pkg),
        )

    def configure(self, pkg: Package) -> BuildPhase:
        flags = " \\\n    ".join(shlex.quote(f) for f in pkg.configure_flags) if pkg.configure_flags else ""
        # --wrap-mode=nodownload prevents meson from fetching subprojects
        # at configure time, enforcing the offline chroot build model
        if pkg.elf_class == "32":
            # lib32 lane (GE arc, G2): the cross file pins EVERY tool the
            # build may consult (compilers, the rustc target, the pkg-config
            # wrapper, llvm-config32) — env vars cannot retarget those (the
            # RT-7 leakage class). libdir passed explicitly too:
            # deterministic over cross-file defaults.
            base = (
                f"meson setup build --cross-file {LIB32_CROSS_FILE} "
                "--prefix=/usr --libdir=/usr/lib32 --buildtype=release "
                "--wrap-mode=nodownload"
            )
        else:
            base = "meson setup build --prefix=/usr --libdir=/usr/lib --buildtype=release --wrap-mode=nodownload"

        if flags:
            cmd = f"{base} \\\n    {flags}"
        else:
            cmd = base

        return BuildPhase(
            name="configure",
            commands=[cmd],
        )

    def build(self, pkg: Package) -> BuildPhase:
        # elf_class-32 packages build VERBOSE by mandate (RT-8/F2-a): the
        # archive-time time64 log assertion refuses a log with no visible
        # compile lines, so quiet ninja would fail that gate by design.
        if pkg.elf_class == "32":
            return BuildPhase(
                name="build",
                commands=["ninja -v -C build -j${IGOS_JOBS}"],
            )
        return BuildPhase(
            name="build",
            commands=["ninja -C build -j${IGOS_JOBS}"],
        )

    def check(self, pkg: Package) -> BuildPhase:
        return BuildPhase(
            name="check",
            commands=["ninja -C build test"],
        )

    def install(self, pkg: Package) -> BuildPhase:
        # lib32 lane install = ALLOWLIST STAGED-COPY (the bash-tier
        # lib32_stage_libs discipline, same helpers): a full ninja install
        # into ${DESTDIR} would ship headers/binaries that collide with the
        # 64-bit sibling's payload. Install into a private root, stage ONLY
        # /usr/lib32, and fail-loud assert nothing else was staged.
        if pkg.elf_class == "32":
            return BuildPhase(
                name="install",
                commands=[
                    'DESTDIR="$PWD/m32root" ninja -C build install',
                    f'{LIB32_ENV_SOURCE}; lib32_stage_libs "$PWD/m32root" && lib32_assert_only_lib32',
                ],
            )
        return BuildPhase(
            name="install",
            commands=["DESTDIR=${DESTDIR} ninja -C build install"],
        )
