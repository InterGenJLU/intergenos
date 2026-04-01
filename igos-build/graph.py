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
