"""Plain Makefile build style — no configure step, just make."""

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
        flags = " ".join(pkg.configure_flags)
        if flags:
            cmd = f"make -j${{IGOS_JOBS}} {flags}"
        else:
            cmd = "make -j${IGOS_JOBS}"
        return BuildPhase(
            name="build",
            commands=[cmd],
        )

    def check(self, pkg: Package) -> BuildPhase:
        return BuildPhase(
            name="check",
            commands=["make check || true"],
        )

    def install(self, pkg: Package) -> BuildPhase:
        return BuildPhase(
            name="install",
            commands=["make DESTDIR=${DESTDIR} PREFIX=/usr install"],
        )
