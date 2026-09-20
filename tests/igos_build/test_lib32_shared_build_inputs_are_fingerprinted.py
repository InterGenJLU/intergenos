# SPDX-License-Identifier: GPL-3.0-or-later
"""The two 32-bit build inputs the build STYLE injects must reach the fingerprint.

A 32-bit package's build consumes two repository files it never names: the
autotools/make lanes prefix every phase command with ``source
scripts/lib32-env.sh`` and the meson lane passes
``config/lib32/lib32-cross.ini`` as the cross file. Between them they decide the
compilers, the target triplet, the pkg-config directory, the staging helpers and
the staged-payload assertion — they are build inputs in every sense.

Nineteen recipes name the profile in their own ``source_tree:`` and are covered.
The other twenty-four consume it through the style, name nothing, and were
covered by nothing: editing either file moved no fingerprint, so the release
auto-bump did not bump them and a targeted build skipped them under
``--skip-built``, leaving bytes built under the previous profile with nothing
saying so. A recipe cannot be expected to declare an input the style injects —
the declaration has to live where the elf class is known.

These wedges drive the real module: both hashes must move (the release gate and
the skip-built key must never disagree — a fold reaching one but not the other
advances a release while the build is skipped), 64-bit packages must not move at
all, a 32-bit package with no first-party content of its own must become
trackable by the auto-bump, and the paths folded here must be the same paths the
styles inject.
"""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "igos-build"))

from content_hash import (  # noqa: E402
    content_fingerprint,
    source_content_hash,
    template_hash,
)

PROFILE_REL = Path("scripts") / "lib32-env.sh"
CROSS_REL = Path("config") / "lib32" / "lib32-cross.ini"


class _Src:
    def __init__(self, url="", sha256=None, generated=False, filename=None):
        self.url = url
        self.sha256 = sha256
        self.generated = generated
        self.filename = filename


class _Pkg:
    """Duck-typed stand-in for parser.Package (content_hash is duck-typed)."""

    def __init__(self, template_path, source=None, source_tree=None,
                 elf_class=None):
        self.template_path = template_path
        self.source = source or []
        self.source_tree = source_tree or []
        self.elf_class = elf_class


def _mk_repo(root: Path, profile="export CC='gcc -m32'\n",
             cross="[binaries]\nc = 'gcc'\n") -> None:
    """A repository tree carrying the two shared 32-bit build inputs."""
    (root / PROFILE_REL).parent.mkdir(parents=True, exist_ok=True)
    (root / PROFILE_REL).write_text(profile)
    (root / CROSS_REL).parent.mkdir(parents=True, exist_ok=True)
    (root / CROSS_REL).write_text(cross)


def _mk_pkg(root: Path, tier="desktop", name="lib32-thing", elf_class="32",
            build_sh=None, yml="release: 1\n", source=None,
            source_tree=None) -> _Pkg:
    """A pinned upstream package with NO first-party content of its own — the
    shape of every style-driven 32-bit recipe in the tree (package.yml only)."""
    d = root / "packages" / tier / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "package.yml").write_text(yml)
    if build_sh is not None:
        (d / "build.sh").write_text(build_sh)
    if source is None:
        source = [_Src(url="https://example.invalid/thing-1.0.tar.xz",
                       sha256="0" * 64)]
    return _Pkg(d / "package.yml", source=source, source_tree=source_tree,
                elf_class=elf_class)


def _hashes(pkg):
    return template_hash(pkg, None), content_fingerprint(pkg, None)


class TestTheProfileReachesEveryThirtyTwoBitPackage(unittest.TestCase):
    def test_editing_the_profile_moves_both_hashes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _mk_repo(root)
            pkg = _mk_pkg(root)
            before = _hashes(pkg)
            (root / PROFILE_REL).write_text("export CC='gcc -m32 -U_TIME_BITS'\n")
            after = _hashes(pkg)
            self.assertNotEqual(after[0], before[0],
                                "the skip-built key must move, or a targeted "
                                "build skips a package whose profile changed")
            self.assertNotEqual(after[1], before[1],
                                "the release gate must move, or the rebuilt "
                                "package reaches no installed machine")

    def test_editing_the_cross_file_moves_both_hashes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _mk_repo(root)
            pkg = _mk_pkg(root)
            before = _hashes(pkg)
            (root / CROSS_REL).write_text("[binaries]\nc = 'clang'\n")
            after = _hashes(pkg)
            self.assertNotEqual(after[0], before[0])
            self.assertNotEqual(after[1], before[1])

    def test_the_two_hashes_move_together(self):
        """A fold that reaches the release gate but not the skip-built key
        advances a release while the build is skipped — the module's own stated
        failure. Neither file may move one without the other."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _mk_repo(root)
            pkg = _mk_pkg(root)
            for rel, new in ((PROFILE_REL, "export CC='cc -m32'\n"),
                             (CROSS_REL, "[binaries]\nc = 'cc'\n")):
                t_before, c_before = _hashes(pkg)
                (root / rel).write_text(new)
                t_after, c_after = _hashes(pkg)
                self.assertNotEqual(t_after, t_before, f"{rel}: skip-built key")
                self.assertNotEqual(c_after, c_before, f"{rel}: release gate")

    def test_a_package_carrying_its_own_content_still_moves(self):
        """The 19 recipes that DO declare the profile keep working: the fold is
        additive, not a replacement for their source_tree declaration."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _mk_repo(root)
            pkg = _mk_pkg(root, build_sh="make\n",
                          source_tree=[str(PROFILE_REL)])
            before = _hashes(pkg)
            (root / PROFILE_REL).write_text("export CC='gcc -m32 -Werror'\n")
            self.assertNotEqual(_hashes(pkg), before)


class TestSixtyFourBitPackagesAreUntouched(unittest.TestCase):
    def test_a_64_bit_package_does_not_move_on_either_edit(self):
        """The fold is keyed on the elf class. A 64-bit package neither sources
        the profile nor reads the cross file, and its fingerprint must not move
        — folding for everything would re-baseline the whole tree."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _mk_repo(root)
            pkg = _mk_pkg(root, tier="core", name="thing", elf_class=None)
            before = _hashes(pkg)
            (root / PROFILE_REL).write_text("export CC='gcc -m32 -Wall'\n")
            (root / CROSS_REL).write_text("[binaries]\nc = 'tcc'\n")
            self.assertEqual(_hashes(pkg), before)

    def test_an_explicit_64_elf_class_does_not_move(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _mk_repo(root)
            pkg = _mk_pkg(root, tier="core", name="thing64", elf_class="64")
            before = _hashes(pkg)
            (root / PROFILE_REL).write_text("export CC='gcc -m32 -Wall'\n")
            self.assertEqual(_hashes(pkg), before)


class TestTheAutoBumpTracksThem(unittest.TestCase):
    """The fold is worth nothing if the release tool never asks for the
    fingerprint. A style-driven 32-bit recipe pins an upstream tarball, declares
    no source_tree, ships no file of its own and declares no gpu_targets — it
    fell outside every trackability clause, exactly as the ROCm recipes did
    before the gpu_targets clause was added."""

    @staticmethod
    def _is_trackable(pkg):
        spec = importlib.util.spec_from_file_location(
            "bump_for_test", REPO_ROOT / "scripts" / "bump-changed-releases.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module._is_trackable(pkg)

    def test_a_style_driven_32_bit_package_is_trackable(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _mk_repo(root)
            pkg = _mk_pkg(root)
            self.assertTrue(self._is_trackable(pkg),
                            "a 32-bit package consumes two first-party build "
                            "inputs, so it carries first-party content")

    def test_a_pinned_64_bit_package_stays_untrackable(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _mk_repo(root)
            pkg = _mk_pkg(root, tier="core", name="thing", elf_class=None)
            self.assertFalse(self._is_trackable(pkg),
                             "the trackable set must not widen beyond the "
                             "32-bit lane")


class TestItRefusesRatherThanHashingNothing(unittest.TestCase):
    def test_a_missing_profile_raises(self):
        """An instrument that cannot see must refuse, not report zero: with the
        profile absent, silently folding nothing would return the pre-change
        digest and call a broken tree in sync."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _mk_repo(root)
            (root / PROFILE_REL).unlink()
            pkg = _mk_pkg(root)
            with self.assertRaises(FileNotFoundError):
                source_content_hash(pkg, None)

    def test_a_missing_cross_file_raises(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _mk_repo(root)
            (root / CROSS_REL).unlink()
            pkg = _mk_pkg(root)
            with self.assertRaises(FileNotFoundError):
                source_content_hash(pkg, None)

    def test_a_64_bit_package_does_not_care_that_they_are_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _mk_repo(root)
            (root / PROFILE_REL).unlink()
            (root / CROSS_REL).unlink()
            pkg = _mk_pkg(root, tier="core", name="thing", elf_class=None)
            source_content_hash(pkg, None)   # must not raise


class TestAgainstTheRealTree(unittest.TestCase):
    """The paths folded must be the paths the styles inject, and the population
    must not be empty — an instrument with nothing to measure certifies
    nothing."""

    @staticmethod
    def _lib32_ymls():
        return sorted(p for p in REPO_ROOT.glob("packages/*/*/package.yml")
                      if 'elf_class: "32"' in p.read_text())

    def test_the_two_inputs_exist_where_the_styles_name_them(self):
        self.assertTrue((REPO_ROOT / PROFILE_REL).is_file())
        self.assertTrue((REPO_ROOT / CROSS_REL).is_file())

    def test_the_folded_paths_are_the_paths_the_styles_inject(self):
        """content_hash.py is loaded standalone by the release tool, so it
        cannot import the styles module; the two relative paths are restated
        there and this is the guard that keeps the copies equal."""
        import content_hash as ch

        base = (REPO_ROOT / "igos-build" / "styles" / "base.py").read_text()
        self.assertIn(f'Path("config") / "lib32" / "lib32-cross.ini"', base)
        self.assertIn(f'Path("scripts") / "lib32-env.sh"', base)
        self.assertEqual(Path(*ch.LIB32_PROFILE_REL.parts), PROFILE_REL)
        self.assertEqual(Path(*ch.LIB32_CROSS_REL.parts), CROSS_REL)

    def test_the_tree_carries_32_bit_packages_to_measure(self):
        self.assertGreaterEqual(len(self._lib32_ymls()), 40)

    def test_every_32_bit_package_in_the_tree_is_trackable(self):
        """The measured gap: 26 of the 45 carried no trackability at all."""
        sys.path.insert(0, str(REPO_ROOT / "igos-build"))
        from parser import parse_template  # noqa: E402

        spec = importlib.util.spec_from_file_location(
            "bump_for_tree_test",
            REPO_ROOT / "scripts" / "bump-changed-releases.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        untracked = [str(p.relative_to(REPO_ROOT))
                     for p in self._lib32_ymls()
                     if not module._is_trackable(parse_template(p))]
        self.assertEqual(untracked, [],
                         "a 32-bit package the release tool never asks about "
                         "can ship bytes built under a changed profile")


if __name__ == "__main__":
    unittest.main()
