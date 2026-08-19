# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for source-aware change detection (igos-build/content_hash.py) and the
release auto-bump (scripts/bump-changed-releases.py).

Covers the bug these close: a first-party source-only edit used to NOT flip the
skip-built fingerprint, so a targeted build silently shipped the stale binary.
"""
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "igos-build"))

from content_hash import (  # noqa: E402
    source_content_hash, template_hash, content_fingerprint,
)


class _Src:
    def __init__(self, url="", sha256=None, generated=False, filename=None):
        self.url = url
        self.sha256 = sha256
        self.generated = generated
        self.filename = filename


class _Pkg:
    """Duck-typed stand-in for parser.Package (content_hash is duck-typed)."""
    def __init__(self, template_path, source=None, source_tree=None):
        self.template_path = template_path
        self.source = source or []
        self.source_tree = source_tree or []


def _mk_pkg(root: Path, tier="core", name="foo", build_sh="echo hi\n",
            yml="release: 1\n", source=None, source_tree=None) -> _Pkg:
    d = root / "packages" / tier / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "package.yml").write_text(yml)
    if build_sh is not None:
        (d / "build.sh").write_text(build_sh)
    return _Pkg(d / "package.yml", source=source, source_tree=source_tree)


class TestBackwardCompat(unittest.TestCase):
    def test_no_extra_source_matches_legacy_scheme(self):
        """A sha-pinned package (no out-of-recipe source) must hash EXACTLY as
        the old sha256(package.yml + build.sh)[:16] — so existing manifests
        still match and the ~700 upstream packages don't rebuild."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            yml, bsh = "release: 1\n", "configure && make\n"
            pkg = _mk_pkg(root, yml=yml, build_sh=bsh,
                          source=[_Src(url="http://x/p.tar.gz", sha256="ab" * 32)])
            legacy = hashlib.sha256((yml + bsh).encode()).hexdigest()[:16]
            self.assertEqual(template_hash(pkg, None), legacy)
            self.assertEqual(source_content_hash(pkg, None), "")

    def test_no_template_path_returns_empty(self):
        self.assertEqual(template_hash(_Pkg(None), None), "")


class TestSourceTree(unittest.TestCase):
    def test_external_source_tree_change_flips_hash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ext = root / "intergen"
            ext.mkdir()
            (ext / "router.py").write_text("v1\n")
            pkg = _mk_pkg(root, tier="ai", name="intergen", source_tree=["intergen"])
            h1 = template_hash(pkg, None)
            sc1 = source_content_hash(pkg, None)
            self.assertNotEqual(sc1, "")            # source IS contributing
            (ext / "router.py").write_text("v2\n")  # edit the external source
            self.assertNotEqual(template_hash(pkg, None), h1)
            self.assertNotEqual(source_content_hash(pkg, None), sc1)

    def test_new_file_in_source_tree_flips_hash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ext = root / "pkm"
            ext.mkdir()
            (ext / "cli.py").write_text("a\n")
            pkg = _mk_pkg(root, name="pkm", source_tree=["pkm"])
            h1 = template_hash(pkg, None)
            (ext / "repo.py").write_text("b\n")     # add a file
            self.assertNotEqual(template_hash(pkg, None), h1)


class TestEphemeralChurnIgnored(unittest.TestCase):
    """The no-drift guarantee: build/test/editor churn must NOT change the hash,
    so the host auto-bump (dirty dev tree) and the build (rsync'd copy) agree
    (review finding on e72aa5f8)."""

    def _churn(self, d: Path):
        (d / "__pycache__").mkdir(exist_ok=True)
        (d / "__pycache__" / "m.cpython-313.pyc").write_text("x")
        (d / ".pytest_cache").mkdir(exist_ok=True)
        (d / ".pytest_cache" / "CACHEDIR.TAG").write_text("x")
        (d / ".mypy_cache").mkdir(exist_ok=True)
        (d / ".mypy_cache" / "cache.json").write_text("x")
        (d / "thing.egg-info").mkdir(exist_ok=True)
        (d / "thing.egg-info" / "PKG-INFO").write_text("x")
        (d / ".coverage").write_text("x")
        (d / ".coverage.host.1234").write_text("x")
        (d / "mod.pyc").write_text("x")
        (d / ".router.py.swp").write_text("x")
        (d / "router.py~").write_text("x")
        (d / ".DS_Store").write_text("x")

    def test_churn_in_source_tree_does_not_flip_hash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ext = root / "intergen"
            ext.mkdir()
            (ext / "router.py").write_text("real\n")
            pkg = _mk_pkg(root, tier="ai", name="intergen", source_tree=["intergen"])
            h1 = template_hash(pkg, None)
            self._churn(ext)
            self.assertEqual(template_hash(pkg, None), h1)

    def test_churn_in_package_dir_does_not_flip_hash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkg = _mk_pkg(root, name="pkm", source=[])
            (pkg.template_path.parent / "pkm.1").write_text("MAN\n")
            h1 = template_hash(pkg, None)
            self._churn(pkg.template_path.parent)
            self.assertEqual(template_hash(pkg, None), h1)

    def test_generated_demand_bank_does_not_flip_hash(self):
        """The demand-bank merge OUTPUTS (demand_corpus/bank.jsonl +
        bank.report.json) are generated + gitignored — a dev tree that ran the
        --demand-bank battery carries them, a clean checkout does not. Hashing
        them recorded a baseline no clean state reproduces (poisoned-baseline
        class). They must be excluded so the dirty-dev-tree hash == clean-tree
        hash. The COMMITTED half-files beside them still count."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            corpus = root / "intergen" / "tests" / "demand_corpus"
            corpus.mkdir(parents=True)
            # committed source-of-truth half-files (MUST be hashed)
            (corpus / "demand_distribution.jsonl").write_text('{"a":1}\n')
            (corpus / "surface_flex.jsonl").write_text('{"b":2}\n')
            pkg = _mk_pkg(root, tier="ai", name="intergen", source_tree=["intergen"])
            h1 = template_hash(pkg, None)
            # generated merge outputs land beside them (per --demand-bank run)
            (corpus / "bank.jsonl").write_text('{"merged":"lots"}\n' * 100)
            (corpus / "bank.report.json").write_text('{"count":1321}\n')
            self.assertEqual(template_hash(pkg, None), h1,
                             "generated demand-bank outputs poisoned the hash")
            # a real edit to a COMMITTED half-file still flips it
            (corpus / "demand_distribution.jsonl").write_text('{"a":2}\n')
            self.assertNotEqual(template_hash(pkg, None), h1)

    def test_bank_basename_only_excluded_under_demand_corpus(self):
        """The exclusion is (parent-dir, basename)-scoped — a same-named file
        NOT under demand_corpus/ is a legitimate source file and stays hashed."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ext = root / "intergen"
            ext.mkdir()
            (ext / "router.py").write_text("real\n")
            pkg = _mk_pkg(root, tier="ai", name="intergen", source_tree=["intergen"])
            h1 = template_hash(pkg, None)
            (ext / "bank.jsonl").write_text("a legit committed file\n")  # NOT demand_corpus/
            self.assertNotEqual(template_hash(pkg, None), h1,
                                "a bank.jsonl outside demand_corpus/ must still count")

    def test_ephemeral_named_ancestor_does_not_collapse_hash(self):
        """A repo checked out UNDER a dir whose name is in the ephemeral set
        (.cache/.vscode/.tox/...) must NOT collapse the tree hash to empty —
        ephemerality is judged on the path RELATIVE to the hashed tree, not on
        absolute ancestors. Otherwise the no-drift guarantee inverts into
        silently never-detecting a source change → shipping stale (the exact hole
        this module closes). (WC review of 6848bc17.)"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / ".cache" / "checkout"   # ancestor literally ".cache"
            ext = root / "intergen"
            ext.mkdir(parents=True)
            (ext / "router.py").write_text("real source\n")
            pkg = _mk_pkg(root, tier="ai", name="intergen", source_tree=["intergen"])
            sc = source_content_hash(pkg, None)
            self.assertNotEqual(sc, "")          # must NOT collapse to empty
            h1 = template_hash(pkg, None)
            (ext / "router.py").write_text("changed\n")  # a real edit still flips
            self.assertNotEqual(template_hash(pkg, None), h1)


class TestBuildAffectingRecipeFields(unittest.TestCase):
    """content_fingerprint folds build-affecting package.yml fields
    (configure_flags/patches) so a recipe-flag change advances the auto-bump
    release (review finding D) — while staying backward-compatible for the
    packages that lack those fields."""

    def test_configure_flags_change_flips_fingerprint(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            yml1 = "release: 1\nconfigure_flags:\n  - \"--enable-x\"\n"
            pkg = _mk_pkg(root, name="foo", source=[], yml=yml1)
            fp1 = content_fingerprint(pkg, None)
            pkg.template_path.write_text(
                "release: 1\nconfigure_flags:\n  - \"--enable-x\"\n  - \"--enable-y\"\n")
            self.assertNotEqual(content_fingerprint(pkg, None), fp1)

    def test_patches_change_flips_fingerprint(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkg = _mk_pkg(root, name="foo", source=[],
                          yml="release: 1\npatches:\n  - file: a.patch\n")
            fp1 = content_fingerprint(pkg, None)
            pkg.template_path.write_text(
                "release: 1\npatches:\n  - file: a.patch\n  - file: b.patch\n")
            self.assertNotEqual(content_fingerprint(pkg, None), fp1)

    def test_no_recipe_fields_is_backward_compatible(self):
        """A package WITHOUT build-affecting fields must fingerprint EXACTLY as
        the pre-change scheme (sha256(build.sh + source)) — so the existing
        first-party content_hash baselines do not spuriously re-bump."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bsh = "configure && make\n"
            pkg = _mk_pkg(root, name="foo", source=[], build_sh=bsh,
                          yml="release: 1\ndescription: a thing\ndependencies:\n  build: []\n")
            # The legacy formula was sha256(build.sh + source_content_hash); with
            # an own-dir source:[] package and only build.sh present, source content
            # = the own-dir (just build.sh is skipped) → empty, so legacy = build.sh.
            legacy = hashlib.sha256(b"\0buildsh\0" + bsh.encode()).hexdigest()[:16]
            self.assertEqual(content_fingerprint(pkg, None), legacy)

    def test_non_build_affecting_field_change_does_not_flip(self):
        """description/dependencies are NOT folded (present on existing packages
        → folding would spuriously re-baseline). Changing them must not move the
        fingerprint."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkg = _mk_pkg(root, name="foo", source=[], build_sh="make\n",
                          yml="release: 1\ndescription: v1\n")
            fp1 = content_fingerprint(pkg, None)
            pkg.template_path.write_text("release: 1\ndescription: TOTALLY DIFFERENT\n")
            self.assertEqual(content_fingerprint(pkg, None), fp1)


class TestPackageDirSiblings(unittest.TestCase):
    def test_sibling_data_file_change_flips_hash(self):
        """A source:[] package's own-dir file (man page, hook) is a build input
        and must be tracked."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkg = _mk_pkg(root, name="pkm", source=[])
            (pkg.template_path.parent / "pkm.1").write_text("MAN v1\n")
            h1 = template_hash(pkg, None)
            (pkg.template_path.parent / "pkm.1").write_text("MAN v2\n")
            self.assertNotEqual(template_hash(pkg, None), h1)

    def test_package_yml_excluded_from_source_content(self):
        """Editing package.yml must NOT change source_content_hash (so the
        auto-bump can't re-trigger on its own bump)."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkg = _mk_pkg(root, name="pkm", source=[])
            (pkg.template_path.parent / "data.txt").write_text("x\n")
            sc1 = source_content_hash(pkg, None)
            pkg.template_path.write_text("release: 2\n")  # bump-like edit
            self.assertEqual(source_content_hash(pkg, None), sc1)


class TestGeneratedTarball(unittest.TestCase):
    def test_generated_tarball_bytes_change_flips_hash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sources = root / "sources"
            sources.mkdir()
            (sources / "forge-1.0.tar.xz").write_bytes(b"TARBALL-v1")
            pkg = _mk_pkg(root, tier="desktop", name="forge",
                          source=[_Src(url="file:///forge-1.0.tar.xz", generated=True)])
            h1 = template_hash(pkg, sources)
            self.assertNotEqual(source_content_hash(pkg, sources), "")
            (sources / "forge-1.0.tar.xz").write_bytes(b"TARBALL-v2")
            self.assertNotEqual(template_hash(pkg, sources), h1)


class TestGeneratedWithDeclaredInputs(unittest.TestCase):
    """Item-8 fingerprint design: a `generated: true` package that declares
    `source_tree:` (its canonical inputs — asset files + generator script)
    fingerprints THOSE; the output tarball's bytes are deliberately ignored,
    so a regeneration-context difference (umask-inherited modes, staged bump
    metadata) can never phantom-bump a release."""

    def _pkg(self, root: Path):
        assets = root / "assets" / "widget-theme"
        assets.mkdir(parents=True, exist_ok=True)
        (assets / "style.css").write_text("body { v: 1 }\n")
        gen = root / "scripts"
        gen.mkdir(exist_ok=True)
        (gen / "build-widget-tarball.sh").write_text("#!/bin/sh\n# v1\n")
        pkg = _mk_pkg(root, tier="desktop", name="widget-theme",
                      source=[_Src(url="file:///widget-theme-1.0.tar.xz", generated=True)],
                      source_tree=["assets/widget-theme",
                                   "scripts/build-widget-tarball.sh"])
        return pkg, assets, gen

    def test_tarball_byte_drift_does_not_move_fingerprint(self):
        """THE phantom-bump killer: regenerated-tarball byte drift with zero
        content change must not move the fingerprint (ledger item 8b — 7
        asset packages phantom-bumped on 2026-07-05)."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sources = root / "sources"
            sources.mkdir()
            (sources / "widget-theme-1.0.tar.xz").write_bytes(b"CONTEXT-A-BYTES")
            pkg, _, _ = self._pkg(root)
            fp1 = content_fingerprint(pkg, sources)
            th1 = template_hash(pkg, sources)
            (sources / "widget-theme-1.0.tar.xz").write_bytes(b"CONTEXT-B-BYTES")
            self.assertEqual(content_fingerprint(pkg, sources), fp1)
            self.assertEqual(template_hash(pkg, sources), th1)

    def test_asset_input_edit_moves_fingerprint(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sources = root / "sources"
            sources.mkdir()
            pkg, assets, _ = self._pkg(root)
            fp1 = content_fingerprint(pkg, sources)
            (assets / "style.css").write_text("body { v: 2 }\n")
            self.assertNotEqual(content_fingerprint(pkg, sources), fp1)

    def test_generator_script_edit_moves_fingerprint(self):
        """The generator script is a declared canonical input — editing it
        changes what the tarball would contain, so the release must advance."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sources = root / "sources"
            sources.mkdir()
            pkg, _, gen = self._pkg(root)
            fp1 = content_fingerprint(pkg, sources)
            (gen / "build-widget-tarball.sh").write_text("#!/bin/sh\n# v2\n")
            self.assertNotEqual(content_fingerprint(pkg, sources), fp1)

    def test_fingerprint_computable_without_staged_tarball(self):
        """Inputs-declared packages never read the tarball, so the fingerprint
        (and thus --check) works on a bare tree with nothing staged."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sources = root / "sources"
            sources.mkdir()  # empty — no tarball staged
            pkg, _, _ = self._pkg(root)
            self.assertNotEqual(source_content_hash(pkg, sources), "")
            self.assertNotEqual(content_fingerprint(pkg, sources), "")


class TestContentFingerprint(unittest.TestCase):
    def test_excludes_package_yml_includes_build_sh(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkg = _mk_pkg(root, name="pkm", source=[], build_sh="step1\n")
            fp1 = content_fingerprint(pkg, None)
            # editing package.yml does NOT change the fingerprint
            pkg.template_path.write_text("release: 9\n")
            self.assertEqual(content_fingerprint(pkg, None), fp1)
            # editing build.sh DOES (it changes how the package is built)
            (pkg.template_path.parent / "build.sh").write_text("step2\n")
            self.assertNotEqual(content_fingerprint(pkg, None), fp1)


class TestFeatureMatrixFold(unittest.TestCase):
    """A feature-matrix.json edit must flip BOTH hashes.

    The matrix pins the recipe's resolved build surface (mesa/lib32-mesa read
    it in configure). It was folded into content_fingerprint only, so a
    matrix-only edit advanced the release but stayed invisible to
    --skip-built — the exact silent-stale-ship class the skip-built key
    exists to prevent.
    """

    def test_matrix_edit_flips_template_hash(self):
        with tempfile.TemporaryDirectory() as td:
            pkg = _mk_pkg(Path(td), name="mesa", source=[])
            matrix = pkg.template_path.parent / "feature-matrix.json"
            matrix.write_text('{"vulkan": true}')
            h1 = template_hash(pkg, None)
            matrix.write_text('{"vulkan": false}')
            self.assertNotEqual(template_hash(pkg, None), h1)

    def test_matrix_edit_flips_content_fingerprint(self):
        with tempfile.TemporaryDirectory() as td:
            pkg = _mk_pkg(Path(td), name="mesa", source=[])
            matrix = pkg.template_path.parent / "feature-matrix.json"
            matrix.write_text('{"vulkan": true}')
            fp1 = content_fingerprint(pkg, None)
            matrix.write_text('{"vulkan": false}')
            self.assertNotEqual(content_fingerprint(pkg, None), fp1)

    def test_matrix_addition_flips_template_hash(self):
        with tempfile.TemporaryDirectory() as td:
            pkg = _mk_pkg(Path(td), name="mesa", source=[])
            h1 = template_hash(pkg, None)
            (pkg.template_path.parent / "feature-matrix.json").write_text("{}")
            self.assertNotEqual(template_hash(pkg, None), h1)

    def test_no_matrix_keeps_legacy_scheme(self):
        # Packages without a matrix keep the exact pre-fold hash — no
        # spurious corpus-wide rebuild.
        with tempfile.TemporaryDirectory() as td:
            pkg = _mk_pkg(Path(td), name="bzip2", source=[],
                          yml="release: 1\n", build_sh="echo hi\n")
            legacy = hashlib.sha256(
                pkg.template_path.read_bytes()
                + (pkg.template_path.parent / "build.sh").read_bytes()
            ).hexdigest()[:16]
            self.assertEqual(template_hash(pkg, None), legacy)


class TestAutoBumpTool(unittest.TestCase):
    """End-to-end against scripts/bump-changed-releases.py."""

    TOOL = REPO_ROOT / "scripts" / "bump-changed-releases.py"

    def _mk_repo(self, td):
        root = Path(td)
        pdir = root / "packages" / "core" / "widget"
        pdir.mkdir(parents=True)
        (pdir / "package.yml").write_text(textwrap.dedent("""\
            name: widget
            version: "1.0"
            release: 3
            description: test widget
            license: GPL-3.0-or-later
            source: []
            build_style: custom
        """))
        (pdir / "build.sh").write_text("do_install() { :; }\n")
        (pdir / "data.conf").write_text("setting=1\n")
        (root / "sources").mkdir()
        return root, pdir

    def _run(self, root, *extra):
        return subprocess.run(
            [sys.executable, str(self.TOOL),
             "--packages-dir", str(root / "packages"),
             "--sources-dir", str(root / "sources"), *extra],
            capture_output=True, text=True)

    def test_establish_then_bump_then_check(self):
        with tempfile.TemporaryDirectory() as td:
            root, pdir = self._mk_repo(td)
            yml = pdir / "package.yml"

            # 1) first apply ESTABLISHES baseline, no bump
            r = self._run(root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("content_hash:", yml.read_text())
            self.assertIn("release: 3", yml.read_text())

            # 2) --check passes when in sync
            r = self._run(root, "--check")
            self.assertEqual(r.returncode, 0, r.stderr)

            # 3) change source content -> --check FAILS (drift)
            (pdir / "data.conf").write_text("setting=2\n")
            r = self._run(root, "--check")
            self.assertEqual(r.returncode, 1, "check should fail on unbumped drift")

            # 4) apply BUMPS release 3 -> 4 and re-records
            r = self._run(root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("release: 4", yml.read_text())

            # 5) --check passes again
            r = self._run(root, "--check")
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_missing_generated_tarball_errors(self):
        with tempfile.TemporaryDirectory() as td:
            root, pdir = self._mk_repo(td)
            # declare a generated source with no tarball staged
            txt = (pdir / "package.yml").read_text().replace(
                "source: []",
                'source:\n- url: file:///widget-1.0.tar.xz\n  generated: true')
            (pdir / "package.yml").write_text(txt)
            r = self._run(root)
            self.assertEqual(r.returncode, 2, "missing generated tarball must error")
            self.assertIn("not in", r.stderr)

    def test_inputs_declared_needs_no_staged_tarball(self):
        """A generated-source package declaring source_tree fingerprints its
        inputs — the tool must run clean on a bare tree (no tarball staged)."""
        with tempfile.TemporaryDirectory() as td:
            root, pdir = self._mk_repo(td)
            (root / "assets" / "widget").mkdir(parents=True)
            (root / "assets" / "widget" / "d.css").write_text("v1\n")
            txt = (pdir / "package.yml").read_text().replace(
                "source: []",
                'source:\n- url: file:///widget-1.0.tar.xz\n  generated: true\n'
                'source_tree:\n  - assets/widget')
            (pdir / "package.yml").write_text(txt)
            r = self._run(root)  # establishes baseline; no tarball anywhere
            self.assertEqual(r.returncode, 0, r.stderr)
            r = self._run(root, "--check")
            self.assertEqual(r.returncode, 0, r.stderr)
            # and the declared input still drives drift detection end-to-end
            (root / "assets" / "widget" / "d.css").write_text("v2\n")
            r = self._run(root, "--check")
            self.assertEqual(r.returncode, 1, "input edit must be flagged as drift")

    def test_missing_source_tree_path_errors(self):
        """Fail-closed: a typo'd/absent source_tree declaration hashes NOTHING
        (iter_tree_files no-ops on a missing root) — the tool must refuse
        rather than record a baseline blind to the real inputs."""
        with tempfile.TemporaryDirectory() as td:
            root, pdir = self._mk_repo(td)
            txt = (pdir / "package.yml").read_text().replace(
                "source: []",
                'source: []\nsource_tree:\n  - assets/no-such-dir')
            (pdir / "package.yml").write_text(txt)
            r = self._run(root)
            self.assertEqual(r.returncode, 2, "missing source_tree path must error")
            self.assertIn("no-such-dir", r.stderr)


class TestCoverageGate(unittest.TestCase):
    """The source-tree coverage gate's external-read detection — locking in the
    false-positive cases debugged on the real tree (bat's $out_dir/assets,
    brave's own-dir keyring) and the true positives (intergen, gnome-shell)."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        path = REPO_ROOT / "scripts" / "check-source-tree-coverage.py"
        spec = importlib.util.spec_from_file_location("cov_gate", path)
        cls.gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.gate)

    def test_external_repo_read_flagged(self):
        for line in [
            "    cp -a /mnt/intergenos/intergen/*.py $DESTDIR/",
            '    local d="${IGOS_SOURCE_ROOT:-/mnt/intergenos}/assets/intergen-mark"',
            "    cp $IGOS_SOURCE_ROOT/pkm/cli.py .",
        ]:
            self.assertTrue(self.gate.external_reads(line), f"should flag: {line}")

    def test_upstream_build_dir_assets_not_flagged(self):
        # bat: assets/ INSIDE the extracted upstream build dir — not our repo.
        self.assertEqual(self.gate.external_reads('install -Dm644 "$out_dir/assets/manual/bat.1" x'), set())

    def test_own_package_dir_not_flagged(self):
        # brave: keyring under the package's OWN dir (packages/...) — arm (b).
        line = '    cp "${IGOS_SOURCE_ROOT:-/mnt/intergenos}/packages/extra/brave/assets/brave-keyring.asc" x'
        self.assertEqual(self.gate.external_reads(line), set())

    def test_comment_line_not_flagged(self):
        self.assertEqual(self.gate.external_reads("    # see /mnt/intergenos/assets/foo/bar"), set())

    def test_bare_relative_not_flagged(self):
        # No recognized repo-root prefix -> not a repo read (wouldn't resolve).
        self.assertEqual(self.gate.external_reads("cp assets/foo/bar ."), set())

    def test_repo_root_file_read_flagged(self):
        # A package can also read a FILE at the repository root — the shipped
        # source-availability statement is installed from /mnt/intergenos/
        # SOURCES.md. That read is exactly the class this gate exists for (an
        # edit to the authored file must move the reader's fingerprint), but
        # the top-level-directory list could never see it, because the path
        # has no directory component at all.
        for line in [
            '    install -Dm644 /mnt/intergenos/SOURCES.md "$DESTDIR/x"',
            '    local f="${IGOS_SOURCE_ROOT:-/mnt/intergenos}/SOURCES.md"',
        ]:
            self.assertEqual(self.gate.external_reads(line), {"SOURCES.md"},
                             f"should flag: {line}")

    def test_repo_root_bare_directory_not_flagged_by_the_file_arm(self):
        # A bare top-level DIRECTORY name is classified by the external-tops
        # list, not by the root-file arm — so a directory that is deliberately
        # not in that list (build output, for instance) stays unflagged.
        self.assertEqual(self.gate.external_reads("cp -r /mnt/intergenos/build ."), set())
    def test_shared_build_input_roots_flagged(self):
        """A recipe reading a shared build input from config/, scripts/, docs/
        or igos-build/ is the same stale-ship class as an intergen/ read: the
        file decides the built bytes (or IS the payload), so an edit to it must
        move the reading package's fingerprint."""
        for line, expected in [
            ("source /mnt/intergenos/scripts/lib32-env.sh", "scripts/lib32-env.sh"),
            ("    --cross-file /mnt/intergenos/config/lib32/lib32-cross.ini \\",
             "config/lib32/lib32-cross.ini"),
            ("    python3 /mnt/intergenos/igos-build/mesa_feature_matrix.py \\",
             "igos-build/mesa_feature_matrix.py"),
            ('    cp "${IGOS_SOURCE_ROOT:-/mnt/intergenos}/docs/signing-key.asc" .',
             "docs/signing-key.asc"),
        ]:
            self.assertIn(expected, self.gate.external_reads(line), f"should flag: {line}")

    def test_external_tops_covers_every_chroot_staged_source_root(self):
        """The roots list must name every first-party source dir the build
        stages into the chroot.

        Anything staged there is reachable by a build.sh at build time, so a
        staged root absent from the list is a read this gate cannot see. The
        gate's own comment states the list MUST stay in sync with that staging
        and admits the gate cannot enforce it on itself; this test is that
        enforcement. Derived from build-intergenos.sh rather than restated, so
        a NEW staged root fails here instead of silently escaping the gate.
        """
        build_sh = (REPO_ROOT / "scripts" / "build-intergenos.sh").read_text()
        body = re.search(r"^sync_chroot_scripts\(\)\s*\{(.*?)^\}", build_sh, re.M | re.S)
        self.assertIsNotNone(body, "sync_chroot_scripts() not found in build-intergenos.sh")
        staged = set(re.findall(r"/mnt/intergenos/([A-Za-z0-9._-]+)/", body.group(1)))
        self.assertTrue(
            staged, "parsed zero staged roots — the parse is wrong, not the tree "
                    "(a zero here would pass this test while checking nothing)")
        # packages/ is the one deliberate exclusion: a recipe reading its OWN
        # dir is hashed by content_hash arm (b) and never needs a declaration.
        missing = sorted(r for r in staged - {"packages"}
                         if r not in self.gate._EXTERNAL_TOPS)
        self.assertEqual(
            missing, [],
            f"build-intergenos.sh stages {missing} into the chroot but "
            f"check-source-tree-coverage.py's _EXTERNAL_TOPS does not list "
            f"them, so a recipe reading from there escapes the gate")

    def test_coverage_prefix_match(self):
        self.assertTrue(self.gate._covered("intergen/web/server.py", ["intergen"]))
        self.assertTrue(self.gate._covered("assets/x/y.png", ["assets/x/y.png"]))
        self.assertFalse(self.gate._covered("assets/x", ["assets/x/y.png"]))  # parent not covered by child
        self.assertFalse(self.gate._covered("intergen/web", []))


if __name__ == "__main__":
    unittest.main()
