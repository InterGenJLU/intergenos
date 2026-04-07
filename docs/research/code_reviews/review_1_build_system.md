# Code Review Request: InterGenOS Build System (igos-build)

I'm requesting a thorough code review of the Python build system for InterGenOS, a Linux distribution built entirely from source following Linux From Scratch (LFS 13.0) and Beyond LFS (BLFS 13.0).

This build system is responsible for parsing ~470 YAML package templates, resolving dependency graphs, and orchestrating the compilation and installation of every package in the distribution. It executes inside an offline chroot environment — no internet access is available during builds. All source tarballs are pre-downloaded to a local directory.

The system handles DESTDIR staging (installing to a temporary directory before deploying to the live filesystem), Slackware-style manifest generation for file tracking, and `.igos.tar.gz` archive creation for each package. It supports `--skip-built` to resume interrupted builds by checking for existing manifests.

I would appreciate your assessment of the following areas in particular:

1. **Dependency resolution and cycle detection** — Is the topological sort correct? Are cycles properly reported?
2. **DESTDIR staging safety** — The deploy step uses a tar pipe from staging to `/`. There is a pre-deploy check for top-level directory collisions with root symlinks (`/lib → usr/lib`, etc.). Is this sufficient?
3. **Error handling** — Are build failures caught, logged, and reported consistently? Are there paths where errors could be silently swallowed?
4. **The `--skip-built` logic** — Currently checks manifest file existence only, not file integrity. Is this the right trade-off?
5. **Security** — Command injection via package names or version strings? Path traversal in archive extraction? Shell injection in `run_command()`?
6. **General code quality** — Maintainability, clarity, edge cases.

The complete source follows. There are 14 files totaling approximately 2,000 lines of Python.

---

## Source Code

### igos-build.py
```python
#!/usr/bin/env python3
"""Wrapper script to run igos-build from any directory.

Usage:
    python3 /mnt/intergenos/igos-build.py [args...]

    Or from /mnt/intergenos:
    python3 -m igos-build [args...]
"""

import os
import sys

# Ensure we can find the igos-build package
project_root = os.path.dirname(os.path.abspath(__file__))
os.chdir(project_root)
sys.path.insert(0, project_root)

# Python doesn't allow hyphens in package names with -m,
# so we import the package by manipulating the path
import importlib
pkg = importlib.import_module("igos-build.__main__")
pkg.main()
```

### __init__.py
```python
"""igos-build — InterGenOS build system."""

__version__ = "0.1.0"
```

### __main__.py
```python
"""Entry point for igos-build: python -m igos-build

Usage:
    python -m igos-build                            Parse templates, show build order
    python -m igos-build --dry-run                  Show what commands would run
    python -m igos-build --build                    Actually build packages
    python -m igos-build --build --tracked          Build with package tracking
    python -m igos-build --build --skip-built       Skip packages with existing manifests
    python -m igos-build --only <name>              Build only one package
    python -m igos-build --tier desktop             Build only one tier
    python -m igos-build --sources-dir /sources     Override sources directory
"""

import sys
from pathlib import Path

from . import __version__
from .parser import load_all_packages, TemplateError
from .graph import build_graph, CycleError, MissingDependencyError
from .styles import get_style
from .builder import BuildExecutor


# Default paths (relative to project root)
PROJECT_ROOT = Path(__file__).parent.parent
PACKAGES_DIR = PROJECT_ROOT / "packages"
WORK_DIR = PROJECT_ROOT / "build" / "work"
LOG_DIR = PROJECT_ROOT / "build" / "logs"
SOURCES_DIR = PROJECT_ROOT / "build" / "sources"
PATCHES_DIR = PROJECT_ROOT / "build" / "patches"  # overridden to sources_dir below
SYSTEM_ROOT = PROJECT_ROOT / "build" / "system"


def main():
    args = sys.argv[1:]
    verbose = "--verbose" in args or "-v" in args
    dry_run = "--dry-run" in args
    do_build = "--build" in args
    tracked = "--tracked" in args
    skip_built = "--skip-built" in args
    only_pkg = None
    tier_filter = None
    sources_dir = SOURCES_DIR
    if "--only" in args:
        idx = args.index("--only")
        if idx + 1 < len(args):
            only_pkg = args[idx + 1]
    if "--tier" in args:
        idx = args.index("--tier")
        tier_filter = []
        for a in args[idx+1:]:
            if a.startswith("--"):
                break
            tier_filter.append(a)
    if "--sources-dir" in args:
        idx = args.index("--sources-dir")
        if idx + 1 < len(args):
            sources_dir = Path(args[idx + 1])

    print(f"igos-build v{__version__}")
    print(f"Scanning: {PACKAGES_DIR}\n")

    # --- Parse all templates ---
    try:
        all_packages = load_all_packages(PACKAGES_DIR)
    except TemplateError as e:
        print(f"TEMPLATE ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # When tier filtering, load ALL packages for the dependency graph
    # but only BUILD packages in the requested tier(s).
    # This ensures cross-tier dependencies are properly resolved.
    if tier_filter:
        packages = [p for p in all_packages if p.tier in tier_filter]
        print(f"Filtered to tier(s): {', '.join(tier_filter)}")
        print(f"  {len(packages)} packages to build (from {len(all_packages)} total)\n")
    else:
        packages = all_packages

    for pkg in packages:
        sources = ", ".join(s.url.split("/")[-1] for s in pkg.source)
        deps_count = (len(pkg.dependencies.build)
                      + len(pkg.dependencies.host)
                      + len(pkg.dependencies.runtime))
        flags_count = len(pkg.configure_flags)
        checks_count = len(pkg.validation)

        print(f"  [{pkg.tier}] {pkg.name} {pkg.version}-{pkg.release}")
        print(f"    style: {pkg.build_style}  |  deps: {deps_count}  |  flags: {flags_count}  |  checks: {checks_count}")
        print(f"    source: {sources}")
        if pkg.pass_number:
            print(f"    pass: {pkg.pass_number}  |  target: {pkg.target_triple}")
        print()

    # --- Build dependency graph ---
    # Always use ALL packages for the graph so cross-tier deps are resolved
    print("=" * 60)
    print("Building dependency graph...\n")

    try:
        graph = build_graph(all_packages, strict=False)
        order = graph.build_order()
        # Filter build order to only include requested tiers
        if tier_filter:
            order = [p for p in order if p.tier in tier_filter]
    except CycleError as e:
        print(f"CYCLE ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except MissingDependencyError as e:
        print(f"DEPENDENCY ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # Filter to single package if --only
    if only_pkg:
        order = [p for p in order if p.name == only_pkg]
        if not order:
            print(f"ERROR: no package named '{only_pkg}'", file=sys.stderr)
            sys.exit(1)

    graph.print_order(order)

    # --- Show build phases (dry run or verbose) ---
    if dry_run or verbose:
        print("\n" + "=" * 60)
        print("Build phases (dry run):\n")

        for pkg in order:
            style = get_style(pkg.build_style)
            phases = style.all_phases(pkg)

            print(f"--- {pkg.name} {pkg.version} ({pkg.build_style}) ---")
            for phase in phases:
                if not phase.commands:
                    continue
                print(f"  [{phase.name}]")
                for cmd in phase.commands:
                    for line in cmd.split("\n"):
                        print(f"    $ {line}")
            print()

    # --- Execute build ---
    if do_build:
        print("\n" + "=" * 60)
        print("EXECUTING BUILD\n")

        executor = BuildExecutor(
            work_dir=WORK_DIR,
            log_dir=LOG_DIR,
            sources_dir=sources_dir,
            patches_dir=sources_dir,  # patches are co-located with sources in /sources/
            system_root=SYSTEM_ROOT,
            tracked=tracked,
            skip_built=skip_built,
        )

        success = executor.build_all(order, halt_on_failure=True)
        sys.exit(0 if success else 1)

    if not do_build:
        print(f"\nAll {len(order)} templates validated. Build order resolved. No cycles.")
        if not dry_run:
            print("\nRun with --build to execute, or --dry-run to preview commands.")


if __name__ == "__main__":
    main()
```

### parser.py
```python
"""Package template parser for igos-build.

Reads package.yml templates, validates required fields and types,
resolves variable substitutions, and returns validated Package objects.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Source:
    """A source tarball or file to download."""
    url: str
    sha256: str
    filename: str | None = None


@dataclass
class Dependencies:
    """Package dependency declarations."""
    build: list[str] = field(default_factory=list)
    host: list[str] = field(default_factory=list)
    runtime: list[str] = field(default_factory=list)


@dataclass
class ValidationCheck:
    """A post-build validation step."""
    type: str                        # sanity_check, footprint, checksum, test_suite
    description: str = ""
    script: str | None = None
    expect_contains: str | None = None
    fatal: bool = True


@dataclass
class Package:
    """A fully parsed and validated package template."""
    name: str
    version: str
    release: int
    description: str
    license: str

    source: list[Source]
    dependencies: Dependencies
    build_style: str                 # autotools, cmake, meson, make, custom

    # Classification
    tier: str = "core"               # toolchain, core, base, desktop

    # Optional metadata
    homepage: str | None = None

    # Build configuration
    configure_flags: list[str] = field(default_factory=list)
    patches: list[str] = field(default_factory=list)

    # Toolchain-specific
    target_triple: str | None = None
    pass_number: int | None = None
    bundled_deps: list[str] = field(default_factory=list)

    # Install function name for custom build style
    install_func: str = "do_install"  # "do_install" (default) or "install" (toolchain only)

    # Install directly to / instead of DESTDIR staging (for multi-pass builds)
    direct_install: bool = False

    # Validation steps
    validation: list[ValidationCheck] = field(default_factory=list)

    # Where this template was loaded from
    template_path: Path | None = None


# ---------------------------------------------------------------------------
# Allowed values
# ---------------------------------------------------------------------------

VALID_BUILD_STYLES = {"autotools", "cmake", "meson", "make", "custom"}
VALID_TIERS = {"toolchain", "core", "base", "desktop", "extra"}

REQUIRED_FIELDS = {"name", "version", "release", "description", "license",
                   "source", "build_style"}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class TemplateError(Exception):
    """Raised when a package template is invalid."""

    def __init__(self, path: Path, message: str):
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message}")


# ---------------------------------------------------------------------------
# Variable resolution
# ---------------------------------------------------------------------------

_VAR_RE = re.compile(r"\$\{(\w+)\}")


def _resolve_variables(text: str, variables: dict[str, str]) -> str:
    """Replace ${name} placeholders with values from the variables dict."""

    def _replace(match: re.Match) -> str:
        key = match.group(1)
        if key not in variables:
            raise KeyError(f"unknown variable '${{{key}}}'")
        return variables[key]

    return _VAR_RE.sub(_replace, text)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_sources(raw: list, variables: dict, path: Path) -> list[Source]:
    """Parse and validate the source list."""
    sources = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise TemplateError(path, f"source[{i}]: must be a mapping with 'url' and 'sha256'")
        url = entry.get("url")
        sha256 = entry.get("sha256")
        if not url:
            raise TemplateError(path, f"source[{i}]: missing 'url'")
        if not sha256:
            raise TemplateError(path, f"source[{i}]: missing 'sha256'")
        url = _resolve_variables(url, variables)
        filename = entry.get("filename")
        if filename:
            filename = _resolve_variables(filename, variables)
        sources.append(Source(url=url, sha256=sha256, filename=filename))
    return sources


def _parse_dependencies(raw: dict | None, path: Path) -> Dependencies:
    """Parse and validate the dependencies block."""
    if raw is None:
        return Dependencies()
    if not isinstance(raw, dict):
        raise TemplateError(path, "dependencies: must be a mapping")
    return Dependencies(
        build=raw.get("build", []) or [],
        host=raw.get("host", []) or [],
        runtime=raw.get("runtime", []) or [],
    )


def _parse_validation(raw: list | None, path: Path) -> list[ValidationCheck]:
    """Parse and validate the validation block."""
    if raw is None:
        return []
    checks = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise TemplateError(path, f"validation[{i}]: must be a mapping")
        vtype = entry.get("type")
        if not vtype:
            raise TemplateError(path, f"validation[{i}]: missing 'type'")
        checks.append(ValidationCheck(
            type=vtype,
            description=entry.get("description", ""),
            script=entry.get("script"),
            expect_contains=entry.get("expect_contains"),
            fatal=entry.get("fatal", True),
        ))
    return checks


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def parse_template(template_path: Path) -> Package:
    """Parse a package.yml file and return a validated Package.

    Args:
        template_path: Path to the package.yml file.

    Returns:
        A fully validated Package object.

    Raises:
        TemplateError: If the template is missing required fields,
                       has invalid values, or fails validation.
        FileNotFoundError: If the template file doesn't exist.
    """
    template_path = Path(template_path)
    if not template_path.exists():
        raise FileNotFoundError(f"template not found: {template_path}")

    with open(template_path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise TemplateError(template_path, "template must be a YAML mapping")

    # --- Check required fields ---
    missing = REQUIRED_FIELDS - set(raw.keys())
    if missing:
        raise TemplateError(template_path, f"missing required fields: {', '.join(sorted(missing))}")

    # --- Basic fields ---
    name = str(raw["name"])
    version = str(raw["version"])
    release = int(raw["release"])
    description = str(raw["description"])
    pkg_license = str(raw["license"])
    build_style = str(raw["build_style"])
    tier = str(raw.get("tier", "core"))

    # --- Validate enums ---
    if build_style not in VALID_BUILD_STYLES:
        raise TemplateError(
            template_path,
            f"invalid build_style '{build_style}' — must be one of: {', '.join(sorted(VALID_BUILD_STYLES))}"
        )
    if tier not in VALID_TIERS:
        raise TemplateError(
            template_path,
            f"invalid tier '{tier}' — must be one of: {', '.join(sorted(VALID_TIERS))}"
        )

    # --- Variable resolution context ---
    variables = {
        "name": name,
        "version": version,
    }

    # --- Parse complex fields ---
    source_raw = raw.get("source", [])
    if not isinstance(source_raw, list):
        raise TemplateError(template_path, "source: must be a list")
    sources = _parse_sources(source_raw, variables, template_path)

    dependencies = _parse_dependencies(raw.get("dependencies"), template_path)
    validation = _parse_validation(raw.get("validation"), template_path)

    # --- Simple optional fields ---
    configure_flags = raw.get("configure_flags", []) or []
    patches = raw.get("patches", []) or []
    bundled_deps = raw.get("bundled_deps", []) or []

    return Package(
        name=name,
        version=version,
        release=release,
        description=description,
        license=pkg_license,
        source=sources,
        dependencies=dependencies,
        build_style=build_style,
        tier=tier,
        homepage=raw.get("homepage"),
        configure_flags=configure_flags,
        patches=patches,
        target_triple=raw.get("target_triple"),
        pass_number=raw.get("pass_number"),
        bundled_deps=bundled_deps,
        install_func=raw.get("install_func", "do_install"),
        direct_install=bool(raw.get("direct_install", False)),
        validation=validation,
        template_path=template_path,
    )


def discover_templates(packages_dir: Path) -> list[Path]:
    """Find all package.yml files under the packages directory.

    Args:
        packages_dir: Root of the packages tree (e.g., /mnt/intergenos/packages)

    Returns:
        Sorted list of paths to package.yml files.
    """
    packages_dir = Path(packages_dir)
    return sorted(packages_dir.rglob("package.yml"))


def load_all_packages(packages_dir: Path) -> list[Package]:
    """Discover and parse all package templates.

    Args:
        packages_dir: Root of the packages tree.

    Returns:
        List of validated Package objects.

    Raises:
        TemplateError: If any template fails validation.
    """
    templates = discover_templates(packages_dir)
    packages = []
    for path in templates:
        packages.append(parse_template(path))
    return packages
```

### graph.py
```python
"""Dependency graph and topological sort for igos-build.

Builds a directed acyclic graph from package dependencies,
detects cycles, and produces a valid build order via topological sort.
"""

from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

from .parser import Package


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class CycleError(Exception):
    """Raised when a dependency cycle is detected."""

    def __init__(self, cycle: list[str]):
        self.cycle = cycle
        path = " -> ".join(cycle)
        super().__init__(f"dependency cycle detected: {path}")


class MissingDependencyError(Exception):
    """Raised when a package depends on something not in the graph."""

    def __init__(self, package: str, missing: str):
        self.package = package
        self.missing = missing
        super().__init__(f"'{package}' depends on '{missing}', which is not a known package")


# ---------------------------------------------------------------------------
# Dependency graph
# ---------------------------------------------------------------------------

@dataclass
class DependencyGraph:
    """A directed acyclic graph of package build dependencies.

    Nodes are package names. An edge from A -> B means A must be built
    before B (B depends on A).
    """
    # package name -> Package object
    packages: dict[str, Package] = field(default_factory=dict)

    # package name -> set of package names it depends on
    depends_on: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    # package name -> set of package names that depend on it
    required_by: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    def add_package(self, pkg: Package):
        """Add a package to the graph."""
        if pkg.name in self.packages:
            existing = self.packages[pkg.name].template_path
            raise ValueError(
                f"duplicate package '{pkg.name}': "
                f"already loaded from {existing}, "
                f"conflict with {pkg.template_path}"
            )
        self.packages[pkg.name] = pkg

    def resolve(self, strict: bool = True):
        """Resolve all dependency edges.

        Combines build, host, and runtime dependencies into a single
        dependency set per package. All dependency types must be built
        before the dependent package.

        Args:
            strict: If True, raise MissingDependencyError for unknown deps.
                    If False, silently skip unknown deps (useful during
                    incremental template development).
        """
        for name, pkg in self.packages.items():
            all_deps = set()
            all_deps.update(pkg.dependencies.build)
            all_deps.update(pkg.dependencies.host)
            all_deps.update(pkg.dependencies.runtime)

            for dep in all_deps:
                if dep not in self.packages:
                    if strict:
                        raise MissingDependencyError(name, dep)
                    continue
                self.depends_on[name].add(dep)
                self.required_by[dep].add(name)

    def build_order(self) -> list[Package]:
        """Compute a valid build order via Kahn's topological sort.

        Returns:
            List of Package objects in the order they should be built.

        Raises:
            CycleError: If the dependency graph contains a cycle.
        """
        # In-degree: how many unbuilt dependencies each package has
        in_degree = {}
        for name in self.packages:
            in_degree[name] = len(self.depends_on.get(name, set()))

        # Start with packages that have no dependencies
        queue = deque()
        for name, degree in in_degree.items():
            if degree == 0:
                queue.append(name)

        # Stable sort: within each "wave" of zero-degree packages,
        # sort by tier priority then alphabetically
        tier_priority = {"toolchain": 0, "core": 1, "base": 2, "desktop": 3}

        def sort_key(name: str) -> tuple:
            pkg = self.packages[name]
            tier_rank = tier_priority.get(pkg.tier, 99)
            pass_num = pkg.pass_number or 0
            return (tier_rank, pass_num, name)

        queue = deque(sorted(queue, key=sort_key))

        order = []
        while queue:
            # Process the current batch in priority order
            name = queue.popleft()
            order.append(name)

            # "Build" this package: decrement in-degree of dependents
            next_batch = []
            for dependent in sorted(self.required_by.get(name, set())):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    next_batch.append(dependent)

            # Insert newly-unblocked packages in sorted order
            for n in sorted(next_batch, key=sort_key):
                queue.append(n)

        # If we didn't process every package, there's a cycle
        if len(order) != len(self.packages):
            remaining = set(self.packages.keys()) - set(order)
            cycle = _find_cycle(remaining, self.depends_on)
            raise CycleError(cycle)

        return [self.packages[name] for name in order]

    def print_order(self, order: list[Package] | None = None):
        """Print the build order in a human-readable format."""
        if order is None:
            order = self.build_order()

        print(f"Build order ({len(order)} packages):\n")
        for i, pkg in enumerate(order, 1):
            deps = self.depends_on.get(pkg.name, set())
            dep_str = f"  (after: {', '.join(sorted(deps))})" if deps else ""
            pass_str = f" pass {pkg.pass_number}" if pkg.pass_number else ""
            print(f"  {i:3d}. [{pkg.tier}] {pkg.name} {pkg.version}{pass_str}{dep_str}")


# ---------------------------------------------------------------------------
# Cycle detection helper
# ---------------------------------------------------------------------------

def _find_cycle(nodes: set[str], depends_on: dict[str, set[str]]) -> list[str]:
    """Find and return one cycle from the remaining unprocessed nodes."""
    visited = set()

    for start in nodes:
        path = []
        path_set = set()

        def dfs(node: str) -> list[str] | None:
            if node in path_set:
                cycle_start = path.index(node)
                return path[cycle_start:] + [node]
            if node in visited:
                return None

            visited.add(node)
            path.append(node)
            path_set.add(node)

            for dep in depends_on.get(node, set()):
                if dep in nodes:
                    result = dfs(dep)
                    if result:
                        return result

            path.pop()
            path_set.discard(node)
            return None

        cycle = dfs(start)
        if cycle:
            return cycle

    return list(nodes)[:5] + ["..."]


# ---------------------------------------------------------------------------
# Convenience builder
# ---------------------------------------------------------------------------

def build_graph(packages: list[Package], strict: bool = True) -> DependencyGraph:
    """Build a dependency graph from a list of packages.

    Args:
        packages: List of parsed Package objects.
        strict: If True, fail on missing dependencies.

    Returns:
        A resolved DependencyGraph ready for build_order().
    """
    graph = DependencyGraph()
    for pkg in packages:
        graph.add_package(pkg)
    graph.resolve(strict=strict)
    return graph
```

### builder.py
```python
"""Build executor for igos-build.

Runs build phases for each package in dependency order. Handles:
  - Source extraction
  - Patch application
  - Build style phase execution
  - Post-build validation checks
  - Full logging of every command and its output
  - Fatal error handling (halt on failure)
"""

import os
import shutil
import subprocess
import time
from pathlib import Path

from .parser import Package
from .styles import get_style
from .log import BuildLogger, SummaryLogger


class BuildExecutor:
    """Executes package builds with full logging and validation.

    Directory layout during builds:
        {work_dir}/{pkg.name}/
            src/          — extracted source tree
            build/        — out-of-tree build directory (if needed)

    Environment variables available to build scripts:
        IGOS            — target system root (e.g., /mnt/intergenos/build/system)
        IGOS_TARGET     — target triple (x86_64-igos-linux-gnu)
        IGOS_JOBS       — parallel make jobs
        IGOS_SOURCES    — path to downloaded source tarballs
        IGOS_PATCHES    — path to patch files
        DESTDIR         — installation destination
    """

    def __init__(
        self,
        work_dir: Path,
        log_dir: Path,
        sources_dir: Path,
        patches_dir: Path,
        system_root: Path,
        target_triple: str = "x86_64-igos-linux-gnu",
        jobs: int | None = None,
        tracked: bool = False,
        skip_built: bool = False,
    ):
        self.work_dir = Path(work_dir)
        self.log_dir = Path(log_dir)
        self.sources_dir = Path(sources_dir)
        self.patches_dir = Path(patches_dir)
        self.system_root = Path(system_root)
        self.target_triple = target_triple
        self.jobs = jobs or os.cpu_count() or 4
        self.tracked = tracked
        self.skip_built = skip_built

        # Package tracking paths (Slackware-style manifests + archives)
        self.pkg_db = Path("/var/lib/igos/packages")
        self.pkg_archives = Path("/var/lib/igos/archives")
        self.pkg_staging = Path("/tmp/igos-staging")

        # Create directories
        dirs = [self.work_dir, self.log_dir, self.sources_dir, self.patches_dir, self.system_root]
        if self.tracked:
            dirs.extend([self.pkg_db, self.pkg_archives, self.pkg_staging])
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)

        self.logger = BuildLogger(self.log_dir)
        self.summary = SummaryLogger()

    def build_env(self, pkg: Package) -> dict[str, str]:
        """Build the environment variables dict for a package."""
        env = os.environ.copy()
        env["IGOS"] = str(self.system_root)
        env["IGOS_TARGET"] = pkg.target_triple or self.target_triple
        env["IGOS_JOBS"] = str(self.jobs)
        env["IGOS_SOURCES"] = str(self.sources_dir)
        env["IGOS_SOURCES_DIR"] = str(self.sources_dir)  # alias for build.sh compat
        env["IGOS_PATCHES"] = str(self.patches_dir)
        env["PKG_VERSION"] = str(pkg.version)
        env["version"] = str(pkg.version)  # convenience for build.sh scripts
        env["MAKEFLAGS"] = f"-j{self.jobs}"
        env["LC_ALL"] = "POSIX"
        env["XML_CATALOG_FILES"] = "/etc/xml/catalog"
        env["PKG_CONFIG_PATH"] = "/usr/lib/pkgconfig:/usr/share/pkgconfig"
        # Include /opt/rustc/bin for Rust toolchain (installed to /opt per BLFS)
        env["PATH"] = f"/opt/rustc/bin:{self.system_root}/tools/bin:" + env.get("PATH", "")

        # When tracked, each package stages into its own DESTDIR
        # and staged files are made visible as a sysroot for multi-pass builds
        if self.tracked:
            if pkg.direct_install:
                # Multi-pass packages install directly to /
                # Tracking uses filesystem diff instead of DESTDIR staging
                # DESTDIR must be unset (not empty string) — some build systems
                # treat "" differently from unset
                env.pop("DESTDIR", None)
            else:
                staging = self.pkg_staging / f"{pkg.name}-{pkg.version}"
                if staging.exists():
                    shutil.rmtree(staging)
                staging.mkdir(parents=True)

                # Prepare staging directory to match live filesystem layout.
                # This mirrors what pkg-functions.sh does for bash-built packages:
                #   1. Create usr/{bin,lib,sbin} so make install has targets
                #   2. Symlink /bin→usr/bin, /lib→usr/lib, /sbin→usr/sbin so
                #      installs through either path land in the same place
                #   3. Create lib64 on x86_64 (GCC multilib convention)
                for d in ("usr/bin", "usr/lib", "usr/sbin"):
                    (staging / d).mkdir(parents=True, exist_ok=True)
                import platform
                if platform.machine() == "x86_64":
                    (staging / "lib64").mkdir(exist_ok=True)
                for link in ("bin", "lib", "sbin"):
                    target = Path(f"/{link}")
                    if target.is_symlink():
                        os.symlink(f"usr/{link}", str(staging / link))
                env["DESTDIR"] = str(staging)
                env["PATH"] = f"{staging}/usr/bin:{staging}/usr/sbin:" + env["PATH"]
                env["PKG_CONFIG_PATH"] = f"{staging}/usr/lib/pkgconfig:{staging}/usr/lib64/pkgconfig:" + env.get("PKG_CONFIG_PATH", "")
                existing_ldpath = env.get("LD_LIBRARY_PATH", "")
                new_ldpath = f"{staging}/usr/lib:{staging}/usr/lib64"
                env["LD_LIBRARY_PATH"] = f"{new_ldpath}:{existing_ldpath}" if existing_ldpath else new_ldpath
        else:
            env["DESTDIR"] = str(self.system_root)

        return env

    def run_command(self, cmd: str, env: dict, cwd: Path) -> int:
        """Run a shell command with full output capture and logging.

        Output is streamed line-by-line to both console and log file.
        Nothing is buffered, nothing is truncated.

        Returns:
            The command's exit code.
        """
        self.logger.command(cmd)

        try:
            proc = subprocess.Popen(
                cmd,
                shell=True,
                executable="/bin/bash",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=str(cwd),
            )

            # Stream output line by line — never buffer, never truncate
            for line in iter(proc.stdout.readline, b""):
                decoded = line.decode("utf-8", errors="replace")
                self.logger.output(decoded)

            proc.wait()
            return proc.returncode

        except Exception as e:
            self.logger.error(f"command execution failed: {e}")
            return 1

    def extract_source(self, pkg: Package, pkg_work_dir: Path) -> Path | None:
        """Download (if needed) and extract the primary source tarball.

        Returns:
            Path to the extracted source directory, or None on failure.
        """
        if not pkg.source:
            self.logger.info("No source defined — skipping extraction")
            return pkg_work_dir

        primary = pkg.source[0]
        tarball_name = primary.filename or primary.url.split("/")[-1]
        tarball_path = self.sources_dir / tarball_name

        if not tarball_path.exists():
            self.logger.info(f"Source not found locally: {tarball_name}")
            self.logger.info(f"Downloading: {primary.url}")
            exit_code = self.run_command(
                f'wget -q --show-progress -O "{tarball_path}" "{primary.url}"',
                env=os.environ.copy(),
                cwd=self.sources_dir,
            )
            if exit_code != 0:
                self.logger.error(f"Failed to download {primary.url}")
                return None
        else:
            self.logger.info(f"Source cached: {tarball_name}")

        # Verify checksum
        if primary.sha256 and not primary.sha256.startswith("placeholder"):
            self.logger.info(f"Verifying SHA256: {primary.sha256[:16]}...")
            result = subprocess.run(
                ["sha256sum", str(tarball_path)],
                capture_output=True, text=True,
            )
            actual = result.stdout.split()[0] if result.stdout else ""
            if actual != primary.sha256:
                self.logger.error(
                    f"Checksum mismatch for {tarball_name}:\n"
                    f"  expected: {primary.sha256}\n"
                    f"  actual:   {actual}"
                )
                return None
            self.logger.info("Checksum verified.")
        else:
            self.logger.info("Checksum: placeholder — skipping verification")

        # Extract
        src_dir = pkg_work_dir / "src"
        src_dir.mkdir(parents=True, exist_ok=True)

        self.logger.info(f"Extracting to {src_dir}")
        # Use Python zipfile for .zip, lzip for .lz, tar for everything else
        if str(tarball_path).endswith('.zip'):
            import zipfile
            try:
                with zipfile.ZipFile(str(tarball_path)) as zf:
                    zf.extractall(str(src_dir))
                # Strip one component level if there's a single top-level dir
                entries = list(src_dir.iterdir())
                if len(entries) == 1 and entries[0].is_dir():
                    top = entries[0]
                    for item in top.iterdir():
                        item.rename(src_dir / item.name)
                    top.rmdir()
                exit_code = 0
            except Exception as e:
                self.logger.error(f"Failed to extract zip: {e}")
                exit_code = 1
        elif str(tarball_path).endswith('.lz'):
            extract_cmd = f'tar --lzip -xf "{tarball_path}" -C "{src_dir}" --strip-components=1'
            exit_code = self.run_command(extract_cmd, env=os.environ.copy(), cwd=pkg_work_dir)
        else:
            extract_cmd = f'tar -xf "{tarball_path}" -C "{src_dir}" --strip-components=1'
            exit_code = self.run_command(extract_cmd, env=os.environ.copy(), cwd=pkg_work_dir)
        if exit_code != 0:
            self.logger.error(f"Failed to extract {tarball_name}")
            return None

        # Extract bundled deps (e.g., GMP/MPFR/MPC into GCC source tree)
        for bundled in pkg.bundled_deps:
            if " -> " in bundled:
                dep_name, dest_rel = bundled.split(" -> ", 1)
                dep_tarball = None
                for s in pkg.source[1:]:
                    if dep_name in s.url:
                        dep_tarball = self.sources_dir / s.url.split("/")[-1]
                        break

                if dep_tarball and dep_tarball.exists():
                    dest = src_dir / dest_rel.replace("${version}", pkg.version)
                    dest.mkdir(parents=True, exist_ok=True)
                    self.logger.info(f"Extracting bundled dep: {dep_name} -> {dest}")
                    exit_code = self.run_command(
                        f'tar -xf "{dep_tarball}" -C "{dest}" --strip-components=1',
                        env=os.environ.copy(),
                        cwd=pkg_work_dir,
                    )
                    if exit_code != 0:
                        self.logger.error(f"Failed to extract bundled dep: {dep_name}")
                        return None
                else:
                    self.logger.error(f"Bundled dep tarball not found: {dep_name}")
                    return None

        return src_dir

    def run_validation(self, pkg: Package, env: dict, cwd: Path) -> bool:
        """Run post-build validation checks.

        Returns:
            True if all checks pass (or no checks defined).
            False if any fatal check fails.
        """
        if not pkg.validation:
            return True

        self.logger.info("Running validation checks...")

        for check in pkg.validation:
            self.logger.info(f"  Check: {check.description} [{check.type}]")

            if check.script:
                exit_code = self.run_command(check.script, env, cwd)

                if check.expect_contains:
                    # Re-run and capture output for content check
                    result = subprocess.run(
                        check.script,
                        shell=True, executable="/bin/bash",
                        capture_output=True, text=True,
                        env=env, cwd=str(cwd),
                    )
                    if check.expect_contains not in result.stdout:
                        self.logger.error(
                            f"Validation failed: expected output to contain '{check.expect_contains}'\n"
                            f"  Actual stdout: {result.stdout}\n"
                            f"  Actual stderr: {result.stderr}"
                        )
                        if check.fatal:
                            return False

                elif exit_code != 0:
                    self.logger.error(f"Validation script exited with code {exit_code}")
                    if check.fatal:
                        return False

            self.logger.info(f"  Check passed: {check.description}")

        return True

    # ------------------------------------------------------------------
    # Package tracking (--tracked mode)
    # ------------------------------------------------------------------

    def pkg_manifest(self, pkg: Package, staging_dir: Path) -> bool:
        """Generate a Slackware-style manifest from staged files.

        Writes: /var/lib/igos/packages/<name>-<version>
        """
        manifest_path = self.pkg_db / f"{pkg.name}-{pkg.version}"

        file_list = []
        for root, dirs, files in os.walk(staging_dir):
            for d in sorted(dirs):
                rel = os.path.relpath(os.path.join(root, d), staging_dir)
                file_list.append(rel + "/")
            for f in sorted(files):
                rel = os.path.relpath(os.path.join(root, f), staging_dir)
                file_list.append(rel)

        if not file_list:
            self.logger.error(f"Staging produced no files for {pkg.name}-{pkg.version}")
            return False

        # Calculate size
        total_size = sum(
            os.path.getsize(os.path.join(root, f))
            for root, _, files in os.walk(staging_dir)
            for f in files
            if os.path.isfile(os.path.join(root, f))
        )
        human_size = f"{total_size / 1024 / 1024:.1f}M" if total_size > 1024*1024 else f"{total_size / 1024:.0f}K"

        from datetime import datetime, timezone
        manifest_content = (
            f"PACKAGE NAME: {pkg.name}-{pkg.version}\n"
            f"PACKAGE VERSION: {pkg.version}\n"
            f"UNCOMPRESSED SIZE: {human_size} ({total_size} bytes)\n"
            f"BUILD DATE: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            f"BUILD SYSTEM: InterGenOS igos-build\n"
            f"DESCRIPTION:\n"
            f"{pkg.name}: {pkg.description}\n"
            f"\n"
            f"FILE LIST:\n"
        )
        manifest_content += "\n".join(file_list) + "\n"

        manifest_path.write_text(manifest_content)
        self.logger.info(f"Manifest: {manifest_path} ({len(file_list)} entries)")
        return True

    def pkg_archive(self, pkg: Package, staging_dir: Path) -> bool:
        """Create a .igos.tar.gz archive from staged files.

        Creates: /var/lib/igos/archives/<name>-<version>.igos.tar.gz
        """
        archive_path = self.pkg_archives / f"{pkg.name}-{pkg.version}.igos.tar.gz"

        result = subprocess.run(
            ["tar", "-C", str(staging_dir), "-czf", str(archive_path), "."],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            self.logger.error(f"Archive creation failed: {result.stderr}")
            return False

        archive_size = archive_path.stat().st_size
        human = f"{archive_size / 1024 / 1024:.1f}M" if archive_size > 1024*1024 else f"{archive_size / 1024:.0f}K"
        self.logger.info(f"Archive: {archive_path} ({human})")
        return True

    def pkg_deploy(self, pkg: Package, staging_dir: Path) -> bool:
        """Deploy staged files to the live filesystem using tar.

        Safety: pre-checks for top-level entries that would collide with
        root-level symlinks (lib -> usr/lib, bin -> usr/bin, etc.).
        A package staging a real directory over one of these symlinks
        would kill the dynamic linker and break every binary on the system.
        """
        # Pre-deploy safety check: detect staging entries that would clobber
        # load-bearing root symlinks (lib -> usr/lib, bin -> usr/bin, etc.)
        dangerous = []
        for entry in ("lib", "lib64", "bin", "sbin"):
            staged = staging_dir / entry
            root_path = Path("/") / entry
            # Only flag real directories — symlinks in staging are intentional
            # (we create them in build_env to mirror the live filesystem layout)
            if staged.is_dir() and not staged.is_symlink() and root_path.is_symlink():
                dangerous.append(entry)

        if dangerous:
            self.logger.error(
                f"DANGEROUS: {pkg.name}-{pkg.version} staging contains top-level "
                f"dirs that would collide with root symlinks: {' '.join(dangerous)}\n"
                f"  Fix the package build.sh to install to usr/ paths instead"
            )
            return False

        result = subprocess.run(
            ["tar", "-C", str(staging_dir), "-cf", "-", "."],
            capture_output=True,
        )
        if result.returncode != 0:
            self.logger.error(f"Deploy tar-create failed: {result.stderr.decode()}")
            return False

        result2 = subprocess.run(
            ["tar", "-C", "/", "-xf", "-",
             "--no-overwrite-dir", "--keep-directory-symlink"],
            input=result.stdout,
            capture_output=True,
        )
        if result2.returncode != 0:
            self.logger.error(f"Deploy tar-extract failed: {result2.stderr.decode()}")
            return False

        self.logger.info(f"Deployed {pkg.name}-{pkg.version} to live filesystem")

        # Clean up staging directory
        shutil.rmtree(staging_dir, ignore_errors=True)
        return True

    def pkg_verify(self, pkg: Package) -> bool:
        """Verify every file in the manifest exists on the live filesystem.

        Reads: /var/lib/igos/packages/<name>-<version>
        Returns True if all files are present, False if any are missing.
        """
        manifest_path = self.pkg_db / f"{pkg.name}-{pkg.version}"
        if not manifest_path.exists():
            self.logger.error(f"Manifest not found: {manifest_path}")
            return False

        content = manifest_path.read_text()
        in_file_list = False
        missing = []

        for line in content.splitlines():
            if line == "FILE LIST:":
                in_file_list = True
                continue
            if in_file_list and line.strip():
                # Skip directories (trailing /), only verify files
                if line.endswith("/"):
                    continue
                filepath = "/" + line
                if not os.path.lexists(filepath):
                    missing.append(filepath)

        if missing:
            self.logger.error(
                f"Manifest verification FAILED for {pkg.name}-{pkg.version}:\n"
                + "\n".join(f"  MISSING: {f}" for f in missing[:20])
            )
            if len(missing) > 20:
                self.logger.error(f"  ... and {len(missing) - 20} more")
            return False

        self.logger.info("Manifest verified: all files present on live filesystem")
        return True

    # ------------------------------------------------------------------
    # Direct install tracking (filesystem diff)
    # ------------------------------------------------------------------

    def fs_snapshot(self, dirs: list[str] | None = None) -> set[str]:
        """Snapshot all files and symlinks under key system directories.

        Captures regular files, symlinks (both file and directory symlinks),
        and any other non-directory entries. Returns a set of paths for diffing.
        """
        if dirs is None:
            dirs = ["/usr", "/etc", "/opt", "/var/lib", "/lib"]
        snapshot = set()
        for d in dirs:
            if not os.path.isdir(d):
                continue
            for root, dirnames, files in os.walk(d, followlinks=False):
                # Capture regular files and file symlinks
                for f in files:
                    snapshot.add(os.path.join(root, f))
                # Capture directory symlinks (os.walk skips them by default)
                for dn in dirnames:
                    path = os.path.join(root, dn)
                    if os.path.islink(path):
                        snapshot.add(path)
        return snapshot

    def pkg_manifest_from_diff(self, pkg: Package, before: set[str], after: set[str]) -> bool:
        """Generate manifest from filesystem diff (for direct_install packages).

        Writes: /var/lib/igos/packages/<name>-<version>
        """
        new_files = sorted(after - before)

        if not new_files:
            self.logger.error(f"No new files detected for {pkg.name}-{pkg.version}")
            return False

        manifest_path = self.pkg_db / f"{pkg.name}-{pkg.version}"

        # Build directory + file list
        file_list = []
        dirs_seen = set()
        for filepath in new_files:
            parts = Path(filepath).relative_to("/")
            for i in range(1, len(parts.parts)):
                parent = str(Path(*parts.parts[:i]))
                if parent not in dirs_seen:
                    dirs_seen.add(parent)
                    file_list.append(parent + "/")
            file_list.append(str(parts))

        # Deduplicate and sort
        file_list = sorted(set(file_list))

        total_size = sum(
            os.path.getsize(f) for f in new_files if os.path.isfile(f)
        )
        human_size = f"{total_size / 1024 / 1024:.1f}M" if total_size > 1024*1024 else f"{total_size / 1024:.0f}K"

        from datetime import datetime, timezone
        manifest_content = (
            f"PACKAGE NAME: {pkg.name}-{pkg.version}\n"
            f"PACKAGE VERSION: {pkg.version}\n"
            f"UNCOMPRESSED SIZE: {human_size} ({total_size} bytes)\n"
            f"BUILD DATE: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            f"BUILD SYSTEM: InterGenOS igos-build\n"
            f"INSTALL MODE: direct (filesystem diff)\n"
            f"DESCRIPTION:\n"
            f"{pkg.name}: {pkg.description}\n"
            f"\n"
            f"FILE LIST:\n"
        )
        manifest_content += "\n".join(file_list) + "\n"

        manifest_path.write_text(manifest_content)
        self.logger.info(f"Manifest (diff): {manifest_path} ({len(new_files)} files, {len(dirs_seen)} dirs)")
        return True

    def pkg_archive_from_files(self, pkg: Package, new_files: list[str]) -> bool:
        """Create .igos.tar.gz archive from a list of files on the live filesystem."""
        archive_path = self.pkg_archives / f"{pkg.name}-{pkg.version}.igos.tar.gz"

        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            for filepath in new_files:
                f.write(filepath.lstrip("/") + "\n")
            filelist_path = f.name

        result = subprocess.run(
            ["tar", "-C", "/", "-czf", str(archive_path), "-T", filelist_path],
            capture_output=True, text=True,
        )
        os.unlink(filelist_path)

        if result.returncode != 0:
            self.logger.error(f"Archive creation failed: {result.stderr}")
            return False

        archive_size = archive_path.stat().st_size
        human = f"{archive_size / 1024 / 1024:.1f}M" if archive_size > 1024*1024 else f"{archive_size / 1024:.0f}K"
        self.logger.info(f"Archive: {archive_path} ({human})")
        return True

    def build_package(self, pkg: Package) -> bool:
        """Build a single package through all phases.

        Returns:
            True if the build succeeded, False otherwise.
        """
        build_start = time.monotonic()
        self.logger.start_package(pkg.name, pkg.version, pkg.build_style)

        # Set up working directory
        pkg_work_dir = self.work_dir / pkg.name
        if pkg_work_dir.exists():
            shutil.rmtree(pkg_work_dir)
        pkg_work_dir.mkdir(parents=True)

        env = self.build_env(pkg)
        success = True

        # Snapshot filesystem before build (for direct_install diff tracking)
        fs_before = None
        if self.tracked and pkg.direct_install:
            self.logger.info("Taking pre-build filesystem snapshot...")
            fs_before = self.fs_snapshot()

        # --- Extract source ---
        self.logger.start_phase("extract")
        src_dir = self.extract_source(pkg, pkg_work_dir)
        if src_dir is None:
            self.logger.end_phase("extract", 1)
            self.logger.end_package(False)
            elapsed = time.monotonic() - build_start
            self.summary.record(pkg.name, pkg.version, False, elapsed)
            return False
        self.logger.end_phase("extract", 0)

        # --- Run build style phases ---
        # build.sh is always authoritative: if it exists, use CustomStyle
        # regardless of declared build_style. build_style remains as a label
        # for humans and generate-templates.py, not a builder instruction.
        build_sh = pkg.template_path.parent / "build.sh" if pkg.template_path else None
        if build_sh and build_sh.exists():
            style = get_style("custom")
        else:
            style = get_style(pkg.build_style)
        phases = style.all_phases(pkg)

        for phase in phases:
            if not phase.commands:
                continue

            self.logger.start_phase(phase.name)

            for cmd in phase.commands:
                exit_code = self.run_command(cmd, env, src_dir)
                if exit_code != 0:
                    self.logger.end_phase(phase.name, exit_code)
                    self.logger.error(
                        f"Build failed in [{phase.name}] phase.\n"
                        f"  Package: {pkg.name} {pkg.version}\n"
                        f"  Command: {cmd}\n"
                        f"  Exit code: {exit_code}\n"
                        f"  Log: {self.log_dir}/{pkg.name}-*.log\n"
                        f"\n  Check the log file for full output above this error."
                    )
                    success = False
                    break

            if not success:
                break

            self.logger.end_phase(phase.name, 0)

        # --- Run validation ---
        if success:
            self.logger.start_phase("validate")
            if not self.run_validation(pkg, env, src_dir):
                success = False
                self.logger.end_phase("validate", 1)
            else:
                self.logger.end_phase("validate", 0)

        # --- Package tracking (manifest, archive, deploy, verify) ---
        if success and self.tracked:
            self.logger.start_phase("track")

            if pkg.direct_install:
                # Diff-based tracking: compare before/after filesystem snapshots
                self.logger.info("Taking post-build filesystem snapshot...")
                fs_after = self.fs_snapshot()
                new_files = sorted(fs_after - fs_before)

                if not self.pkg_manifest_from_diff(pkg, fs_before, fs_after):
                    success = False
                    self.logger.end_phase("track", 1)
                elif not self.pkg_archive_from_files(pkg, new_files):
                    success = False
                    self.logger.end_phase("track", 1)
                elif not self.pkg_verify(pkg):
                    success = False
                    self.logger.end_phase("track", 1)
                else:
                    self.logger.end_phase("track", 0)
            else:
                # DESTDIR staging: manifest, archive, deploy, verify
                staging_dir = self.pkg_staging / f"{pkg.name}-{pkg.version}"

                if not self.pkg_manifest(pkg, staging_dir):
                    success = False
                    self.logger.end_phase("track", 1)
                elif not self.pkg_archive(pkg, staging_dir):
                    success = False
                    self.logger.end_phase("track", 1)
                elif not self.pkg_deploy(pkg, staging_dir):
                    success = False
                    self.logger.end_phase("track", 1)
                elif not self.pkg_verify(pkg):
                    success = False
                    self.logger.end_phase("track", 1)
                else:
                    self.logger.end_phase("track", 0)

        # --- Post-install (runs on live filesystem, after deploy) ---
        # post_install hooks handle things like catalog registration, systemd
        # enable, config file generation — anything that must run on the live
        # system rather than in DESTDIR.
        if success:
            post_phase = style.post_install(pkg)
            if post_phase.commands:
                self.logger.start_phase("post_install")
                # Run with DESTDIR unset so commands target the live filesystem
                post_env = env.copy()
                post_env.pop("DESTDIR", None)
                for cmd in post_phase.commands:
                    exit_code = self.run_command(cmd, post_env, src_dir)
                    if exit_code != 0:
                        self.logger.error(
                            f"post_install failed for {pkg.name} {pkg.version}\n"
                            f"  Command: {cmd}\n"
                            f"  Exit code: {exit_code}"
                        )
                        success = False
                        break
                self.logger.end_phase("post_install", 0 if success else 1)

        elapsed = time.monotonic() - build_start
        self.logger.end_package(success)
        self.summary.record(pkg.name, pkg.version, success, elapsed)
        return success

    def build_all(self, packages: list[Package], halt_on_failure: bool = True) -> bool:
        """Build all packages in the given order.

        Args:
            packages: List of Package objects in build order.
            halt_on_failure: If True, stop at the first failure.

        Returns:
            True if all builds succeeded, False otherwise.
        """
        total = len(packages)
        all_success = True

        self.logger.info(f"\nStarting build of {total} package(s)...\n")

        for i, pkg in enumerate(packages, 1):
            # Skip packages that have a tracked manifest.
            # Manifest existence is sufficient — full file verification already
            # ran at install time. Re-verifying here causes false rebuilds when
            # post_install moves/deletes files after the manifest was written
            # (e.g., Rust removes .old docs and moves bash completions).
            if self.skip_built:
                manifest = self.pkg_db / f"{pkg.name}-{pkg.version}"
                if manifest.exists():
                    self.logger.info(f"[{i}/{total}] Skipping {pkg.name} {pkg.version} (already tracked)")
                    self.summary.record(pkg.name, pkg.version, True, 0, skipped=True)
                    continue

            self.logger.info(f"[{i}/{total}] Building {pkg.name} {pkg.version}...")
            success = self.build_package(pkg)

            if not success:
                all_success = False
                if halt_on_failure:
                    self.logger.error(
                        f"Build halted at {pkg.name} {pkg.version} "
                        f"({i}/{total}). Fix the error and retry."
                    )
                    break

        self.summary.print_summary()
        return all_success
```

### log.py
```python
"""Logging infrastructure for igos-build.

Every build action is logged with timestamps, phase markers, and full
untruncated output. Logs are written to both console and per-package
log files. Nothing is hidden, nothing is summarized.
"""

import sys
import time
from datetime import datetime, timezone
from pathlib import Path


class BuildLogger:
    """Logs build output to console and per-package log files.

    Each package gets its own log file at:
        {log_dir}/{package_name}-{timestamp}.log

    The log captures everything: commands run, stdout, stderr, exit codes,
    timing, and phase boundaries. Full output, never truncated.
    """

    def __init__(self, log_dir: Path):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._file = None
        self._pkg_name = None
        self._phase_start = None
        self._build_start = None

    def start_package(self, name: str, version: str, style: str):
        """Open a log file for a new package build."""
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        log_path = self.log_dir / f"{name}-{timestamp}.log"
        self._file = open(log_path, "w")
        self._pkg_name = name
        self._build_start = time.monotonic()

        header = (
            f"{'=' * 72}\n"
            f"  PACKAGE: {name} {version}\n"
            f"  STYLE:   {style}\n"
            f"  STARTED: {datetime.now(timezone.utc).isoformat()}\n"
            f"  LOG:     {log_path}\n"
            f"{'=' * 72}\n"
        )
        self._write(header)
        self._console(header)

    def end_package(self, success: bool):
        """Close the log file for the current package."""
        elapsed = time.monotonic() - self._build_start
        status = "SUCCESS" if success else "FAILED"

        footer = (
            f"\n{'=' * 72}\n"
            f"  {self._pkg_name}: {status} in {elapsed:.1f}s\n"
            f"{'=' * 72}\n\n"
        )
        self._write(footer)
        self._console(footer)

        if self._file:
            self._file.close()
            self._file = None

    def start_phase(self, phase_name: str):
        """Log the start of a build phase."""
        self._phase_start = time.monotonic()
        marker = f"\n--- [{phase_name.upper()}] {self._pkg_name} ---\n"
        self._write(marker)
        self._console(marker)

    def end_phase(self, phase_name: str, exit_code: int):
        """Log the end of a build phase with its exit code."""
        elapsed = time.monotonic() - self._phase_start
        status = "OK" if exit_code == 0 else f"FAILED (exit {exit_code})"
        marker = f"--- [{phase_name.upper()}] {status} ({elapsed:.1f}s) ---\n"
        self._write(marker)
        self._console(marker)

    def command(self, cmd: str):
        """Log a command about to be executed."""
        line = f"\n  $ {cmd}\n"
        self._write(line)
        self._console(line)

    def output(self, text: str):
        """Log command output (stdout or stderr). Never truncated."""
        if text:
            self._write(text)
            self._console_output(text)

    def error(self, message: str):
        """Log an error message."""
        line = f"\n  ERROR: {message}\n"
        self._write(line)
        self._console_error(line)

    def info(self, message: str):
        """Log an informational message."""
        line = f"  {message}\n"
        self._write(line)
        self._console(line)

    def _write(self, text: str):
        """Write to the log file."""
        if self._file:
            self._file.write(text)
            self._file.flush()

    def _console(self, text: str):
        """Write to stdout."""
        sys.stdout.write(text)
        sys.stdout.flush()

    def _console_output(self, text: str):
        """Write command output to stdout."""
        sys.stdout.write(text)
        sys.stdout.flush()

    def _console_error(self, text: str):
        """Write error to stderr."""
        sys.stderr.write(text)
        sys.stderr.flush()


class SummaryLogger:
    """Tracks and reports the overall build summary."""

    def __init__(self):
        self._results: list[tuple[str, str, bool, float]] = []
        self._start = time.monotonic()

    def record(self, name: str, version: str, success: bool, elapsed: float, skipped: bool = False):
        """Record the result of one package build."""
        self._results.append((name, version, success, elapsed, skipped))

    def print_summary(self):
        """Print the final build summary."""
        total_time = time.monotonic() - self._start
        built = [r for r in self._results if not r[4]]  # not skipped
        skipped = [r for r in self._results if r[4]]
        succeeded = [r for r in built if r[2]]
        failed = [r for r in built if not r[2]]

        print(f"\n{'=' * 72}")
        print(f"  BUILD SUMMARY")
        print(f"{'=' * 72}\n")
        print(f"  Total packages: {len(self._results)}")
        print(f"  Built:          {len(built)}")
        print(f"  Succeeded:      {len(succeeded)}")
        print(f"  Failed:         {len(failed)}")
        if skipped:
            print(f"  Skipped:        {len(skipped)}")
        print(f"  Total time:     {total_time:.1f}s\n")

        if failed:
            print("  FAILURES:")
            for name, version, _, elapsed, _ in failed:
                print(f"    - {name} {version} ({elapsed:.1f}s)")
            print()

        print("  COMPLETED:")
        for name, version, success, elapsed, was_skipped in self._results:
            if was_skipped:
                print(f"    [SKIP] {name} {version}")
            else:
                status = "OK" if success else "FAIL"
                print(f"    [{status:4s}] {name} {version} ({elapsed:.1f}s)")

        print(f"\n{'=' * 72}\n")
```

### __init__.py
```python
"""Build styles for igos-build.

Each style encapsulates a common build pattern (autotools, cmake, etc.)
and generates the shell commands for each build phase.
"""

from .base import BuildStyle, BuildPhase
from .autotools import AutotoolsStyle
from .cmake import CMakeStyle
from .meson import MesonStyle
from .make import MakeStyle
from .custom import CustomStyle


# Registry of available build styles
STYLES: dict[str, type[BuildStyle]] = {
    "autotools": AutotoolsStyle,
    "cmake": CMakeStyle,
    "meson": MesonStyle,
    "make": MakeStyle,
    "custom": CustomStyle,
}


def get_style(name: str) -> BuildStyle:
    """Get a build style instance by name.

    Args:
        name: One of: autotools, cmake, meson, make, custom

    Returns:
        A BuildStyle instance.

    Raises:
        ValueError: If the style name is not recognized.
    """
    cls = STYLES.get(name)
    if cls is None:
        raise ValueError(f"unknown build style '{name}' — available: {', '.join(sorted(STYLES))}")
    return cls()
```

### base.py
```python
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
        """Standard patch application — shared across styles."""
        commands = []
        for patch_file in pkg.patches:
            commands.append(f'echo "Applying patch: {patch_file}"')
            commands.append(f"patch -Np1 -i $IGOS_PATCHES/{patch_file}")
        return commands
```

### custom.py
```python
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

    def configure(self, pkg: Package) -> BuildPhase:
        script = self._build_sh_path(pkg)
        return BuildPhase(
            name="configure",
            commands=[
                f"source {script} && if declare -f configure >/dev/null 2>&1; then configure; fi",
            ],
        )

    def build(self, pkg: Package) -> BuildPhase:
        script = self._build_sh_path(pkg)
        return BuildPhase(
            name="build",
            commands=[
                f"source {script} && if declare -f build >/dev/null 2>&1; then build; fi",
            ],
        )

    def check(self, pkg: Package) -> BuildPhase:
        script = self._build_sh_path(pkg)
        return BuildPhase(
            name="check",
            commands=[
                f"source {script} && if declare -f check >/dev/null 2>&1; then check; fi",
            ],
        )

    def install(self, pkg: Package) -> BuildPhase:
        script = self._build_sh_path(pkg)
        func = pkg.install_func  # "install" (toolchain) or "do_install" (core/base)
        return BuildPhase(
            name="install",
            commands=[
                f"source {script} && if declare -f {func} >/dev/null 2>&1; then {func}; fi",
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
                f"source {script} && if declare -f post_install >/dev/null 2>&1; then post_install; fi",
            ],
        )
```

### autotools.py
```python
"""Autotools build style — ./configure && make && make install.

Handles the vast majority of LFS packages: anything using the standard
GNU autoconf/automake/libtool build pattern.
"""

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
        flags = " \\\n    ".join(pkg.configure_flags) if pkg.configure_flags else ""

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
            commands=["make check || true"],
        )

    def install(self, pkg: Package) -> BuildPhase:
        return BuildPhase(
            name="install",
            commands=["make DESTDIR=${DESTDIR} install"],
        )
```

### cmake.py
```python
"""CMake build style — cmake, make/ninja, make install."""

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
        flags = " \\\n    ".join(pkg.configure_flags) if pkg.configure_flags else ""
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
```

### meson.py
```python
"""Meson build style — meson setup, ninja, ninja install."""

from ..parser import Package
from .base import BuildStyle, BuildPhase


class MesonStyle(BuildStyle):
    """Meson + Ninja build system."""

    def patch(self, pkg: Package) -> BuildPhase:
        return BuildPhase(
            name="patch",
            commands=self._patch_commands(pkg),
        )

    def configure(self, pkg: Package) -> BuildPhase:
        flags = " \\\n    ".join(pkg.configure_flags) if pkg.configure_flags else ""
        base = "meson setup build --prefix=/usr --libdir=/usr/lib --buildtype=release"

        if flags:
            cmd = f"{base} \\\n    {flags}"
        else:
            cmd = base

        return BuildPhase(
            name="configure",
            commands=[cmd],
        )

    def build(self, pkg: Package) -> BuildPhase:
        return BuildPhase(
            name="build",
            commands=["ninja -C build -j${IGOS_JOBS}"],
        )

    def check(self, pkg: Package) -> BuildPhase:
        return BuildPhase(
            name="check",
            commands=["ninja -C build test || true"],
        )

    def install(self, pkg: Package) -> BuildPhase:
        return BuildPhase(
            name="install",
            commands=["DESTDIR=${DESTDIR} ninja -C build install"],
        )
```

### make.py
```python
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
```
