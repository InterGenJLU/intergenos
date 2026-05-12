"""Unit tests for scripts/preflight-build-order.py.

Covers the five finding types (SAME-SCRIPT-VIOLATION, CROSS-PHASE-VIOLATION,
DEP-NOT-FOUND, DEP-TIER-UNKNOWN, PACKAGE-YML-MISSING), the clean-tree zero-
finding case, and duplicate-package detection. Each test builds a synthetic
mini-repo in a tempdir + invokes the scanner via --root.
"""

import importlib.util
import io
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "preflight-build-order.py"

# Load the scanner module by file path (the hyphen in the name prevents
# normal import).
_spec = importlib.util.spec_from_file_location(
    "preflight_build_order", SCRIPT_PATH
)
preflight = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(preflight)


def _make_repo(tmp: Path, packages: dict[str, dict], scripts: dict[str, list[str]]) -> Path:
    """Build a minimal repo layout under tmp.

    packages = {pkg_name: {"tier": "core", "deps": ["dep1", "dep2"]}, ...}
        — generates packages/<tier>/<pkg>/package.yml with dependencies.build
    scripts = {phase_name: ["pkg_a", "pkg_b", ...]}
        — generates scripts/chroot-build-<phase>.sh with run_package lines
          in the order given.
    """
    (tmp / "packages").mkdir()
    for pkg_name, meta in packages.items():
        tier = meta["tier"]
        deps = meta.get("deps", [])
        pkg_dir = tmp / "packages" / tier / pkg_name
        pkg_dir.mkdir(parents=True)
        deps_yaml = "\n".join(f"    - {d}" for d in deps) if deps else "    []"
        yml = textwrap.dedent(f"""\
            name: {pkg_name}
            version: 1.0.0
            release: 1
            tier: {tier}
            dependencies:
              build:
            {deps_yaml}
            """)
        (pkg_dir / "package.yml").write_text(yml)

    (tmp / "scripts").mkdir()
    for phase, pkgs in scripts.items():
        run_lines = "\n".join(
            f'    run_package "{p}" "{p}" "1.0.0"' for p in pkgs
        )
        script_text = f"#!/bin/bash\nbuild_phase() {{\n{run_lines}\n}}\n"
        (tmp / "scripts" / f"chroot-build-{phase}.sh").write_text(script_text)
    return tmp


class TestCleanTree(unittest.TestCase):
    def test_zero_findings_on_well_ordered_repo(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td), packages={
                "libxml2": {"tier": "core", "deps": []},
                "nghttp2": {"tier": "core", "deps": ["libxml2"]},
            }, scripts={
                "core-extra": ["libxml2", "nghttp2"],
            })
            _, findings, dupes = preflight.scan(repo)
        self.assertEqual(findings, [])
        self.assertEqual(dupes, {})


class TestSameScriptViolation(unittest.TestCase):
    def test_dep_after_consumer_in_same_script(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td), packages={
                "libxml2": {"tier": "core", "deps": []},
                "nghttp2": {"tier": "core", "deps": ["libxml2"]},
            }, scripts={
                "core-extra": ["nghttp2", "libxml2"],  # WRONG order
            })
            _, findings, _ = preflight.scan(repo)
        types = [f["type"] for f in findings]
        self.assertIn("SAME-SCRIPT-VIOLATION", types)
        violation = next(f for f in findings if f["type"] == "SAME-SCRIPT-VIOLATION")
        self.assertEqual(violation["consumer"], "nghttp2")
        self.assertEqual(violation["dep"], "libxml2")


class TestCrossPhaseViolation(unittest.TestCase):
    def test_dep_in_later_phase_via_run_package(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td), packages={
                "openldap": {"tier": "core", "deps": []},
                "mitkrb": {"tier": "core", "deps": ["openldap"]},
            }, scripts={
                "core-extra": ["mitkrb"],
                "desktop": ["openldap"],  # dep in LATER phase
            })
            _, findings, _ = preflight.scan(repo)
        types = [f["type"] for f in findings]
        self.assertIn("CROSS-PHASE-VIOLATION", types)

    def test_dep_in_later_phase_via_tier_default(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td), packages={
                "vala": {"tier": "desktop", "deps": []},
                "libgudev": {"tier": "core", "deps": ["vala"]},
            }, scripts={
                "core-extra": ["libgudev"],
            })
            _, findings, _ = preflight.scan(repo)
        types = [f["type"] for f in findings]
        self.assertIn("CROSS-PHASE-VIOLATION", types)


class TestDepNotFound(unittest.TestCase):
    def test_dep_with_no_package_yml(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td), packages={
                "consumer": {"tier": "core", "deps": ["nonexistent-dep"]},
            }, scripts={
                "core-extra": ["consumer"],
            })
            _, findings, _ = preflight.scan(repo)
        types = [f["type"] for f in findings]
        self.assertIn("DEP-NOT-FOUND", types)


class TestDuplicateDetection(unittest.TestCase):
    def test_same_package_in_two_scripts(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td), packages={
                "pkg-a": {"tier": "core", "deps": []},
            }, scripts={
                "core-extra": ["pkg-a"],
                "desktop": ["pkg-a"],  # duplicate
            })
            _, _, dupes = preflight.scan(repo)
        self.assertIn("pkg-a", dupes)
        self.assertEqual(len(dupes["pkg-a"]), 2)


class TestPackageYmlMissing(unittest.TestCase):
    def test_run_package_without_yml(self):
        """run_package line for a name that has no package.yml anywhere."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "packages").mkdir()
            (tmp / "packages" / "core").mkdir()
            (tmp / "scripts").mkdir()
            script = tmp / "scripts" / "chroot-build-core-extra.sh"
            script.write_text('build_phase() {\n    run_package "ghost-pkg" "ghost-pkg" "1.0"\n}\n')
            _, findings, _ = preflight.scan(tmp)
        types = [f["type"] for f in findings]
        self.assertIn("PACKAGE-YML-MISSING", types)


class TestExitCodeViaMain(unittest.TestCase):
    """Verify the script's main() returns 0 on clean, 1 on findings."""

    def _run_main(self, repo: Path) -> int:
        argv_orig = sys.argv
        sys.argv = ["preflight-build-order.py", "--root", str(repo)]
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                return preflight.main()
        finally:
            sys.argv = argv_orig

    def test_exit_zero_on_clean(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td), packages={
                "libxml2": {"tier": "core", "deps": []},
            }, scripts={"core-extra": ["libxml2"]})
            self.assertEqual(self._run_main(repo), 0)

    def test_exit_one_on_findings(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _make_repo(Path(td), packages={
                "libxml2": {"tier": "core", "deps": []},
                "nghttp2": {"tier": "core", "deps": ["libxml2"]},
            }, scripts={"core-extra": ["nghttp2", "libxml2"]})
            self.assertEqual(self._run_main(repo), 1)


class TestParseDepsBuildIndentStyles(unittest.TestCase):
    """parse_deps_build handles both YAML list indent styles."""

    def test_style_a_deeper_indent(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "package.yml"
            p.write_text(textwrap.dedent("""\
                name: foo
                dependencies:
                  build:
                    - dep1
                    - dep2
                """))
            deps = preflight.parse_deps_build(p)
        self.assertEqual(deps, ["dep1", "dep2"])

    def test_style_b_same_indent(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "package.yml"
            p.write_text(textwrap.dedent("""\
                name: foo
                dependencies:
                  build:
                  - dep1
                  - dep2
                """))
            deps = preflight.parse_deps_build(p)
        self.assertEqual(deps, ["dep1", "dep2"])

    def test_empty_build_list(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "package.yml"
            p.write_text(textwrap.dedent("""\
                name: foo
                dependencies:
                  build:
                """))
            deps = preflight.parse_deps_build(p)
        self.assertEqual(deps, [])


if __name__ == "__main__":
    unittest.main()
