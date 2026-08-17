# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
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
    # verify_source_checksum, source_profile_d, etc. — sourced into every phase
    # shell so package build.sh files can call them. chroot-build-ch8.sh and
    # core-extra source this at top; Python builder must do the same. Halt #4
    # (Build #6, cpio 2.15 [CHECK] exit 127) was the symptom of this gap.
    #
    # source_profile_d after sourcing pkg-functions.sh refreshes PATH from
    # /etc/profile.d/*.sh so packages like rust (installs cargo to
    # /opt/rustc/bin via /etc/profile.d/rustc.sh) are visible to subsequent
    # builds. Build #9 resume #8 cargo-c halt (exit 127 "cargo: command not
    # found") was the symptom of this gap.
    _PKG_FUNCS = "source /mnt/intergenos/scripts/pkg-functions.sh && source_profile_d && "

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
        # The install function is REQUIRED when a build.sh exists: silently
        # running a no-op install (the old `if declare -f` with no else)
        # let a typo'd or missing do_install produce a green phase whose
        # staging held only overlay/license content — the package recorded
        # built while its real program was absent (Rule 11/21: a stub is a
        # lie). configure/build/check remain optional — data-only and
        # install-only recipes legitimately omit them; every package in the
        # tree defines its install function (verified over all 922 build.sh
        # files at the time this gate landed).
        return BuildPhase(
            name="install",
            commands=[
                f"{self._PKG_FUNCS}source {script} && "
                f"if declare -f {func} >/dev/null 2>&1; then {func}; else "
                f"echo \"FATAL: {script} defines no {func}() — a build.sh "
                f"package must implement its install function\" >&2; exit 1; fi",
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
