# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Dependency graph and topological sort for igos-build.

Builds a directed acyclic graph from package dependencies,
detects cycles, and produces a valid build order via topological sort.
"""

from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

try:
    from .parser import Package
except ImportError:
    # Fallback for top-level import (e.g., test runners that insert igos-build
    # on sys.path rather than importing as a package).
    from parser import Package

try:
    from . import _trace
    _TRACE_AVAILABLE = True
except ImportError:
    _trace = None
    _TRACE_AVAILABLE = False


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class CycleError(Exception):
    """Raised when one or more dependency cycles are detected."""

    def __init__(self, cycles: list[list[str]]):
        self.cycles = cycles
        # Preserve the legacy .cycle attribute (first cycle) so any existing
        # consumer reading `.cycle` keeps working without surprise.
        self.cycle = cycles[0] if cycles else []
        if len(cycles) == 1:
            msg = f"dependency cycle detected: {' -> '.join(cycles[0])}"
        else:
            lines = [f"  {i+1}. {' -> '.join(c)}" for i, c in enumerate(cycles)]
            msg = (
                f"{len(cycles)} dependency cycles detected:\n"
                + "\n".join(lines)
            )
        super().__init__(msg)


class MissingDependencyError(Exception):
    """Raised when a package depends on something not in the graph."""

    def __init__(self, package: str, missing: str, hint: str = ""):
        self.package = package
        self.missing = missing
        super().__init__(
            f"'{package}' depends on '{missing}', which is not a known package{hint}")


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

        Build/host and runtime dependencies live in DIFFERENT namespaces:

        - build/host deps order the BUILD, which knows only recipe names
          (package.yml `name:` fields) — validated strictly against those.
        - runtime deps are USER-side contracts: tracker.py emits them
          verbatim into .PKGINFO `depend=` lines (H-004), so the mirror
          index and pkm resolve them in the SHIPPED namespace. The ch8
          dual-name twins (`run_package "gcc-core" "gcc"` in
          chroot-build-ch8.sh) ship under a different name than their
          recipe — declared per-recipe via `ships_as:` — so runtime deps
          validate against recipe names UNION ship names, and a ship name
          resolves its edge to the recipe that ships it.

        (F25, 2026-07-21: runtime deps written in recipe names shipped
        unresolvable on user systems; written in ship names they were
        rejected here. The namespace split is the fix for both halves.)

        All resolved dependency edges must be built before the dependent
        package.

        Args:
            strict: If True, raise MissingDependencyError for unknown deps.
                    If False, silently skip unknown deps (useful during
                    incremental template development).
        """
        import sys

        # Ship-name provider map. Two recipes shipping the same name is a
        # real double-provider in the user namespace — always an error,
        # strict or not (the archive set could not hold both).
        ship_providers: dict[str, str] = {}
        for name, pkg in self.packages.items():
            sa = getattr(pkg, "ships_as", None)
            if sa:
                if sa in ship_providers:
                    raise ValueError(
                        f"duplicate ships_as '{sa}': declared by both "
                        f"'{ship_providers[sa]}' and '{name}'")
                ship_providers[sa] = name

        for name, pkg in self.packages.items():
            resolved: set[str] = set()

            for dep in set(pkg.dependencies.build) | set(pkg.dependencies.host):
                if dep not in self.packages:
                    if strict:
                        hint = ""
                        if dep in ship_providers:
                            hint = (
                                f" (build/host deps use RECIPE names; '{dep}' is the "
                                f"ship name of recipe '{ship_providers[dep]}' — "
                                f"did you mean that?)")
                        raise MissingDependencyError(name, dep, hint)
                    continue
                resolved.add(dep)

            for dep in set(pkg.dependencies.runtime):
                if dep in ship_providers:
                    target = ship_providers[dep]
                    if target == name:
                        raise ValueError(
                            f"'{name}' runtime-depends on its own ship name '{dep}'")
                    if dep in self.packages:
                        # e.g. runtime dep 'glibc': toolchain/glibc is a recipe
                        # node, but the SHIPPED provider is glibc-core — the
                        # shipped package is the semantic target of a runtime
                        # contract. Noted loudly so the shadowing is auditable.
                        print(
                            f"note: runtime dep '{dep}' of {name}: shipped provider "
                            f"'{target}' wins over same-named recipe '{dep}'",
                            file=sys.stderr)
                    resolved.add(target)
                elif dep in self.packages:
                    resolved.add(dep)
                elif strict:
                    raise MissingDependencyError(name, dep)

            for dep in resolved:
                self.depends_on[name].add(dep)
                self.required_by[dep].add(name)

                # Informational: flag cross-tier dependencies
                dep_pkg = self.packages[dep]
                if pkg.tier != dep_pkg.tier:
                    print(f"note: cross-tier dependency: {name} ({pkg.tier}) -> {dep} ({dep_pkg.tier})",
                          file=sys.stderr)

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
        tier_priority = {"toolchain": 0, "core": 1, "base": 2, "desktop": 3, "ai": 4, "extra": 5}

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

        # If we didn't process every package, there's at least one cycle.
        # Report ALL distinct cycles so the user can fix them in one pass
        # rather than discovering them iteratively.
        if len(order) != len(self.packages):
            remaining = set(self.packages.keys()) - set(order)
            cycles = _find_all_cycles(remaining, self.depends_on)
            raise CycleError(cycles)

        return [self.packages[name] for name in order]

    def print_order(self, order: list[Package] | None = None):
        """Print the build order in a human-readable format."""
        if order is None:
            order = self.build_order()

        print(f"==> Build order ({len(order)} packages)\n")
        for i, pkg in enumerate(order, 1):
            deps = self.depends_on.get(pkg.name, set())
            dep_str = f"  (after: {', '.join(sorted(deps))})" if deps else ""
            pass_str = f" pass {pkg.pass_number}" if pkg.pass_number else ""
            print(f"  {i:3d}. [{pkg.tier}] {pkg.name} {pkg.version}{pass_str}{dep_str}")


# ---------------------------------------------------------------------------
# Cycle detection helper
# ---------------------------------------------------------------------------

def _find_all_cycles(nodes: set[str], depends_on: dict[str, set[str]]) -> list[list[str]]:
    """Find and return ALL distinct cycles among the remaining unprocessed nodes.

    Two cycles are considered the same if their node-set and rotation match,
    so [A,B,C,A] and [B,C,A,B] deduplicate to a single cycle. Each returned
    cycle is a list where the first node is repeated at the end
    (e.g., ["A", "B", "C", "A"]).
    """
    found: list[list[str]] = []
    seen_keys: set[tuple[str, ...]] = set()

    def cycle_key(cycle: list[str]) -> tuple[str, ...]:
        # Canonical form: rotate so the lexicographically smallest node
        # is first (ignoring the repeated-at-end node).
        core = cycle[:-1]  # drop the repeated last
        if not core:
            return tuple(cycle)
        i = core.index(min(core))
        rotated = core[i:] + core[:i]
        return tuple(rotated)

    for start in nodes:
        path: list[str] = []
        path_set: set[str] = set()

        def dfs(node: str) -> None:
            if node in path_set:
                cycle_start = path.index(node)
                cycle = path[cycle_start:] + [node]
                key = cycle_key(cycle)
                if key not in seen_keys:
                    seen_keys.add(key)
                    found.append(cycle)
                return

            path.append(node)
            path_set.add(node)

            for dep in depends_on.get(node, set()):
                if dep in nodes:
                    dfs(dep)

            path.pop()
            path_set.discard(node)

        dfs(start)

    # Fallback: if the topological sort failed but we couldn't walk to a
    # cycle (shouldn't happen, but safety net), report the stuck nodes.
    if not found:
        return [list(nodes)[:5] + ["..."]]

    return found


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
    if _TRACE_AVAILABLE:
        try:
            order = graph.build_order()
            _trace.trace_event(
                "graph_resolve",
                package_count=len(packages),
                ordered_count=len(order),
                cycles=0,            # resolve() raised CycleError on cycle
                missing_deps=0,      # resolve(strict=True) raised on missing
                strict=strict,
            )
        except Exception:
            pass
    return graph
