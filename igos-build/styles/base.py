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

from ..parser import Package


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
