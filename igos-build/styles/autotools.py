"""Autotools build style — ./configure && make && make install.

Handles the vast majority of LFS packages: anything using the standard
GNU autoconf/automake/libtool build pattern.
"""

import shlex

from ..parser import Package
from .base import BuildStyle, BuildPhase


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

        return BuildPhase(
            name="configure",
            commands=[cmd],
        )

    def build(self, pkg: Package) -> BuildPhase:
        return BuildPhase(
            name="build",
            commands=["make -j${IGOS_JOBS}"],
        )

    def check(self, pkg: Package) -> BuildPhase:
        return BuildPhase(
            name="check",
            commands=["make check"],
        )

    def install(self, pkg: Package) -> BuildPhase:
        return BuildPhase(
            name="install",
            commands=["make DESTDIR=${DESTDIR} install"],
        )
