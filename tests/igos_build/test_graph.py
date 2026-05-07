"""Unit tests for igos-build/graph.py.

Covers DependencyGraph construction, dependency resolution (strict + non-strict),
topological sort with tier priority, cycle detection (single + multiple), and
the build_graph convenience function.

Closes §1 B5 (HIGH) test-coverage gap on graph.py.
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "igos-build"))

from parser import Dependencies, Package, Source  # noqa: E402
from graph import (  # noqa: E402
    CycleError,
    DependencyGraph,
    MissingDependencyError,
    build_graph,
    _find_all_cycles,
)


def _pkg(name, tier="core", build_deps=None, host_deps=None, runtime_deps=None,
         pass_number=None, version="1.0.0"):
    """Construct a minimal Package fixture for graph tests.

    The graph module only reads name, tier, dependencies, pass_number, template_path.
    All other fields are stub-valid."""
    deps = Dependencies(
        build=build_deps or [],
        host=host_deps or [],
        runtime=runtime_deps or [],
    )
    return Package(
        name=name,
        version=version,
        release=1,
        description=f"test fixture {name}",
        license="GPL-3.0-or-later",
        source=[Source(url=f"https://example.com/{name}.tar.gz", sha256="0" * 64)],
        dependencies=deps,
        build_style="custom",
        tier=tier,
        pass_number=pass_number,
        template_path=Path(f"/fake/{name}/package.yml"),
    )


class TestDependencyGraphAddPackage(unittest.TestCase):
    """Coverage of DependencyGraph.add_package."""

    def test_add_single_package(self):
        graph = DependencyGraph()
        pkg = _pkg("glibc", tier="core")
        graph.add_package(pkg)
        self.assertIn("glibc", graph.packages)
        self.assertIs(graph.packages["glibc"], pkg)

    def test_add_duplicate_raises_value_error(self):
        graph = DependencyGraph()
        graph.add_package(_pkg("glibc", tier="core"))
        with self.assertRaises(ValueError) as ctx:
            graph.add_package(_pkg("glibc", tier="core"))
        self.assertIn("duplicate package 'glibc'", str(ctx.exception))


class TestDependencyGraphResolve(unittest.TestCase):
    """Coverage of DependencyGraph.resolve in strict + non-strict modes."""

    def test_resolve_strict_with_missing_dep_raises(self):
        graph = DependencyGraph()
        graph.add_package(_pkg("foo", build_deps=["missing-bar"]))
        with self.assertRaises(MissingDependencyError) as ctx:
            graph.resolve(strict=True)
        self.assertEqual(ctx.exception.package, "foo")
        self.assertEqual(ctx.exception.missing, "missing-bar")

    def test_resolve_non_strict_skips_missing(self):
        graph = DependencyGraph()
        graph.add_package(_pkg("foo", build_deps=["missing-bar"]))
        graph.resolve(strict=False)
        # foo has no resolved deps because missing-bar was skipped
        self.assertEqual(graph.depends_on.get("foo", set()), set())

    def test_resolve_combines_build_host_runtime_deps(self):
        graph = DependencyGraph()
        graph.add_package(_pkg("a"))
        graph.add_package(_pkg("b"))
        graph.add_package(_pkg("c"))
        graph.add_package(_pkg(
            "main",
            build_deps=["a"],
            host_deps=["b"],
            runtime_deps=["c"],
        ))
        graph.resolve(strict=True)
        self.assertEqual(graph.depends_on["main"], {"a", "b", "c"})

    def test_resolve_populates_required_by_inverse(self):
        graph = DependencyGraph()
        graph.add_package(_pkg("base"))
        graph.add_package(_pkg("dependent", build_deps=["base"]))
        graph.resolve(strict=True)
        self.assertEqual(graph.required_by["base"], {"dependent"})


class TestDependencyGraphBuildOrder(unittest.TestCase):
    """Coverage of DependencyGraph.build_order — Kahn's topological sort."""

    def test_simple_linear_chain(self):
        graph = DependencyGraph()
        graph.add_package(_pkg("a"))
        graph.add_package(_pkg("b", build_deps=["a"]))
        graph.add_package(_pkg("c", build_deps=["b"]))
        graph.resolve()
        order = graph.build_order()
        names = [p.name for p in order]
        self.assertEqual(names, ["a", "b", "c"])

    def test_no_dependencies_alphabetical(self):
        graph = DependencyGraph()
        for name in ["zulu", "alpha", "mike"]:
            graph.add_package(_pkg(name))
        graph.resolve()
        order = graph.build_order()
        names = [p.name for p in order]
        # Same tier, no deps — alphabetical within tier
        self.assertEqual(names, ["alpha", "mike", "zulu"])

    def test_tier_priority_ordering(self):
        """toolchain < core < base < desktop < ai < extra"""
        graph = DependencyGraph()
        graph.add_package(_pkg("a-extra", tier="extra"))
        graph.add_package(_pkg("b-toolchain", tier="toolchain"))
        graph.add_package(_pkg("c-core", tier="core"))
        graph.add_package(_pkg("d-desktop", tier="desktop"))
        graph.resolve()
        order = graph.build_order()
        names = [p.name for p in order]
        # Despite alphabetical "a-extra" first, tier priority puts toolchain first
        self.assertEqual(names, ["b-toolchain", "c-core", "d-desktop", "a-extra"])

    def test_pass_number_ordering(self):
        """gcc-pass1 builds before gcc-pass2 even with same tier + alphabetical tie."""
        graph = DependencyGraph()
        graph.add_package(_pkg("gcc", tier="toolchain", pass_number=2))
        graph.add_package(_pkg("gcc-prep", tier="toolchain", pass_number=1))
        graph.resolve()
        order = graph.build_order()
        names = [p.name for p in order]
        self.assertEqual(names, ["gcc-prep", "gcc"])

    def test_diamond_dependency_resolves(self):
        """A -> B, A -> C, B -> D, C -> D produces a valid linear order."""
        graph = DependencyGraph()
        graph.add_package(_pkg("a"))
        graph.add_package(_pkg("b", build_deps=["a"]))
        graph.add_package(_pkg("c", build_deps=["a"]))
        graph.add_package(_pkg("d", build_deps=["b", "c"]))
        graph.resolve()
        order = graph.build_order()
        names = [p.name for p in order]
        # a must precede b, c; both must precede d
        self.assertEqual(names[0], "a")
        self.assertEqual(names[-1], "d")
        self.assertIn(names[1], {"b", "c"})
        self.assertIn(names[2], {"b", "c"})


class TestCycleDetection(unittest.TestCase):
    """Coverage of cycle detection + reporting."""

    def test_single_two_node_cycle(self):
        graph = DependencyGraph()
        graph.add_package(_pkg("a", build_deps=["b"]))
        graph.add_package(_pkg("b", build_deps=["a"]))
        graph.resolve()
        with self.assertRaises(CycleError) as ctx:
            graph.build_order()
        self.assertEqual(len(ctx.exception.cycles), 1)
        # Cycle should include both a and b. Drop the repeated-at-end marker.
        cycle_nodes = set(ctx.exception.cycles[0][:-1])
        self.assertEqual(cycle_nodes, {"a", "b"})

    def test_three_node_cycle(self):
        graph = DependencyGraph()
        graph.add_package(_pkg("a", build_deps=["b"]))
        graph.add_package(_pkg("b", build_deps=["c"]))
        graph.add_package(_pkg("c", build_deps=["a"]))
        graph.resolve()
        with self.assertRaises(CycleError) as ctx:
            graph.build_order()
        # First cycle contains all 3 nodes
        first = ctx.exception.cycles[0]
        self.assertEqual(set(first[:-1]), {"a", "b", "c"})

    def test_cycle_message_includes_arrow_chain(self):
        graph = DependencyGraph()
        graph.add_package(_pkg("a", build_deps=["b"]))
        graph.add_package(_pkg("b", build_deps=["a"]))
        graph.resolve()
        with self.assertRaises(CycleError) as ctx:
            graph.build_order()
        self.assertIn("->", str(ctx.exception))

    def test_legacy_cycle_attribute_preserved(self):
        """CycleError.cycle (singular) must still work for legacy callers."""
        graph = DependencyGraph()
        graph.add_package(_pkg("a", build_deps=["b"]))
        graph.add_package(_pkg("b", build_deps=["a"]))
        graph.resolve()
        with self.assertRaises(CycleError) as ctx:
            graph.build_order()
        # Legacy: .cycle returns the first cycle
        self.assertEqual(ctx.exception.cycle, ctx.exception.cycles[0])


class TestFindAllCycles(unittest.TestCase):
    """Coverage of _find_all_cycles helper — multi-cycle reporting + dedup."""

    def test_dedupes_rotated_cycles(self):
        depends_on = {"a": {"b"}, "b": {"a"}}
        cycles = _find_all_cycles({"a", "b"}, depends_on)
        # One cycle, even though DFS from a finds [a,b,a] and from b finds [b,a,b]
        self.assertEqual(len(cycles), 1)

    def test_finds_two_disjoint_cycles(self):
        # Cycle 1: a -> b -> a; Cycle 2: c -> d -> c
        depends_on = {
            "a": {"b"}, "b": {"a"},
            "c": {"d"}, "d": {"c"},
        }
        cycles = _find_all_cycles({"a", "b", "c", "d"}, depends_on)
        self.assertEqual(len(cycles), 2)

    def test_three_node_cycle_returns_one_cycle(self):
        """A->B->C->A returns one cycle regardless of DFS start node."""
        depends_on = {"a": {"b"}, "b": {"c"}, "c": {"a"}}
        cycles = _find_all_cycles({"a", "b", "c"}, depends_on)
        # Single cycle (rotated forms dedup via cycle_key)
        self.assertEqual(len(cycles), 1)
        # All three nodes appear in the cycle
        self.assertEqual(set(cycles[0][:-1]), {"a", "b", "c"})


class TestBuildGraphConvenience(unittest.TestCase):
    """Coverage of build_graph convenience function."""

    def test_build_graph_returns_resolved_graph(self):
        packages = [
            _pkg("a"),
            _pkg("b", build_deps=["a"]),
        ]
        graph = build_graph(packages, strict=True)
        order = graph.build_order()
        self.assertEqual([p.name for p in order], ["a", "b"])

    def test_build_graph_strict_raises_on_missing(self):
        packages = [_pkg("a", build_deps=["missing"])]
        with self.assertRaises(MissingDependencyError):
            build_graph(packages, strict=True)

    def test_build_graph_non_strict_skips_missing(self):
        packages = [_pkg("a", build_deps=["missing"])]
        graph = build_graph(packages, strict=False)
        # Should produce a graph with one package, no deps resolved
        self.assertIn("a", graph.packages)


if __name__ == "__main__":
    unittest.main()
