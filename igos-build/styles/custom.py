"""Custom build style — delegates to a build.sh script.

For complex packages (GCC, glibc, kernel) that don't fit standard patterns.
The build.sh lives alongside the package.yml and defines bash functions:
  configure(), build(), check(), install()
"""

from ..parser import Package
from .base import BuildStyle, BuildPhase


class CustomStyle(BuildStyle):
    """Custom builds via build.sh in the package template directory."""

    def _build_sh_path(self, pkg: Package) -> str:
        """Get the path to the build.sh script."""
        if pkg.template_path:
            return str(pkg.template_path.parent / "build.sh")
        return "build.sh"

    def patch(self, pkg: Package) -> BuildPhase:
        return BuildPhase(
            name="patch",
            commands=self._patch_commands(pkg),
        )

    def configure(self, pkg: Package) -> BuildPhase:
        script = self._build_sh_path(pkg)
        return BuildPhase(
            name="configure",
            commands=[
                f"source {script}",
                "type configure &>/dev/null && configure || true",
            ],
        )

    def build(self, pkg: Package) -> BuildPhase:
        script = self._build_sh_path(pkg)
        return BuildPhase(
            name="build",
            commands=[
                f"source {script}",
                "type build &>/dev/null && build || true",
            ],
        )

    def check(self, pkg: Package) -> BuildPhase:
        script = self._build_sh_path(pkg)
        return BuildPhase(
            name="check",
            commands=[
                f"source {script}",
                "type check &>/dev/null && check || true",
            ],
        )

    def install(self, pkg: Package) -> BuildPhase:
        script = self._build_sh_path(pkg)
        func = pkg.install_func  # "install" (toolchain) or "do_install" (core/base)
        return BuildPhase(
            name="install",
            commands=[
                f"source {script}",
                f"type {func} &>/dev/null && {func} || true",
            ],
        )
