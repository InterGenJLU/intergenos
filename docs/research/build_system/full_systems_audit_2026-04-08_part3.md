# InterGenOS Full Systems Audit — Part 3 of 5
# Python Build System, Package Functions, Image Creator, Host Checker

**Date:** 2026-04-08
**Prepared for:** External Security Auditors

---

## 1. Entry Point: igos-build.py

**Path:** `/mnt/intergenos/igos-build.py`

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

---

## 2. Package Init: igos-build/__init__.py

**Path:** `/mnt/intergenos/igos-build/__init__.py`

```python
"""igos-build — InterGenOS build system."""

__version__ = "0.1.0"
```

---

## 3. Main Entry: igos-build/__main__.py

**Path:** `/mnt/intergenos/igos-build/__main__.py`

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
        graph = build_graph(all_packages, strict=True)
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

---

## 4. Template Parser: igos-build/parser.py

**Path:** `/mnt/intergenos/igos-build/parser.py`

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

    # Skip package tracking (for pass packages that overwrite existing files)
    skip_tracking: bool = False

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
        skip_tracking=bool(raw.get("skip_tracking", False)),
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

---

## 5. Dependency Graph: igos-build/graph.py

**Path:** `/mnt/intergenos/igos-build/graph.py`

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

---

## 6. Build Executor: igos-build/builder.py

**Path:** `/mnt/intergenos/igos-build/builder.py`

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
from .tracker import PackageTracker


class BuildExecutor(PackageTracker):
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
        # PKG_CONFIG_LIBDIR replaces the default search path (unlike
        # PKG_CONFIG_PATH which augments it). This prevents host .pc files
        # from leaking into the build and causing non-deterministic results.
        env["PKG_CONFIG_LIBDIR"] = "/usr/lib/pkgconfig:/usr/lib64/pkgconfig:/usr/share/pkgconfig"
        env.pop("PKG_CONFIG_PATH", None)  # ensure only LIBDIR is used
        # GObject Introspection typelib path — needed by g-ir-scanner when
        # building GTK, GStreamer, and other GI-consuming packages
        env["GI_TYPELIB_PATH"] = "/usr/lib/girepository-1.0"
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
                # PKG_CONFIG_LIBDIR: staging first, then system — replaces
                # default search entirely so host .pc files cannot leak in
                env["PKG_CONFIG_LIBDIR"] = (
                    f"{staging}/usr/lib/pkgconfig:{staging}/usr/lib64/pkgconfig:"
                    + env["PKG_CONFIG_LIBDIR"]
                )
                # GI typelib resolution for staged packages
                env["GI_TYPELIB_PATH"] = (
                    f"{staging}/usr/lib/girepository-1.0:"
                    + env["GI_TYPELIB_PATH"]
                )
                # LD_LIBRARY_PATH for runtime lib resolution during build
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
            import traceback
            self.logger.error(f"command execution failed: {e}\n{traceback.format_exc()}")
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
            # Hard-fail if source is missing. The build runs in an offline
            # chroot — network downloads are not available. Run
            # download-sources.py on the host first.
            self.logger.error(
                f"Source not found: {tarball_name}\n"
                f"  Expected at: {tarball_path}\n"
                f"  URL: {primary.url}\n"
                f"  Run 'python3 scripts/download-sources.py' on the host to fetch missing sources."
            )
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
        # All extraction uses hardened flags to prevent path traversal,
        # symlink attacks, and UID/GID injection.
        TAR_SAFETY = "--no-same-owner --no-same-permissions"
        if str(tarball_path).endswith('.zip'):
            import zipfile
            try:
                with zipfile.ZipFile(str(tarball_path)) as zf:
                    # Validate members before extraction — reject path traversal
                    for member in zf.namelist():
                        resolved = (src_dir / member).resolve()
                        if not str(resolved).startswith(str(src_dir.resolve())):
                            self.logger.error(
                                f"SECURITY: zip member '{member}' escapes extraction root — rejecting archive"
                            )
                            return None
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
                import traceback
                self.logger.error(traceback.format_exc())
                exit_code = 1
        elif str(tarball_path).endswith('.lz'):
            extract_cmd = f'tar --lzip -xf "{tarball_path}" -C "{src_dir}" --strip-components=1 {TAR_SAFETY}'
            exit_code = self.run_command(extract_cmd, env=os.environ.copy(), cwd=pkg_work_dir)
        else:
            extract_cmd = f'tar -xf "{tarball_path}" -C "{src_dir}" --strip-components=1 {TAR_SAFETY}'
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
                        f'tar -xf "{dep_tarball}" -C "{dest}" --strip-components=1 {TAR_SAFETY}',
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
                if check.expect_contains:
                    # Run once with output capture for content check
                    result = subprocess.run(
                        check.script,
                        shell=True, executable="/bin/bash",
                        capture_output=True, text=True,
                        env=env, cwd=str(cwd),
                    )
                    # Log the output (mirrors what run_command does)
                    self.logger.command(check.script)
                    if result.stdout:
                        self.logger.output(result.stdout)
                    if result.stderr:
                        self.logger.output(result.stderr)

                    if check.expect_contains not in result.stdout:
                        self.logger.error(
                            f"Validation failed: expected output to contain '{check.expect_contains}'\n"
                            f"  Actual stdout: {result.stdout}\n"
                            f"  Actual stderr: {result.stderr}"
                        )
                        if check.fatal:
                            return False
                else:
                    # No content check needed — just run and check exit code
                    exit_code = self.run_command(check.script, env, cwd)

                    if exit_code != 0:
                        self.logger.error(f"Validation script exited with code {exit_code}")
                        if check.fatal:
                            return False

            self.logger.info(f"  Check passed: {check.description}")

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
        if success and self.tracked and pkg.skip_tracking:
            self.logger.info(f"Skipping tracking for {pkg.name} (skip_tracking=true)")
        elif success and self.tracked:
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
                    # Check if template has changed since last build
                    # by comparing a hash of package.yml + build.sh
                    rebuild_needed = False
                    if pkg.template_path:
                        import hashlib
                        hasher = hashlib.sha256()
                        for tpl_file in [pkg.template_path, pkg.template_path.parent / "build.sh"]:
                            if tpl_file.exists():
                                hasher.update(tpl_file.read_bytes())
                        current_hash = hasher.hexdigest()[:16]
                        # Check if manifest contains our hash marker
                        manifest_text = manifest.read_text()
                        if f"TEMPLATE_HASH: {current_hash}" not in manifest_text:
                            self.logger.info(f"[{i}/{total}] Rebuilding {pkg.name} {pkg.version} (template changed)")
                            rebuild_needed = True
                    if not rebuild_needed:
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

---

## 7. Package Tracker: igos-build/tracker.py

**Path:** `/mnt/intergenos/igos-build/tracker.py`

```python
"""Package tracking — manifest generation, archive creation, deployment, verification.

Extracted from builder.py to reduce the BuildExecutor class size.
These methods handle everything after a successful build:
  1. Generate Slackware-style text manifest
  2. Create .igos.tar.gz archive
  3. Deploy staged files to the live filesystem
  4. Verify deployment against manifest
"""

import os
import shutil
import subprocess
from pathlib import Path

from .parser import Package


class PackageTracker:
    """Mixin class providing package tracking methods.

    Requires self.logger, self.pkg_db, self.pkg_archives, self.pkg_staging
    to be set by the host class (BuildExecutor).
    """

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
        # Compute template hash for skip-built change detection
        template_hash = ""
        if pkg.template_path:
            import hashlib
            hasher = hashlib.sha256()
            for tpl_file in [pkg.template_path, pkg.template_path.parent / "build.sh"]:
                if tpl_file.exists():
                    hasher.update(tpl_file.read_bytes())
            template_hash = hasher.hexdigest()[:16]

        manifest_content = (
            f"PACKAGE NAME: {pkg.name}-{pkg.version}\n"
            f"PACKAGE VERSION: {pkg.version}\n"
            f"UNCOMPRESSED SIZE: {human_size} ({total_size} bytes)\n"
            f"BUILD DATE: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            f"BUILD SYSTEM: InterGenOS igos-build\n"
            f"TEMPLATE_HASH: {template_hash}\n"
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
        """
        dangerous = []
        for entry in ("lib", "lib64", "bin", "sbin"):
            staged = staging_dir / entry
            root_path = Path("/") / entry
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
        shutil.rmtree(staging_dir, ignore_errors=True)
        return True

    def pkg_verify(self, pkg: Package) -> bool:
        """Verify every file in the manifest exists on the live filesystem."""
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
        """Snapshot all files and symlinks under key system directories."""
        if dirs is None:
            dirs = ["/usr", "/etc", "/opt", "/var/lib", "/lib"]
        snapshot = set()
        for d in dirs:
            if not os.path.isdir(d):
                continue
            for root, dirnames, files in os.walk(d, followlinks=False):
                for f in files:
                    snapshot.add(os.path.join(root, f))
                for dn in dirnames:
                    path = os.path.join(root, dn)
                    if os.path.islink(path):
                        snapshot.add(path)
        return snapshot

    def pkg_manifest_from_diff(self, pkg: Package, before: set[str], after: set[str]) -> bool:
        """Generate manifest from filesystem diff (for direct_install packages)."""
        new_files = sorted(after - before)

        if not new_files:
            self.logger.error(f"No new files detected for {pkg.name}-{pkg.version}")
            return False

        manifest_path = self.pkg_db / f"{pkg.name}-{pkg.version}"

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
```

---

## 8. Build Logger: igos-build/log.py

**Path:** `/mnt/intergenos/igos-build/log.py`

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

    def __del__(self):
        """Ensure log file is closed on garbage collection."""
        if self._file:
            try:
                self._file.close()
            except Exception:
                pass
            self._file = None

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

---

## 9. Build Styles Registry: igos-build/styles/__init__.py

**Path:** `/mnt/intergenos/igos-build/styles/__init__.py`

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

---

## 10. Build Style Base Class: igos-build/styles/base.py

**Path:** `/mnt/intergenos/igos-build/styles/base.py`

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

---

## 11. Autotools Style: igos-build/styles/autotools.py

**Path:** `/mnt/intergenos/igos-build/styles/autotools.py`

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

---

## 12. CMake Style: igos-build/styles/cmake.py

**Path:** `/mnt/intergenos/igos-build/styles/cmake.py`

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

---

## 13. Meson Style: igos-build/styles/meson.py

**Path:** `/mnt/intergenos/igos-build/styles/meson.py`

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
        # --wrap-mode=nodownload prevents meson from fetching subprojects
        # at configure time, enforcing the offline chroot build model
        base = "meson setup build --prefix=/usr --libdir=/usr/lib --buildtype=release --wrap-mode=nodownload"

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

---

## 14. Make Style: igos-build/styles/make.py

**Path:** `/mnt/intergenos/igos-build/styles/make.py`

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

---

## 15. Custom Style: igos-build/styles/custom.py

**Path:** `/mnt/intergenos/igos-build/styles/custom.py`

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

---

## 16. Package Functions (Bash): pkg-functions.sh

**Path:** `/mnt/intergenos/scripts/pkg-functions.sh`

```bash
#!/bin/bash
# InterGenOS Package Functions — DESTDIR Staging + Slackware-style Tracking
#
# Sourced by the Chapter 8 build runner. Provides functions to:
#   1. Stage a package's installed files via DESTDIR
#   2. Generate a file manifest
#   3. Create a compressed archive (.igos.tar.gz)
#   4. Deploy staged files to the live filesystem
#   5. Run post-install hooks on the live system
#
# Database: /var/lib/igos/packages/<name>-<version>  (one text file per package)
# Archives: /var/lib/igos/archives/<name>-<version>.igos.tar.gz
#
# Design: Slackware-style manifests — human-readable, cat-inspectable,
# no binary database, no dependency resolution at install time.
# The build system handles build order; this layer tracks installed state.

# ============================================================================
# Configuration
# ============================================================================

IGOS_PKG_DB="/var/lib/igos/packages"
IGOS_PKG_ARCHIVES="/var/lib/igos/archives"
IGOS_PKG_STAGING="/tmp/igos-staging"

# ============================================================================
# Internal helpers
# ============================================================================

pkg_log() {
    echo "[pkg] $*" | tee -a "$IGOS_LOGS/pkg-install.log"
}

pkg_error() {
    echo "[pkg] ERROR: $*" | tee -a "$IGOS_LOGS/pkg-install.log" >&2
}

# ============================================================================
# pkg_init — Create database and archive directories
# ============================================================================

pkg_init() {
    mkdir -pv "$IGOS_PKG_DB"
    mkdir -pv "$IGOS_PKG_ARCHIVES"
    mkdir -pv "$IGOS_PKG_STAGING"
}

# ============================================================================
# pkg_stage — Run install() with DESTDIR pointing to a staging directory
#
# Usage: pkg_stage <name> <version>
#
# Expects:
#   - CWD is the package build directory
#   - An install() function is defined (from the package's build.sh)
#   - Or a pkg_custom_install() function for exception packages
#
# Sets: PKG_DEST (the staging root for this package)
# ============================================================================

pkg_stage() {
    local name="$1"
    local version="$2"

    export PKG_DEST="${IGOS_PKG_STAGING}/${name}-${version}"

    # Clean any prior staging attempt
    rm -rf "$PKG_DEST"
    mkdir -pv "$PKG_DEST"

    # Mirror root-level symlinks so DESTDIR installs follow them.
    # Without this, `make install DESTDIR=...` creates real /lib, /bin, /sbin
    # directories that collide with the root filesystem's symlinks.
    for link in bin lib sbin; do
        if [ -L "/$link" ]; then
            ln -sv "usr/$link" "${PKG_DEST}/$link"
        fi
    done
    case $(uname -m) in
        x86_64) mkdir -pv "${PKG_DEST}/lib64" ;;
    esac
    mkdir -pv "${PKG_DEST}/usr/"{bin,lib,sbin}

    # Export DESTDIR for autotools/meson packages
    export DESTDIR="$PKG_DEST"

    pkg_log "Staging ${name}-${version} to ${PKG_DEST}"

    # Run the package's do_install function
    # Named do_install (not install) to avoid collision with /usr/bin/install.
    # Output appends to the most recent build log for this package so all
    # output is in one place. Falls back to a standalone install log.
    local install_log
    install_log=$(ls -t "${IGOS_LOGS}/${name}-"*".log" 2>/dev/null | head -1)
    if [ -z "$install_log" ]; then
        install_log="${IGOS_LOGS}/${name}-install-$(date '+%Y%m%d-%H%M%S').log"
    fi

    if declare -f do_install > /dev/null 2>&1; then
        echo "=== [INSTALL] $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$install_log"
        do_install >> "$install_log" 2>&1
    else
        pkg_error "No do_install() function defined for ${name}"
        return 1
    fi

    local rc=$?
    if [ $rc -ne 0 ]; then
        pkg_error "Staging failed for ${name}-${version} (exit $rc)"
        return 1
    fi

    # Verify something was actually staged
    local file_count
    file_count=$(find "$PKG_DEST" -not -type d | wc -l)
    if [ "$file_count" -eq 0 ]; then
        pkg_error "Staging produced no files for ${name}-${version}"
        pkg_error "Check that do_install() uses \$DESTDIR or the correct staging variable"
        return 1
    fi

    pkg_log "Staged ${file_count} files for ${name}-${version}"

    # Unset DESTDIR so it doesn't leak into post-install steps
    unset DESTDIR

    return 0
}

# ============================================================================
# pkg_manifest — Generate a Slackware-style manifest from staged files
#
# Usage: pkg_manifest <name> <version> [description]
#
# Writes: $IGOS_PKG_DB/<name>-<version>
# ============================================================================

pkg_manifest() {
    local name="$1"
    local version="$2"
    local description="${3:-No description}"
    local dest="${IGOS_PKG_STAGING}/${name}-${version}"
    local manifest="${IGOS_PKG_DB}/${name}-${version}"

    if [ ! -d "$dest" ]; then
        pkg_error "No staging directory found for ${name}-${version}"
        return 1
    fi

    # Calculate sizes
    local uncompressed_size
    uncompressed_size=$(du -sb "$dest" | cut -f1)
    local uncompressed_human
    uncompressed_human=$(du -sh "$dest" | cut -f1)

    # Generate file list — paths relative to staging root, sorted
    # Directories listed with trailing /
    local file_list
    file_list=$(cd "$dest" && find . -mindepth 1 | sed 's|^\./||' | sort)

    # Write the manifest
    cat > "$manifest" << EOF
PACKAGE NAME: ${name}-${version}
PACKAGE VERSION: ${version}
UNCOMPRESSED SIZE: ${uncompressed_human} (${uncompressed_size} bytes)
BUILD DATE: $(date -u '+%Y-%m-%dT%H:%M:%SZ')
BUILD SYSTEM: InterGenOS LFS 13.0
DESCRIPTION:
${name}: ${description}

FILE LIST:
${file_list}
EOF

    pkg_log "Manifest written: ${manifest} ($(echo "$file_list" | wc -l) entries)"
    return 0
}

# ============================================================================
# pkg_archive — Create a .igos.tar.gz archive from staged files
#
# Usage: pkg_archive <name> <version>
#
# Creates: $IGOS_PKG_ARCHIVES/<name>-<version>.igos.tar.gz
#
# Uses gzip during initial build (available from Chapter 7).
# Archives can be re-compressed to zstd later if desired.
# ============================================================================

pkg_archive() {
    local name="$1"
    local version="$2"
    local dest="${IGOS_PKG_STAGING}/${name}-${version}"
    local archive="${IGOS_PKG_ARCHIVES}/${name}-${version}.igos.tar.gz"

    if [ ! -d "$dest" ]; then
        pkg_error "No staging directory found for ${name}-${version}"
        return 1
    fi

    # Create the archive — rooted at the staging directory so paths are relative
    # This means extracting to / will put files in the right place
    tar -C "$dest" -czf "$archive" .

    local rc=$?
    if [ $rc -ne 0 ]; then
        pkg_error "Archive creation failed for ${name}-${version}"
        return 1
    fi

    local archive_size
    archive_size=$(du -sh "$archive" | cut -f1)
    pkg_log "Archive created: ${archive} (${archive_size})"

    # Update manifest with compressed size
    local manifest="${IGOS_PKG_DB}/${name}-${version}"
    if [ -f "$manifest" ]; then
        local compressed_bytes
        compressed_bytes=$(stat -c%s "$archive")
        sed -i "/^BUILD DATE:/i COMPRESSED SIZE: ${archive_size} (${compressed_bytes} bytes)" "$manifest"
    fi

    return 0
}

# ============================================================================
# pkg_deploy — Copy staged files to the live filesystem
#
# Usage: pkg_deploy <name> <version>
#
# Copies everything from the staging directory to /
# Preserves permissions, ownership, and symlinks
#
# Safety: pre-checks for top-level entries that would collide with root-level
# symlinks (lib -> usr/lib, bin -> usr/bin, etc.). A package staging a real
# directory over one of these symlinks would kill the system.
# ============================================================================

pkg_deploy() {
    local name="$1"
    local version="$2"
    local dest="${IGOS_PKG_STAGING}/${name}-${version}"

    if [ ! -d "$dest" ]; then
        pkg_error "No staging directory found for ${name}-${version}"
        return 1
    fi

    # Pre-deploy safety check: detect staging entries that would collide with
    # root-level symlinks. These symlinks (lib -> usr/lib, bin -> usr/bin, etc.)
    # are load-bearing — replacing them with real directories is catastrophic.
    local dangerous=""
    for entry in lib lib64 bin sbin; do
        if [ -d "${dest}/${entry}" ] && [ ! -L "${dest}/${entry}" ] && [ -L "/${entry}" ]; then
            dangerous="${dangerous} ${entry}"
        fi
    done

    if [ -n "$dangerous" ]; then
        pkg_error "DANGEROUS: ${name}-${version} staging contains top-level dirs" \
                  "that would collide with root symlinks:${dangerous}"
        pkg_error "Fix the package build.sh to install to usr/ paths instead"
        return 1
    fi

    pkg_log "Deploying ${name}-${version} to live filesystem"

    # Use tar for deployment:
    # --no-overwrite-dir    preserves metadata of existing real directories
    # --keep-directory-symlink  follows existing symlinks to directories instead
    #                           of replacing them (e.g., /var/run -> /run)
    tar -C "${dest}" -cf - . \
        | tar -C / -xf - --no-overwrite-dir --keep-directory-symlink

    local rc=$?
    if [ $rc -ne 0 ]; then
        pkg_error "Deploy failed for ${name}-${version}"
        return 1
    fi

    pkg_log "Deployed ${name}-${version}"
    return 0
}

# ============================================================================
# pkg_cleanup — Remove staging directory after successful install
#
# Usage: pkg_cleanup <name> <version>
# ============================================================================

pkg_cleanup() {
    local name="$1"
    local version="$2"
    local dest="${IGOS_PKG_STAGING}/${name}-${version}"

    rm -rf "$dest"
}

# ============================================================================
# pkg_install — Full pipeline: stage -> manifest -> archive -> deploy -> cleanup
#
# Usage: pkg_install <name> <version> [description]
#
# This is the main entry point called by the build runner after
# configure/build/check have completed.
# ============================================================================

pkg_install() {
    local name="$1"
    local version="$2"
    local description="${3:-No description}"

    pkg_log "=========================================="
    pkg_log "Installing package: ${name}-${version}"
    pkg_log "=========================================="

    local start
    start=$(date +%s)

    # Ensure database directories exist
    pkg_init

    # Stage
    pkg_stage "$name" "$version" || return 1

    # Generate manifest
    pkg_manifest "$name" "$version" "$description" || return 1

    # Create archive
    pkg_archive "$name" "$version" || return 1

    # Deploy to live filesystem
    pkg_deploy "$name" "$version" || return 1

    # Clean up staging directory
    pkg_cleanup "$name" "$version"

    local elapsed=$(( $(date +%s) - start ))
    pkg_log "Package ${name}-${version} installed successfully (${elapsed}s)"
    pkg_log ""

    return 0
}

# ============================================================================
# pkg_info — Display information about an installed package
#
# Usage: pkg_info <name>-<version>
#    or: pkg_info (no args — list all installed packages)
# ============================================================================

pkg_info() {
    if [ -z "$1" ]; then
        # List all installed packages
        if [ -d "$IGOS_PKG_DB" ]; then
            for manifest in "$IGOS_PKG_DB"/*; do
                [ -f "$manifest" ] || continue
                local pkg_name pkg_version
                pkg_name=$(grep "^PACKAGE NAME:" "$manifest" | cut -d: -f2- | tr -d ' ')
                pkg_version=$(grep "^PACKAGE VERSION:" "$manifest" | cut -d: -f2- | tr -d ' ')
                local desc
                desc=$(grep "^${pkg_name%%"-$pkg_version"}:" "$manifest" | head -1)
                echo "${pkg_name}  ${desc:+— $desc}"
            done
        else
            echo "No packages installed."
        fi
    else
        # Show specific package
        local manifest="${IGOS_PKG_DB}/$1"
        if [ -f "$manifest" ]; then
            cat "$manifest"
        else
            echo "Package $1 is not installed."
            return 1
        fi
    fi
}

# ============================================================================
# pkg_files — List files owned by an installed package
#
# Usage: pkg_files <name>-<version>
# ============================================================================

pkg_files() {
    local manifest="${IGOS_PKG_DB}/$1"
    if [ ! -f "$manifest" ]; then
        echo "Package $1 is not installed."
        return 1
    fi

    # Extract file list (everything after "FILE LIST:" line)
    sed -n '/^FILE LIST:$/,$ { /^FILE LIST:$/d; p }' "$manifest"
}

# ============================================================================
# pkg_owner — Find which package owns a file
#
# Usage: pkg_owner /usr/bin/gcc
# ============================================================================

pkg_owner() {
    local target="$1"

    # Strip leading / for comparison against manifest paths
    target="${target#/}"

    if [ -d "$IGOS_PKG_DB" ]; then
        for manifest in "$IGOS_PKG_DB"/*; do
            [ -f "$manifest" ] || continue
            if sed -n '/^FILE LIST:$/,$ p' "$manifest" | grep -qx "$target"; then
                basename "$manifest"
            fi
        done
    fi
}

# ============================================================================
# pkg_remove — Remove an installed package
#
# Usage: pkg_remove <name>-<version>
#
# Removes all files owned by the package (in reverse order so dirs come last),
# then removes the manifest. Does NOT remove the archive.
# ============================================================================

pkg_remove() {
    local pkg="$1"
    local manifest="${IGOS_PKG_DB}/${pkg}"

    if [ ! -f "$manifest" ]; then
        pkg_error "Package ${pkg} is not installed."
        return 1
    fi

    pkg_log "Removing package: ${pkg}"

    # Get file list, reverse sorted (files before their parent directories)
    local files
    files=$(pkg_files "$pkg" | sort -r)

    local removed=0
    local skipped=0

    while IFS= read -r file; do
        [ -z "$file" ] && continue
        local fullpath="/${file}"

        if [ -d "$fullpath" ] && [ ! -L "$fullpath" ]; then
            # Only remove directory if empty
            rmdir "$fullpath" 2>/dev/null && removed=$((removed+1))
        elif [ -e "$fullpath" ] || [ -L "$fullpath" ]; then
            rm -f "$fullpath" && removed=$((removed+1))
        else
            skipped=$((skipped+1))
        fi
    done <<< "$files"

    # Remove the manifest
    rm -f "$manifest"

    pkg_log "Removed ${pkg}: ${removed} files/dirs removed, ${skipped} already absent"
    return 0
}
```

---

## 17. Disk Image Creator: create-image.sh

**Path:** `/mnt/intergenos/scripts/create-image.sh`

```bash
#!/bin/bash
# InterGenOS — Package chroot into bootable disk image
#
# Takes the completed chroot at /mnt/igos and creates a bootable disk
# image. Supports both VM (qcow2) and bare metal (raw) targets.
#
# Must run on the HOST (not inside the chroot).
# Requires: qemu-img, qemu-nbd, parted, mkfs.ext4, dosfstools (mkfs.fat)
#
# Usage:
#   sudo bash /mnt/intergenos/scripts/create-image.sh <output-path> [disk-size]
#
# Examples:
#   # For VM (qcow2):
#   sudo bash /mnt/intergenos/scripts/create-image.sh /mnt/intergenos/build/intergenos.qcow2 500G
#
#   # For USB/bare metal (raw):
#   sudo bash /mnt/intergenos/scripts/create-image.sh /mnt/intergenos/build/intergenos.img 64G
#   dd if=/mnt/intergenos/build/intergenos.img of=/dev/sdX bs=4M status=progress

set -euo pipefail

CHROOT=/mnt/igos
IMAGE="${1:?Usage: create-image.sh <output-path.qcow2> [disk-size]}"
DISK_SIZE="${2:-500G}"
NBD_DEV=/dev/nbd0
MOUNT_POINT=/mnt/image-root

log() {
    echo "[IMAGE] $*"
}

err() {
    echo "[ERROR] $*" >&2
}

cleanup() {
    log "Cleaning up..."
    umount "${MOUNT_POINT}/sys" 2>/dev/null || true
    umount "${MOUNT_POINT}/proc" 2>/dev/null || true
    umount "${MOUNT_POINT}/dev/pts" 2>/dev/null || true
    umount "${MOUNT_POINT}/dev" 2>/dev/null || true
    umount "$MOUNT_POINT" 2>/dev/null || true
    qemu-nbd --disconnect "$NBD_DEV" 2>/dev/null || true
}

trap cleanup EXIT

# ============================================================================
# Preflight checks
# ============================================================================

if [ "$(id -u)" -ne 0 ]; then
    err "Must run as root"
    exit 1
fi

if [ ! -d "$CHROOT/usr/bin" ]; then
    err "Chroot at $CHROOT doesn't look valid (no /usr/bin)"
    exit 1
fi

if [ ! -f "$CHROOT/boot/vmlinuz-"* ] 2>/dev/null; then
    err "No kernel found in $CHROOT/boot/"
    exit 1
fi

for tool in qemu-img qemu-nbd parted mkfs.ext4; do
    if ! command -v "$tool" > /dev/null 2>&1; then
        err "Required tool not found: $tool"
        exit 1
    fi
done

# ============================================================================
# Step 1: Create disk image (qcow2 for VM, raw for bare metal/USB)
# ============================================================================

# Detect format from file extension
case "$IMAGE" in
    *.qcow2) IMAGE_FORMAT="qcow2" ;;
    *.img|*.raw) IMAGE_FORMAT="raw" ;;
    *) IMAGE_FORMAT="qcow2" ;;  # default to qcow2
esac

log "Creating ${DISK_SIZE} ${IMAGE_FORMAT} image at ${IMAGE}..."
qemu-img create -f "$IMAGE_FORMAT" "$IMAGE" "$DISK_SIZE"

# ============================================================================
# Step 2: Connect image as block device
# ============================================================================

log "Loading nbd module and connecting image..."
modprobe nbd max_part=8
qemu-nbd --connect="$NBD_DEV" -f "$IMAGE_FORMAT" "$IMAGE"

# Wait for device to appear
sleep 1

# ============================================================================
# Step 3: Partition the disk (GPT + BIOS boot)
# ============================================================================

log "Creating partition table (GPT with BIOS + EFI support)..."
parted -s "$NBD_DEV" mklabel gpt
parted -s "$NBD_DEV" mkpart bios_grub 1MiB 2MiB
parted -s "$NBD_DEV" set 1 bios_grub on
parted -s "$NBD_DEV" mkpart ESP fat32 2MiB 514MiB
parted -s "$NBD_DEV" set 2 esp on
parted -s "$NBD_DEV" mkpart root ext4 514MiB 100%

# Wait for partition devices
sleep 1
partprobe "$NBD_DEV" 2>/dev/null || true
sleep 1

# ============================================================================
# Step 4: Format partitions
# ============================================================================

log "Formatting partitions..."
mkfs.fat -F32 -n ESP "${NBD_DEV}p2"
mkfs.ext4 -L intergenos "${NBD_DEV}p3"

# ============================================================================
# Step 5: Mount and copy chroot contents
# ============================================================================

log "Mounting image and copying chroot..."
mkdir -p "$MOUNT_POINT"
mount "${NBD_DEV}p3" "$MOUNT_POINT"

# Use tar to preserve everything correctly
# --one-file-system avoids copying virtual filesystems (/proc, /sys, etc.)
tar -C "$CHROOT" --one-file-system -cf - . | tar -C "$MOUNT_POINT" -xf -

log "  Copy complete: $(du -sh "$MOUNT_POINT" | cut -f1)"

# Fix root directory ownership — tar preserves the chroot's ownership
# which is the build user, not root
chown root:root "$MOUNT_POINT"

# ============================================================================
# Step 6: Create /etc/fstab
# ============================================================================

log "Writing /etc/fstab..."
# Use UUIDs for portability across VM and bare metal
ROOT_UUID=$(blkid -s UUID -o value "${NBD_DEV}p3")
ESP_UUID=$(blkid -s UUID -o value "${NBD_DEV}p2")
cat > "${MOUNT_POINT}/etc/fstab" << FSTABEOF
# /etc/fstab — InterGenOS
# <file system>                            <mount point>  <type>  <options>              <dump>  <pass>
UUID=${ROOT_UUID}  /              ext4    defaults,noatime       1       1
UUID=${ESP_UUID}  /boot/efi      vfat    fmask=0077,dmask=0077  0       2
FSTABEOF
log "  Root UUID: ${ROOT_UUID}"
log "  ESP UUID:  ${ESP_UUID}"

# ============================================================================
# Step 7: Create /etc/default/grub
# ============================================================================

log "Writing GRUB defaults..."
mkdir -p "${MOUNT_POINT}/etc/default"
cat > "${MOUNT_POINT}/etc/default/grub" << GRUBEOF
# GRUB defaults for InterGenOS
GRUB_DEFAULT=0
GRUB_TIMEOUT=5
GRUB_DISTRIBUTOR="InterGenOS"
GRUB_CMDLINE_LINUX_DEFAULT=""
GRUB_CMDLINE_LINUX="root=UUID=${ROOT_UUID} console=tty0 console=ttyS0,115200"
GRUB_TERMINAL="console serial"
GRUB_SERIAL_COMMAND="serial --speed=115200"
GRUB_DISABLE_OS_PROBER=true
GRUBEOF

# ============================================================================
# Step 8: Install GRUB bootloader
# ============================================================================

log "Installing GRUB (BIOS + EFI)..."

# Mount ESP
mkdir -p "${MOUNT_POINT}/boot/efi"
mount "${NBD_DEV}p2" "${MOUNT_POINT}/boot/efi"

# Bind mount host filesystems into the image
mount --bind /dev "${MOUNT_POINT}/dev"
mount --bind /dev/pts "${MOUNT_POINT}/dev/pts"
mount -t proc proc "${MOUNT_POINT}/proc"
mount -t sysfs sysfs "${MOUNT_POINT}/sys"

# Install GRUB for BIOS boot
chroot "$MOUNT_POINT" grub-install --target=i386-pc "$NBD_DEV"

# Install GRUB for EFI boot (skip if x86_64-efi modules not built)
if [ -d "${MOUNT_POINT}/usr/lib/grub/x86_64-efi" ]; then
    chroot "$MOUNT_POINT" grub-install --target=x86_64-efi \
        --efi-directory=/boot/efi --bootloader-id=InterGenOS --removable
else
    log "  WARNING: x86_64-efi GRUB modules not found — skipping EFI install"
fi

# Generate GRUB config.
# grub-mkconfig runs inside the chroot where root is mounted via NBD,
# so it detects /dev/nbd0pN as the root device. Override with UUID.
chroot "$MOUNT_POINT" /bin/bash -c \
    "GRUB_DEVICE=UUID=${ROOT_UUID} grub-mkconfig -o /boot/grub/grub.cfg"

# Belt and suspenders: ensure no NBD references leaked through
sed -i "s|/dev/nbd[0-9]*p[0-9]*|UUID=${ROOT_UUID}|g" "${MOUNT_POINT}/boot/grub/grub.cfg"

# Unmount bind mounts and ESP
umount "${MOUNT_POINT}/sys"
umount "${MOUNT_POINT}/proc"
umount "${MOUNT_POINT}/dev/pts"
umount "${MOUNT_POINT}/dev"
umount "${MOUNT_POINT}/boot/efi"

# ============================================================================
# Step 8b: Apply post-deploy fixes for VM boot
# ============================================================================

log "Applying post-deploy fixes..."

# Fix sudo setuid bit (tar strips setuid during copy)
if [ -f "${MOUNT_POINT}/usr/bin/sudo" ]; then
    chmod 4755 "${MOUNT_POINT}/usr/bin/sudo"
    log "  sudo setuid bit restored"
fi

# Enable serial console for VM management
chroot "$MOUNT_POINT" /bin/bash -c '
    ln -sf /usr/lib/systemd/system/serial-getty@.service \
        /etc/systemd/system/getty.target.wants/serial-getty@ttyS0.service
'

# Enable networking (systemd-networkd + resolved)
chroot "$MOUNT_POINT" /bin/bash -c '
    ln -sf /usr/lib/systemd/system/systemd-networkd.service \
        /etc/systemd/system/multi-user.target.wants/systemd-networkd.service
    ln -sf /usr/lib/systemd/system/systemd-resolved.service \
        /etc/systemd/system/multi-user.target.wants/systemd-resolved.service
'

# Create DHCP network config
mkdir -p "${MOUNT_POINT}/etc/systemd/network"
cat > "${MOUNT_POINT}/etc/systemd/network/10-dhcp.network" << 'NETEOF'
[Match]
Name=en*

[Network]
DHCP=yes
NETEOF

# Set up DNS resolution via systemd-resolved
ln -sf /run/systemd/resolve/stub-resolv.conf "${MOUNT_POINT}/etc/resolv.conf"

# Set root password — override with ROOT_PASSWORD env var
IMAGE_ROOT_PASSWORD="${ROOT_PASSWORD:-intergenos}"
if [ "$IMAGE_ROOT_PASSWORD" = "intergenos" ]; then
    log "  WARNING: Using default root password — set ROOT_PASSWORD env var for production"
fi
echo "root:${IMAGE_ROOT_PASSWORD}" | chroot "$MOUNT_POINT" chpasswd
chroot "$MOUNT_POINT" passwd -x 99999 root

# Create default user account — override with IMAGE_USER env var
IMAGE_USER="${IMAGE_USER:-christopher}"
IMAGE_USER_PASSWORD="${IMAGE_USER_PASSWORD:-intergenos}"
if ! chroot "$MOUNT_POINT" id "$IMAGE_USER" > /dev/null 2>&1; then
    chroot "$MOUNT_POINT" useradd -m -G wheel,video,audio,input -s /bin/bash "$IMAGE_USER"
    echo "${IMAGE_USER}:${IMAGE_USER_PASSWORD}" | chroot "$MOUNT_POINT" chpasswd
    # Copy skel files
    if [ -d "${MOUNT_POINT}/etc/skel" ]; then
        cp -a "${MOUNT_POINT}/etc/skel/." "${MOUNT_POINT}/home/${IMAGE_USER}/"
        chroot "$MOUNT_POINT" chown -R "${IMAGE_USER}:${IMAGE_USER}" "/home/${IMAGE_USER}"
    fi
    log "  User '${IMAGE_USER}' created (groups: wheel,video,audio,input)"
fi

# Enable GDM and set graphical target for desktop boot
if [ -f "${MOUNT_POINT}/usr/lib/systemd/system/gdm.service" ]; then
    chroot "$MOUNT_POINT" /bin/bash -c '
        systemctl enable gdm
        systemctl set-default graphical.target
    '
    log "  GDM enabled, default target set to graphical"
fi

# Fix /tmp/.X11-unix ownership (must be root-owned with sticky bit)
# Create tmpfiles.d config so systemd maintains it across reboots
mkdir -p "${MOUNT_POINT}/etc/tmpfiles.d"
cat > "${MOUNT_POINT}/etc/tmpfiles.d/x11.conf" << 'TMPEOF'
d /tmp/.X11-unix 1777 root root -
TMPEOF
mkdir -p "${MOUNT_POINT}/tmp/.X11-unix"
chown root:root "${MOUNT_POINT}/tmp/.X11-unix"
chmod 1777 "${MOUNT_POINT}/tmp/.X11-unix"

# Ensure /tmp itself has correct permissions
chmod 1777 "${MOUNT_POINT}/tmp"

# Build icon caches, font caches, and compile GSettings schemas
chroot "$MOUNT_POINT" /bin/bash -c '
    # GSettings schemas
    if [ -d /usr/share/glib-2.0/schemas ]; then
        glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null
    fi

    # Icon caches
    for theme_dir in /usr/share/icons/*/; do
        if [ -f "${theme_dir}index.theme" ]; then
            gtk-update-icon-cache -q "${theme_dir}" 2>/dev/null || true
        fi
    done

    # Font cache
    if command -v fc-cache >/dev/null 2>&1; then
        fc-cache -f 2>/dev/null
    fi

    # GIO module cache
    if command -v gio-querymodules >/dev/null 2>&1; then
        gio-querymodules /usr/lib/gio/modules 2>/dev/null || true
    fi

    # gdk-pixbuf loader cache
    if command -v gdk-pixbuf-query-loaders >/dev/null 2>&1; then
        gdk-pixbuf-query-loaders --update-cache 2>/dev/null || true
    fi

    # MIME database
    if command -v update-mime-database >/dev/null 2>&1; then
        update-mime-database /usr/share/mime 2>/dev/null || true
    fi

    # Desktop database
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database /usr/share/applications 2>/dev/null || true
    fi

    # Linker cache — must run after all libraries are installed
    ldconfig 2>/dev/null

    # Enable essential desktop services
    systemctl enable avahi-daemon.service 2>/dev/null || true
    systemctl enable cups.service 2>/dev/null || true
    systemctl enable bluetooth.service 2>/dev/null || true
' 2>/dev/null
log "  Caches built (icons, fonts, schemas, GIO, pixbuf, MIME, desktop, ldconfig)"

log "  Post-deploy fixes applied (serial console, networking, DNS, root password, GDM, services, caches)"

# ============================================================================
# Step 9: Unmount and disconnect
# ============================================================================

log "Unmounting image..."
umount "$MOUNT_POINT"

log "Disconnecting NBD..."
qemu-nbd --disconnect "$NBD_DEV"

# Clear the trap since we cleaned up manually
trap - EXIT

# ============================================================================
# Done
# ============================================================================

FINAL_SIZE=$(du -h "$IMAGE" | cut -f1)

log ""
log "============================================"
log "  InterGenOS disk image created"
log "  Image: $IMAGE"
log "  Size:  $FINAL_SIZE"
log "============================================"
log "  Format: ${IMAGE_FORMAT}"
log ""
if [ "$IMAGE_FORMAT" = "qcow2" ]; then
    log "  Create a VM with:"
    log "    virt-install --name intergenos --ram 12288 --vcpus 12 \\"
    log "      --cpu host-passthrough --machine q35 --os-variant linux2022 \\"
    log "      --disk path=$IMAGE,format=qcow2,bus=virtio \\"
    log "      --import --network network=default,model=virtio \\"
    log "      --graphics vnc,listen=0.0.0.0 --video virtio --noautoconsole"
    log ""
    log "  Convert to raw for USB:"
    log "    qemu-img convert -f qcow2 -O raw $IMAGE intergenos.img"
    log "    sudo dd if=intergenos.img of=/dev/sdX bs=4M status=progress"
else
    log "  Write to USB drive:"
    log "    sudo dd if=$IMAGE of=/dev/sdX bs=4M status=progress"
    log ""
    log "  Or create a VM from raw:"
    log "    qemu-img convert -f raw -O qcow2 $IMAGE intergenos.qcow2"
fi
log ""
```

---

## 18. Host Requirements Checker: host-check.py

**Path:** `/mnt/intergenos/scripts/host-check.py`

```python
#!/usr/bin/env python3
"""InterGenOS Host System Requirements Check

Validates that the build host meets all LFS 13.0 minimum requirements
before attempting a build. Replaces the original req_check.sh from
build_003 with proper Python, structured output, and clear diagnostics.

Usage:
    python3 scripts/host-check.py              # Check local system
    python3 scripts/host-check.py --remote     # Check VM via SSH
"""

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# LFS 13.0 minimum requirements (Section 2.2)
# ---------------------------------------------------------------------------

@dataclass
class Requirement:
    """A single host system requirement."""
    name: str
    min_version: str
    command: str                      # Shell command to get version string
    version_regex: str = ""           # Regex to extract version number
    symlink_check: str = ""           # Path that should be a symlink
    symlink_target: str = ""          # Expected symlink target (substring)
    max_version: str = ""             # Maximum tested version (warning only)
    notes: str = ""
    required: bool = True


REQUIREMENTS = [
    Requirement(
        name="Bash",
        min_version="3.2",
        command="bash --version | head -1",
        version_regex=r"version (\d+\.\d+)",
        symlink_check="/bin/sh",
        symlink_target="bash",
        notes="/bin/sh must be a link to bash",
    ),
    Requirement(
        name="Binutils",
        min_version="2.13.1",
        command="ld --version | head -1",
        version_regex=r"(\d+\.\d+(?:\.\d+)?)",
        max_version="2.46.0",
        notes="Versions > 2.46.0 not tested by LFS",
    ),
    Requirement(
        name="Bison",
        min_version="2.7",
        command="bison --version | head -1",
        version_regex=r"(\d+\.\d+(?:\.\d+)?)",
        symlink_check="/usr/bin/yacc",
        symlink_target="bison",
        notes="/usr/bin/yacc should link to bison",
    ),
    Requirement(
        name="Coreutils",
        min_version="8.1",
        command="chown --version | head -1",
        version_regex=r"(\d+\.\d+)",
    ),
    Requirement(
        name="Diffutils",
        min_version="2.8.1",
        command="diff --version | head -1",
        version_regex=r"(\d+\.\d+(?:\.\d+)?)",
    ),
    Requirement(
        name="Findutils",
        min_version="4.2.31",
        command="find --version | head -1",
        version_regex=r"(\d+\.\d+(?:\.\d+)?)",
    ),
    Requirement(
        name="Gawk",
        min_version="4.0.1",
        command="gawk --version | head -1",
        version_regex=r"GNU Awk (\d+\.\d+\.\d+)",
        symlink_check="/usr/bin/awk",
        symlink_target="gawk",
        notes="/usr/bin/awk should link to gawk",
    ),
    Requirement(
        name="GCC",
        min_version="5.4",
        command="gcc --version | head -1",
        version_regex=r"(\d+\.\d+\.\d+)",
        max_version="15.2.0",
        notes="Versions > 15.2.0 not tested by LFS",
    ),
    Requirement(
        name="G++",
        min_version="5.4",
        command="g++ --version | head -1",
        version_regex=r"(\d+\.\d+\.\d+)",
    ),
    Requirement(
        name="Grep",
        min_version="2.5.1",
        command="grep --version | head -1",
        version_regex=r"(\d+\.\d+(?:\.\d+)?)",
    ),
    Requirement(
        name="Gzip",
        min_version="1.3.12",
        command="gzip --version | head -1",
        version_regex=r"(\d+\.\d+)",
    ),
    Requirement(
        name="Linux Kernel",
        min_version="5.4",
        command="uname -r",
        version_regex=r"(\d+\.\d+)",
        notes="CONFIG_UNIX98_PTYS must be set to y",
    ),
    Requirement(
        name="M4",
        min_version="1.4.10",
        command="m4 --version | head -1",
        version_regex=r"(\d+\.\d+\.\d+)",
    ),
    Requirement(
        name="Make",
        min_version="4.0",
        command="make --version | head -1",
        version_regex=r"(\d+\.\d+(?:\.\d+)?)",
    ),
    Requirement(
        name="Patch",
        min_version="2.5.4",
        command="patch --version | head -1",
        version_regex=r"(\d+\.\d+(?:\.\d+)?)",
    ),
    Requirement(
        name="Perl",
        min_version="5.8.8",
        command='perl -e "print $^V"',
        version_regex=r"v(\d+\.\d+\.\d+)",
    ),
    Requirement(
        name="Python",
        min_version="3.4",
        command="python3 --version",
        version_regex=r"(\d+\.\d+\.\d+)",
    ),
    Requirement(
        name="Sed",
        min_version="4.1.5",
        command="sed --version | head -1",
        version_regex=r"(\d+\.\d+)",
    ),
    Requirement(
        name="Tar",
        min_version="1.22",
        command="tar --version | head -1",
        version_regex=r"(\d+\.\d+)",
    ),
    Requirement(
        name="Texinfo",
        min_version="5.0",
        command="makeinfo --version | head -1",
        version_regex=r"(\d+\.\d+)",
    ),
    Requirement(
        name="Xz",
        min_version="5.0.0",
        command="xz --version | head -1",
        version_regex=r"(\d+\.\d+\.\d+)",
    ),
]


# ---------------------------------------------------------------------------
# Version comparison
# ---------------------------------------------------------------------------

def parse_version(version_str: str) -> tuple[int, ...]:
    """Parse a version string into a tuple of ints for comparison."""
    parts = re.findall(r"\d+", version_str)
    return tuple(int(p) for p in parts)


def version_ge(actual: str, minimum: str) -> bool:
    """Check if actual version >= minimum version."""
    return parse_version(actual) >= parse_version(minimum)


def version_le(actual: str, maximum: str) -> bool:
    """Check if actual version <= maximum version."""
    return parse_version(actual) <= parse_version(maximum)


# ---------------------------------------------------------------------------
# Check execution
# ---------------------------------------------------------------------------

def run_command(cmd: str, remote: Optional[str] = None) -> tuple[int, str]:
    """Run a command locally or via SSH. Returns (exit_code, output)."""
    if remote:
        full_cmd = f"ssh {remote} '{cmd}'"
    else:
        full_cmd = cmd

    try:
        result = subprocess.run(
            full_cmd, shell=True, capture_output=True, text=True, timeout=10
        )
        output = (result.stdout + result.stderr).strip()
        return result.returncode, output
    except subprocess.TimeoutExpired:
        return 1, "(command timed out)"
    except Exception as e:
        return 1, f"(error: {e})"


def check_symlink(path: str, expected_target: str, remote: Optional[str] = None) -> tuple[bool, str]:
    """Check if a symlink exists and points to the expected target."""
    code, output = run_command(f"readlink -f {path}", remote)
    if code != 0:
        return False, f"{path} not found"
    if expected_target in output:
        return True, f"{path} -> {output}"
    return False, f"{path} -> {output} (expected {expected_target})"


def check_compilation(remote: Optional[str] = None) -> tuple[bool, str]:
    """Test that gcc and g++ can compile and link a simple program."""
    test_code = 'echo "int main(){}" > /tmp/igos-check.c'
    results = []

    # gcc test
    cmd = f'{test_code} && gcc /tmp/igos-check.c -o /tmp/igos-check && echo "gcc OK" && rm -f /tmp/igos-check /tmp/igos-check.c'
    code, output = run_command(cmd, remote)
    if code == 0 and "gcc OK" in output:
        results.append(("gcc compile+link", True, "OK"))
    else:
        results.append(("gcc compile+link", False, output))

    # g++ test
    cmd = f'{test_code} && g++ /tmp/igos-check.c -o /tmp/igos-check && echo "g++ OK" && rm -f /tmp/igos-check /tmp/igos-check.c'
    code, output = run_command(cmd, remote)
    if code == 0 and "g++ OK" in output:
        results.append(("g++ compile+link", True, "OK"))
    else:
        results.append(("g++ compile+link", False, output))

    return results


def check_library_consistency(remote: Optional[str] = None) -> tuple[bool, str]:
    """Check GMP/MPFR/MPC .la file consistency (all present or all absent)."""
    libs = ["libgmp.la", "libmpfr.la", "libmpc.la"]
    found = []

    for lib in libs:
        cmd = f"find /usr/lib* -name '{lib}' 2>/dev/null | head -1"
        code, output = run_command(cmd, remote)
        found.append(bool(output.strip()))

    if all(found):
        return True, "all present (consistent)"
    elif not any(found):
        return True, "all absent (consistent)"
    else:
        present = [l for l, f in zip(libs, found) if f]
        absent = [l for l, f in zip(libs, found) if not f]
        return False, f"INCONSISTENT — present: {present}, absent: {absent}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    remote = None
    if "--remote" in sys.argv:
        remote = "christopher@192.168.122.69"

    target = f"remote ({remote})" if remote else "local system"

    print("=" * 72)
    print(f"  InterGenOS Host System Requirements Check")
    print(f"  LFS 13.0-systemd minimum requirements")
    print(f"  Target: {target}")
    print("=" * 72)
    print()

    passed = 0
    failed = 0
    warnings = 0

    # --- Tool version checks ---
    print("--- Tool Versions ---\n")

    for req in REQUIREMENTS:
        code, output = run_command(req.command, remote)

        if code != 0 and not output:
            status = "FAIL"
            version = "NOT FOUND"
            detail = ""
            failed += 1
        else:
            # Extract version
            match = re.search(req.version_regex, output) if req.version_regex else None
            if match:
                version = match.group(1)
            else:
                version = output[:60]

            # Check minimum
            if match and not version_ge(version, req.min_version):
                status = "FAIL"
                detail = f"(need >= {req.min_version})"
                failed += 1
            elif match and req.max_version and not version_le(version, req.max_version):
                status = "WARN"
                detail = f"(> {req.max_version} — not tested by LFS)"
                warnings += 1
            else:
                status = "OK"
                detail = ""
                passed += 1

        pad = 16 - len(req.name)
        print(f"  [{status:4s}] {req.name}{' ' * pad}{version}  {detail}")

    # --- Symlink checks ---
    print("\n--- Symlink Checks ---\n")

    for req in REQUIREMENTS:
        if not req.symlink_check:
            continue
        ok, detail = check_symlink(req.symlink_check, req.symlink_target, remote)
        status = "OK" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"  [{status:4s}] {detail}")

    # --- Compilation tests ---
    print("\n--- Compilation Tests ---\n")

    for name, ok, detail in check_compilation(remote):
        status = "OK" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"  [{status:4s}] {name}: {detail}")

    # --- Library consistency ---
    print("\n--- Library Consistency (GMP/MPFR/MPC) ---\n")

    ok, detail = check_library_consistency(remote)
    status = "OK" if ok else "FAIL"
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"  [{status:4s}] {detail}")

    # --- Hardware ---
    print("\n--- Hardware ---\n")

    code, output = run_command("nproc", remote)
    cores = output.strip() if code == 0 else "unknown"
    code, output = run_command("head -1 /proc/meminfo", remote)
    if code == 0 and output.strip():
        match = re.search(r"(\d+)", output)
        if match:
            ram_kb = int(match.group(1))
            ram = f"{ram_kb // 1048576}G" if ram_kb >= 1048576 else f"{ram_kb // 1024}M"
        else:
            ram = "unknown"
    else:
        ram = "unknown"
    code, output = run_command("stat -f --format=%a_%S /", remote)
    if code == 0 and output.strip() and "_" in output:
        parts = output.strip().split("_")
        if len(parts) == 2:
            free_bytes = int(parts[0]) * int(parts[1])
            disk = f"{free_bytes // (1024**3)}G"
        else:
            disk = "unknown"
    else:
        disk = "unknown"

    print(f"  CPU cores:    {cores}")
    print(f"  RAM:          {ram}")
    print(f"  Free disk:    {disk}")

    if cores != "unknown" and int(cores) < 4:
        print(f"  [WARN] LFS recommends at least 4 cores (have {cores})")
        warnings += 1

    # --- Summary ---
    print(f"\n{'=' * 72}")
    print(f"  RESULTS: {passed} passed, {failed} failed, {warnings} warnings")

    if failed > 0:
        print(f"\n  Host system does NOT meet LFS 13.0 requirements.")
        print(f"  Fix the failures above before attempting a build.")
        print(f"{'=' * 72}\n")
        return 1
    elif warnings > 0:
        print(f"\n  Host system meets requirements with warnings.")
        print(f"  Build should succeed but is outside tested configuration.")
        print(f"{'=' * 72}\n")
        return 0
    else:
        print(f"\n  Host system meets all LFS 13.0 requirements.")
        print(f"  Ready to build InterGenOS.")
        print(f"{'=' * 72}\n")
        return 0


if __name__ == "__main__":
    sys.exit(main())
```

---

**End of Part 3. Continue to Part 4 for kernel configuration files.**
