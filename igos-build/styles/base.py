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

    def all_phases(self, pkg: Package) -> list[BuildPhase]:
        """Return all phases in order."""
        return [
            self.patch(pkg),
            self.configure(pkg),
            self.build(pkg),
            self.check(pkg),
            self.install(pkg),
        ]

    def _patch_commands(self, pkg: Package) -> list[str]:
        """Standard patch application — shared across styles."""
        commands = []
        for patch_file in pkg.patches:
            commands.append(f'echo "Applying patch: {patch_file}"')
            commands.append(f"patch -Np1 -i $IGOS_PATCHES/{patch_file}")
        return commands
