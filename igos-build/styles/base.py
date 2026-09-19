# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Base class for build styles.

Every build style produces shell commands for five phases:
  1. patch     — apply patches to the source tree
  2. configure — set up the build (./configure, cmake, meson setup, etc.)
  3. build     — compile (make, ninja, etc.)
  4. check     — run test suite (optional)
  5. install   — install to DESTDIR

Each phase returns a list of shell command strings. The build executor
runs them sequentially in the package's build directory.
"""

import shlex
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from ..parser import Package

# THE lib32 build profile, in its two tier-appropriate halves (GE arc, G2/T2
# one-definition rule — styles inject the MECHANISM; recipes never restate
# the values):
#   - meson consumers take the cross file (env vars cannot retarget Rust,
#     config tools, or CMake probes — the RT-7 leakage class);
#   - autotools/make consumers source the bash profile per command (the
#     builder runs every command in its OWN bash subprocess, so env never
#     persists — each command re-sources, and nothing can leak between
#     packages on this tier by construction).
#
# BOTH files belong to the checkout the build is running out of, not to a fixed
# absolute path. The repository is bind-mounted at /mnt/intergenos inside the
# build chroot, so a chroot build's own computed root IS that path and the
# resolution below returns exactly these two strings; they stay as the LAST
# fallback for a caller whose tree carries neither file.
CHROOT_LIB32_CROSS_FILE = "/mnt/intergenos/config/lib32/lib32-cross.ini"
CHROOT_LIB32_ENV_SCRIPT = "/mnt/intergenos/scripts/lib32-env.sh"

_LIB32_CROSS_RELATIVE = Path("config") / "lib32" / "lib32-cross.ini"
_LIB32_ENV_RELATIVE = Path("scripts") / "lib32-env.sh"
_MODULE_ROOT = Path(__file__).resolve().parents[2]


def resolve_tree_file(template_path, relative, fallback, module_root=None) -> str:
    """Return the path of a repository file a build of this recipe must use.

    The file belongs to the checkout the build is running out of: on a live
    machine a build driven from a second checkout (a lane worktree, a clone, a
    review tree) took the RECIPE from that checkout and its build inputs from
    ``/mnt/intergenos``, a different tree at a different commit, with nothing
    saying so.

    Order:
      1. the checkout the RECIPE is in — the directory above its ``packages/``
         tree;
      2. the checkout this builder module itself lives in;
      3. ``fallback`` (the chroot's bind-mount path), when neither has the file.

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
        candidate = root / relative
        if candidate.is_file():
            return str(candidate)
    return fallback


def lib32_cross_file(pkg: Package, module_root=None,
                     fallback: str = CHROOT_LIB32_CROSS_FILE) -> str:
    """The meson cross file a 32-bit build of this package must be configured with."""
    return resolve_tree_file(getattr(pkg, "template_path", None),
                             _LIB32_CROSS_RELATIVE, fallback, module_root)


def lib32_env_script(pkg: Package, module_root=None,
                     fallback: str = CHROOT_LIB32_ENV_SCRIPT) -> str:
    """The bash 32-bit build profile this package's phase commands must source."""
    return resolve_tree_file(getattr(pkg, "template_path", None),
                             _LIB32_ENV_RELATIVE, fallback, module_root)


def lib32_env_source(pkg: Package, module_root=None,
                     fallback: str = CHROOT_LIB32_ENV_SCRIPT) -> str:
    """The `source <profile>` command every 32-bit env-lane command begins with."""
    return f"source {lib32_env_script(pkg, module_root, fallback)}"


@dataclass
class BuildPhase:
    """Commands for a single build phase."""
    name: str
    commands: list[str] = field(default_factory=list)
    workdir: str | None = None      # cd here before running (relative to source root)
    env: dict[str, str] = field(default_factory=dict)


class BuildStyle(ABC):
    """Abstract base for build styles."""

    @abstractmethod
    def patch(self, pkg: Package) -> BuildPhase:
        """Generate patch commands."""

    @abstractmethod
    def configure(self, pkg: Package) -> BuildPhase:
        """Generate configure commands."""

    @abstractmethod
    def build(self, pkg: Package) -> BuildPhase:
        """Generate build/compile commands."""

    @abstractmethod
    def check(self, pkg: Package) -> BuildPhase:
        """Generate test suite commands."""

    @abstractmethod
    def install(self, pkg: Package) -> BuildPhase:
        """Generate install commands."""

    def post_install(self, pkg: Package) -> BuildPhase:
        """Generate post-install commands (runs on live filesystem, not DESTDIR).

        Default: no-op. Override in styles that support post_install hooks
        (currently custom style only).
        """
        return BuildPhase(name="post_install", commands=[])

    def all_phases(self, pkg: Package) -> list[BuildPhase]:
        """Return all phases in order.

        Note: post_install is NOT included here — it runs after package
        tracking (deploy), not as a regular build phase. The builder
        handles it separately so it executes on the live filesystem.
        """
        return [
            self.patch(pkg),
            self.configure(pkg),
            self.build(pkg),
            self.check(pkg),
            self.install(pkg),
        ]

    def lib32_paths(self, pkg: Package) -> dict[str, str]:
        """The 32-bit build inputs this style will use, for the build log.

        Empty for every 64-bit package (the zero-behavior-change guarantee on
        the 64-bit tree). The env-consuming styles (autotools/make) use the
        bash profile; the meson style overrides this to add the cross file.
        """
        if pkg.elf_class != "32":
            return {}
        return {"build profile": lib32_env_script(pkg)}

    def _lib32_wrap(self, pkg: Package, commands: list[str]) -> list[str]:
        """Prefix every command with the lib32 profile source for an
        elf_class-32 package; identity for everything else (the zero-
        behavior-change guarantee on the 64-bit tree). Used by the
        env-consuming styles (autotools/make); meson uses the cross file."""
        if pkg.elf_class != "32":
            return commands
        return [f"{lib32_env_source(pkg)}; {c}" for c in commands]

    def _patch_commands(self, pkg: Package) -> list[str]:
        """Standard patch application — shared across styles.

        Verifies SHA256 checksum before applying each patch when a
        checksum is declared in the package template.

        Supports compressed patches: .gz files are decompressed via zcat
        before piping to patch. SHA256 is verified on the compressed file
        (matches what's on disk).
        """
        commands = []
        for entry in pkg.patches:
            patch_path = f"$IGOS_PATCHES/{shlex.quote(entry.file)}"
            commands.append(f'echo "Applying patch: {shlex.quote(entry.file)}"')
            if entry.sha256:
                commands.append(
                    f'echo "{shlex.quote(entry.sha256)}  {patch_path}" | sha256sum -c - '
                    f'|| {{ echo "FATAL: Checksum mismatch for {shlex.quote(entry.file)}"; exit 1; }}'
                )
            if entry.file.endswith('.gz'):
                commands.append(f"zcat {patch_path} | patch -Np1")
            elif entry.file.endswith('.bz2'):
                commands.append(f"bzcat {patch_path} | patch -Np1")
            elif entry.file.endswith('.xz'):
                commands.append(f"xzcat {patch_path} | patch -Np1")
            else:
                commands.append(f"patch -Np1 -i {patch_path}")
        return commands
