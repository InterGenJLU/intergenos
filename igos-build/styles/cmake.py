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
        flags = " \\\n    ".join(shlex.quote(f) for f in pkg.configure_flags) if pkg.configure_flags else ""
        base = "cmake -B build -DCMAKE_INSTALL_PREFIX=/usr -DCMAKE_BUILD_TYPE=Release"

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
            commands=["cmake --build build --target test || true"],
        )

    def install(self, pkg: Package) -> BuildPhase:
        return BuildPhase(
            name="install",
            commands=["DESTDIR=${DESTDIR} cmake --install build"],
        )
