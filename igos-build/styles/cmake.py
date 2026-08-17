# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""CMake build style — cmake, make/ninja, make install."""

import shlex

from ..parser import Package
from .base import BuildStyle, BuildPhase


class CMakeStyle(BuildStyle):
    """CMake with out-of-tree build in a 'build' subdirectory."""

    def patch(self, pkg: Package) -> BuildPhase:
        return BuildPhase(
            name="patch",
            commands=self._patch_commands(pkg),
        )

    def configure(self, pkg: Package) -> BuildPhase:
        # FAIL-CLOSED lib32 guard (GE arc): the AUTO cmake style has no lib32
        # lane — a pure-yml cmake build cannot be correctly retargeted to
        # 32-bit here (find_library and feature probes would silently answer
        # from the 64-bit world — the RT-7 leakage class). The supported
        # path is build_style custom consuming THE toolchain file
        # config/lib32/lib32-cmake-toolchain.cmake explicitly, the way
        # lib32-llvm models it. Refuse loudly rather than build wrong.
        if pkg.elf_class == "32":
            raise ValueError(
                f"{pkg.name}: elf_class 32 with build_style cmake — the auto "
                "cmake style has no lib32 lane. Use build_style custom and "
                "consume config/lib32/lib32-cmake-toolchain.cmake explicitly "
                "(see lib32-llvm); refusing to configure a silently-64-bit "
                "lib32 build."
            )
        flags = " \\\n    ".join(shlex.quote(f) for f in pkg.configure_flags) if pkg.configure_flags else ""
        # CMake 4.x removed compatibility with cmake_minimum_required(VERSION <3.5).
        # Auto-inject CMAKE_POLICY_VERSION_MINIMUM=3.5 so older CMakeLists.txt
        # files (libqrencode etc.) configure cleanly without per-package patches.
        # Per CMake's own error-message workaround. Safe to set globally because
        # it only affects packages whose minimum version is below 3.5; modern
        # packages ignore it.
        base = "cmake -B build -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DCMAKE_INSTALL_PREFIX=/usr -DCMAKE_BUILD_TYPE=Release"

        if flags:
            cmd = f"{base} \\\n    {flags}"
        else:
            cmd = base

        return BuildPhase(
            name="configure",
            commands=["mkdir -pv build", cmd],
        )

    def build(self, pkg: Package) -> BuildPhase:
        return BuildPhase(
            name="build",
            commands=["cmake --build build -j${IGOS_JOBS}"],
        )

    def check(self, pkg: Package) -> BuildPhase:
        return BuildPhase(
            name="check",
            commands=["cmake --build build --target test"],
        )

    def install(self, pkg: Package) -> BuildPhase:
        return BuildPhase(
            name="install",
            commands=["DESTDIR=${DESTDIR} cmake --install build"],
        )
