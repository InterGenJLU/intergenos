"""Meson build style — meson setup, ninja, ninja install."""

import shlex

from ..parser import Package
from .base import BuildStyle, BuildPhase


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
        return BuildPhase(
            name="install",
            commands=["DESTDIR=${DESTDIR} ninja -C build install"],
        )
