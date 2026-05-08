"""Custom build style — delegates to a build.sh script.

For complex packages (GCC, glibc, kernel) that don't fit standard patterns.
The build.sh lives alongside the package.yml and defines bash functions:
  configure(), build(), check(), install() or do_install()
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

    # pkg-functions.sh defines pkg_run_tests (test-allow-list policy wrapper),
    # verify_source_checksum, etc. — sourced into every phase shell so package
    # build.sh files can call them. chroot-build-ch8.sh and core-extra source
    # this at top; Python builder must do the same. Halt #4 (Build #6, cpio
    # 2.15 [CHECK] exit 127) was the symptom of this gap.
    _PKG_FUNCS = "source /mnt/intergenos/scripts/pkg-functions.sh && "

    def configure(self, pkg: Package) -> BuildPhase:
        script = self._build_sh_path(pkg)
        return BuildPhase(
            name="configure",
            commands=[
                f"{self._PKG_FUNCS}source {script} || {{ echo 'FATAL: failed to source {script}'; exit 1; }}; "
                f"if declare -f configure >/dev/null 2>&1; then configure; fi",
            ],
        )

    def build(self, pkg: Package) -> BuildPhase:
        script = self._build_sh_path(pkg)
        return BuildPhase(
            name="build",
            commands=[
                f"{self._PKG_FUNCS}source {script} && if declare -f build >/dev/null 2>&1; then build; fi",
            ],
        )

    def check(self, pkg: Package) -> BuildPhase:
        script = self._build_sh_path(pkg)
        return BuildPhase(
            name="check",
            commands=[
                f"{self._PKG_FUNCS}source {script} && if declare -f check >/dev/null 2>&1; then check; fi",
            ],
        )

    def install(self, pkg: Package) -> BuildPhase:
        script = self._build_sh_path(pkg)
        func = pkg.install_func  # "install" (toolchain) or "do_install" (core/base)
        return BuildPhase(
            name="install",
            commands=[
                f"{self._PKG_FUNCS}source {script} && if declare -f {func} >/dev/null 2>&1; then {func}; fi",
            ],
        )

    def post_install(self, pkg: Package) -> BuildPhase:
        """Post-install hooks that run on the live filesystem (not in DESTDIR).

        Used for things like catalog registration, user/group creation,
        systemd enable, config file generation, etc.
        """
        script = self._build_sh_path(pkg)
        return BuildPhase(
            name="post_install",
            commands=[
                f"{self._PKG_FUNCS}source {script} && if declare -f post_install >/dev/null 2>&1; then post_install; fi",
            ],
        )
