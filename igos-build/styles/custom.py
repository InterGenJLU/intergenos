# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Custom build style — delegates to a build.sh script.

For complex packages (GCC, glibc, kernel) that don't fit standard patterns.
The build.sh lives alongside the package.yml and defines bash functions:
  configure(), build(), check(), install() or do_install()
"""

from pathlib import Path

from ..parser import Package
from .base import BuildStyle, BuildPhase

# The repository is bind-mounted at this absolute path inside the build chroot,
# so a chroot build's own computed root IS this path and resolution below
# returns exactly this string. It stays as the LAST fallback for a caller whose
# tree carries no helper at all.
CHROOT_PKG_FUNCTIONS = "/mnt/intergenos/scripts/pkg-functions.sh"

_HELPER_RELATIVE = Path("scripts") / "pkg-functions.sh"
_MODULE_ROOT = Path(__file__).resolve().parents[2]


def resolve_pkg_functions(template_path, module_root=None, fallback=CHROOT_PKG_FUNCTIONS) -> str:
    """Return the shell helper path a build of this recipe must source.

    The helper belongs to the checkout the build is running out of, not to a
    fixed absolute path: on a live machine a build driven from a second
    checkout (a lane worktree, a clone, a review tree) used to take the recipe
    from that checkout and the helper from ``/mnt/intergenos``, a different
    tree at a different commit, with nothing saying so.

    Order:
      1. the checkout the RECIPE is in — the directory above its ``packages/``
         tree;
      2. the checkout this builder module itself lives in;
      3. ``fallback`` (the chroot's bind-mount path), when neither has one.

    Inside the chroot step 1 already IS ``/mnt/intergenos``, so the composed
    command is unchanged there.
    """
    roots: list[Path] = []
    if template_path is not None:
        resolved = Path(template_path).resolve()
        for parent in resolved.parents:
            if parent.name == "packages":
                roots.append(parent.parent)
                break
    roots.append(Path(module_root) if module_root is not None else _MODULE_ROOT)

    seen: set[Path] = set()
    for root in roots:
        if root in seen:
            continue
        seen.add(root)
        candidate = root / _HELPER_RELATIVE
        if candidate.is_file():
            return str(candidate)
    return fallback


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
    def pkg_functions_path(self, pkg: Package) -> str:
        """The shell helper this package's phases will source, as a path."""
        return resolve_pkg_functions(pkg.template_path)

    def _pkg_funcs(self, pkg: Package) -> str:
        return f"source {self.pkg_functions_path(pkg)} && source_profile_d && "

    def configure(self, pkg: Package) -> BuildPhase:
        script = self._build_sh_path(pkg)
        return BuildPhase(
            name="configure",
            commands=[
                f"{self._pkg_funcs(pkg)}source {script} || {{ echo 'FATAL: failed to source {script}'; exit 1; }}; "
                f"if declare -f configure >/dev/null 2>&1; then configure; fi",
            ],
        )

    def build(self, pkg: Package) -> BuildPhase:
        script = self._build_sh_path(pkg)
        return BuildPhase(
            name="build",
            commands=[
                f"{self._pkg_funcs(pkg)}source {script} && if declare -f build >/dev/null 2>&1; then build; fi",
            ],
        )

    def check(self, pkg: Package) -> BuildPhase:
        script = self._build_sh_path(pkg)
        return BuildPhase(
            name="check",
            commands=[
                f"{self._pkg_funcs(pkg)}source {script} && if declare -f check >/dev/null 2>&1; then check; fi",
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
                f"{self._pkg_funcs(pkg)}source {script} && "
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
                f"{self._pkg_funcs(pkg)}source {script} && if declare -f post_install >/dev/null 2>&1; then post_install; fi",
            ],
        )
