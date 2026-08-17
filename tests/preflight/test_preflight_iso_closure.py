# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""RED/GREEN tests for scripts/preflight-iso-closure.py.

Each test builds a synthetic mini-repo in a tempdir (packages/<tier>/<pkg>/
package.yml + a symlink to the REAL igos-build/ so the gate uses the actual
parser rule) and invokes the gate as a subprocess via --root, asserting the
exit code (0 clean / 1 HARD) and the emitted finding type.

Covers the four gate cases named in the dispatch plus a MIRROR→MIRROR GREEN
control:
  - violating edge (shipped → mirror-only)      -> RED, names the edge
  - dangling runtime-dep                         -> RED
  - non-boolean explicit iso_include ("false")   -> RED
  - clean tree                                    -> GREEN
  - mirror-only → mirror-only edge               -> GREEN (both evicted)
"""

import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "preflight-iso-closure.py"
REAL_IGOS_BUILD = REPO_ROOT / "igos-build"


def _pkg_yml(name: str, tier: str, *, runtime=None, iso_include_line=None,
             ships_as=None) -> str:
    """A minimal-but-complete template satisfying parser REQUIRED_FIELDS."""
    lines = [
        f"name: {name}",
        "version: 1.0.0",
        "release: 1",
        f"description: {name} test fixture",
        "license: GPL-3.0-or-later",
        "source: []",
        "build_style: custom",
        f"tier: {tier}",
    ]
    if ships_as is not None:
        lines.append(f"ships_as: {ships_as}")
    if iso_include_line is not None:
        lines.append(iso_include_line)
    if runtime:
        lines.append("dependencies:")
        lines.append("  runtime:")
        for d in runtime:
            lines.append(f"    - {d}")
    return "\n".join(lines) + "\n"


def _make_repo(tmp: Path, packages: dict[str, dict]) -> Path:
    """packages = {name: {"tier":..., "runtime":[...], "iso": "iso_include: false"}}"""
    (tmp / "packages").mkdir()
    for name, meta in packages.items():
        tier = meta["tier"]
        pkg_dir = tmp / "packages" / tier / name
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "package.yml").write_text(_pkg_yml(
            name, tier,
            runtime=meta.get("runtime"),
            iso_include_line=meta.get("iso"),
            ships_as=meta.get("ships_as"),
        ))
    # The gate imports igos-build.parser from --root; symlink the real one so
    # EFFECTIVE iso_include comes from build-time semantics, not a stub.
    (tmp / "igos-build").symlink_to(REAL_IGOS_BUILD)
    return tmp


def _run(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(repo), "--verbose"],
        capture_output=True, text=True,
    )


class TestIsoClosureGate(unittest.TestCase):
    def test_violating_edge_is_red_and_names_the_edge(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td), {
                # shipped app (core -> effective iso_include True)
                "mpv": {"tier": "core", "runtime": ["libvdpau"]},
                # mirror-only dep (extra -> effective iso_include False)
                "libvdpau": {"tier": "extra"},
            })
            r = _run(repo)
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("ISO-CLOSURE-VIOLATION", r.stdout)
            self.assertIn("mpv -> libvdpau", r.stdout)

    def test_dangling_runtime_dep_is_red(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td), {
                "mpv": {"tier": "core", "runtime": ["ghost-package"]},
            })
            r = _run(repo)
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("ISO-CLOSURE-DANGLING-DEP", r.stdout)
            self.assertIn("ghost-package", r.stdout)

    def test_nonboolean_iso_include_string_false_is_red(self):
        with tempfile.TemporaryDirectory() as td:
            # A quoted "false" is a Python str, which bool() coerces True —
            # the parser silently ships it; the gate must refuse it.
            repo = _make_repo(Path(td), {
                "sneaky": {"tier": "extra", "iso": 'iso_include: "false"'},
            })
            r = _run(repo)
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("ISO-CLOSURE-NONBOOLEAN", r.stdout)

    def test_clean_tree_is_green(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td), {
                "libfoo": {"tier": "core"},
                "app": {"tier": "core", "runtime": ["libfoo"]},
            })
            r = _run(repo)
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertIn("PASS", r.stdout)

    def test_mirror_to_mirror_edge_is_green(self):
        with tempfile.TemporaryDirectory() as td:
            # Both mirror-only (extra): the shipped-set closure is unaffected
            # because neither ships — no violation.
            repo = _make_repo(Path(td), {
                "extra-app": {"tier": "extra", "runtime": ["extra-lib"]},
                "extra-lib": {"tier": "extra"},
            })
            r = _run(repo)
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertIn("PASS", r.stdout)

    def test_explicit_iso_include_true_ships_and_can_violate(self):
        with tempfile.TemporaryDirectory() as td:
            # An extra-tier package force-shipped via explicit iso_include:true
            # that runtime-depends a mirror-only sibling is a violation — the
            # explicit override must be honored (proves effective, not tier).
            repo = _make_repo(Path(td), {
                "forced": {"tier": "extra", "iso": "iso_include: true",
                           "runtime": ["extra-lib"]},
                "extra-lib": {"tier": "extra"},
            })
            r = _run(repo)
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("ISO-CLOSURE-VIOLATION", r.stdout)
            self.assertIn("forced -> extra-lib", r.stdout)

    def test_runtime_dep_on_ship_name_is_green(self):
        with tempfile.TemporaryDirectory() as td:
            # F25 namespace wave: recipe gcc-core ships as gcc; a mirror-only
            # consumer's runtime dep on the SHIP name must resolve, not
            # false-flag as dangling.
            repo = _make_repo(Path(td), {
                "gcc-core": {"tier": "core", "ships_as": "gcc"},
                "nvidia": {"tier": "extra", "runtime": ["gcc"]},
            })
            r = _run(repo)
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertIn("PASS", r.stdout)

    def test_ship_name_edge_still_catches_closure_violation(self):
        with tempfile.TemporaryDirectory() as td:
            # A SHIPPED consumer depending (via ship name) on a mirror-only
            # provider must still be a violation — resolution through
            # ships_as must not weaken the closure check.
            repo = _make_repo(Path(td), {
                "tool-core": {"tier": "extra", "ships_as": "tool"},
                "app": {"tier": "core", "runtime": ["tool"]},
            })
            r = _run(repo)
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("ISO-CLOSURE-VIOLATION", r.stdout)
            self.assertIn("app -> tool", r.stdout)

    def test_runtime_dep_on_build_namespace_name_is_red(self):
        with tempfile.TemporaryDirectory() as td:
            # F25 gate (namespace check): recipe ncurses-core ships as
            # ncurses; a runtime dep naming the RECIPE (build-namespace)
            # name resolves in-tree but lands verbatim in .PKGINFO depend=
            # and fails on every user system. Must be RED and name the
            # shipped name to declare instead.
            repo = _make_repo(Path(td), {
                "ncurses-core": {"tier": "core", "ships_as": "ncurses"},
                "bash": {"tier": "core", "runtime": ["ncurses-core"]},
            })
            r = _run(repo)
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("ISO-CLOSURE-BUILD-NAME-DEP", r.stdout)
            self.assertIn("bash -> ncurses-core", r.stdout)
            self.assertIn("'ncurses'", r.stdout)

    def test_duplicate_ships_as_is_red(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td), {
                "a-core": {"tier": "core", "ships_as": "thing"},
                "b-core": {"tier": "core", "ships_as": "thing"},
            })
            r = _run(repo)
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("ISO-CLOSURE-DUPLICATE-SHIPS-AS", r.stdout)


if __name__ == "__main__":
    unittest.main()
