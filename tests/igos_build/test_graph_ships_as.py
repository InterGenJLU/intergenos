"""Unit tests for the ships_as ship-namespace resolution in graph.resolve().

F25 namespace wave (2026-07-21): runtime deps are user-side contracts emitted
verbatim into .PKGINFO, so they validate against recipe names UNION declared
ship names (the ch8 dual-name twins, e.g. recipe gcc-core ships as gcc);
build/host deps stay strictly recipe-namespace.
"""

import contextlib
import io
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "igos-build"))

from parser import Dependencies, Package, Source  # noqa: E402
from graph import (  # noqa: E402
    DependencyGraph,
    MissingDependencyError,
    build_graph,
)


def _pkg(name, tier="core", build_deps=None, host_deps=None, runtime_deps=None,
         ships_as=None, version="1.0.0"):
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
        ships_as=ships_as,
        template_path=Path(f"/fake/{name}/package.yml"),
    )


class TestRuntimeShipNameResolution(unittest.TestCase):

    def test_runtime_dep_on_ship_name_resolves_to_shipping_recipe(self):
        gcc_core = _pkg("gcc-core", ships_as="gcc")
        consumer = _pkg("ffmpeg-x", tier="extra", runtime_deps=["gcc"])
        graph = build_graph([gcc_core, consumer], strict=True)
        self.assertIn("gcc-core", graph.depends_on["ffmpeg-x"])
        self.assertIn("ffmpeg-x", graph.required_by["gcc-core"])

    def test_unknown_runtime_dep_still_raises_strict(self):
        consumer = _pkg("consumer", runtime_deps=["nonexistent"])
        with self.assertRaises(MissingDependencyError):
            build_graph([consumer], strict=True)

    def test_build_dep_on_ship_name_raises_with_namespace_hint(self):
        gcc_core = _pkg("gcc-core", ships_as="gcc")
        consumer = _pkg("consumer", build_deps=["gcc"])
        with self.assertRaises(MissingDependencyError) as ctx:
            build_graph([gcc_core, consumer], strict=True)
        self.assertIn("gcc-core", str(ctx.exception))
        self.assertIn("RECIPE names", str(ctx.exception))

    def test_build_dep_on_recipe_name_still_resolves(self):
        gcc_core = _pkg("gcc-core", ships_as="gcc")
        consumer = _pkg("consumer", build_deps=["gcc-core"])
        graph = build_graph([gcc_core, consumer], strict=True)
        self.assertIn("gcc-core", graph.depends_on["consumer"])

    def test_shipped_provider_shadows_same_named_recipe(self):
        # The glibc/ncurses shape as the tree carried it until 2026-08-25: a
        # toolchain recipe owns the bare name while the -core twin ships as
        # it. Those three were renamed to -tmp, so this fixture is now the
        # only place the shape exists — which is the point, because the
        # SHIPPED provider must still win the runtime edge, with a loud
        # stderr note, for any pair that turns up later.
        glibc_toolchain = _pkg("glibc", tier="toolchain")
        glibc_core = _pkg("glibc-core", ships_as="glibc")
        consumer = _pkg("nvidia-x", tier="extra", runtime_deps=["glibc"])
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            graph = build_graph(
                [glibc_toolchain, glibc_core, consumer], strict=True)
        self.assertIn("glibc-core", graph.depends_on["nvidia-x"])
        self.assertNotIn("glibc", graph.depends_on["nvidia-x"])
        self.assertIn("shipped provider", stderr.getvalue())

    def test_duplicate_ships_as_is_always_an_error(self):
        a = _pkg("a-core", ships_as="thing")
        b = _pkg("b-core", ships_as="thing")
        with self.assertRaises(ValueError) as ctx:
            build_graph([a, b], strict=False)  # non-strict must ALSO refuse
        self.assertIn("duplicate ships_as", str(ctx.exception))

    def test_self_runtime_dep_via_own_ship_name_is_an_error(self):
        weird = _pkg("gcc-core", ships_as="gcc", runtime_deps=["gcc"])
        with self.assertRaises(ValueError) as ctx:
            build_graph([weird], strict=True)
        self.assertIn("its own ship name", str(ctx.exception))

    def test_non_strict_skips_unknown_runtime_dep(self):
        consumer = _pkg("consumer", runtime_deps=["nonexistent"])
        graph = build_graph([consumer], strict=False)
        self.assertEqual(graph.depends_on.get("consumer", set()), set())

    def test_build_order_places_shipping_recipe_before_consumer(self):
        gcc_core = _pkg("gcc-core", ships_as="gcc")
        consumer = _pkg("consumer", tier="extra", runtime_deps=["gcc"])
        graph = build_graph([gcc_core, consumer], strict=True)
        order = [p.name for p in graph.build_order()]
        self.assertLess(order.index("gcc-core"), order.index("consumer"))


if __name__ == "__main__":
    unittest.main()
