#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""A recipe's shipped sibling files must reach both hashes, and no first-party
package may sit without a baseline.

WHY THIS FILE EXISTS. A package could pin an upstream tarball and still ship
files we wrote — install hooks, helper programs, whole files/ trees, apparmor
profiles, units, patches. source_content_hash folds a package's own directory
only when the package is NOT sha-pinned, so for a pinned one those files
reached neither the release fingerprint nor the skip-built key. Measured
2026-08-05: 68 packages were in that position. Planting a probe line in one
package's shipped hook script left the release-drift check reporting "in
sync"; planting the same probe in that package's own build.sh made the check
report a DIFFERENT package, because the installer-hooks fingerprint couples to
every recipe and that was the only thing that moved. A maintainer editing a
hook was told either nothing or the wrong name.

The tests come in three kinds:
  * MECHANISM — a sibling edit moves the fingerprint, and moves the skip-built
    key too. The second half matters as much as the first: a fold that reached
    the release gate but not the rebuild key would advance a release while the
    build is skipped, shipping the previous bytes under a new release number.
  * NO-CHURN — an unpinned package's regular files must NOT be double-folded,
    and a bare recipe must keep its exact legacy hash. Coverage that
    re-baselines the whole corpus for no gain is its own kind of wrong.
  * CORPUS — against the real tree: every trackable package records a
    baseline, and the enumeration this fold depends on matches what git
    actually tracks, so "tracked" is checked rather than assumed.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "igos-build"))

from content_hash import (  # noqa: E402
    _is_ephemeral, content_fingerprint, sibling_shipped_bytes, template_hash,
)
from parser import parse_template, discover_templates, TemplateError  # noqa: E402


class _Src:
    def __init__(self, sha256=None):
        self.url = "https://example.invalid/x-1.0.tar.gz"
        self.sha256 = sha256
        self.generated = False
        self.filename = None


class _Pkg:
    def __init__(self, template_path, source=None):
        self.template_path = template_path
        self.source = source or []
        self.source_tree = []


def _recipe(root: Path, pinned: bool) -> _Pkg:
    d = root / "packages" / "extra" / "demo"
    d.mkdir(parents=True)
    (d / "package.yml").write_text("name: demo\nversion: '1.0'\nrelease: 1\n")
    (d / "build.sh").write_text("do_install() { :; }\n")
    return _Pkg(d / "package.yml", source=[_Src(sha256="ab" * 32)] if pinned else [])


class PinnedRecipeSiblings(unittest.TestCase):
    """The gap that was open: a pinned package shipping our own files."""

    def test_a_shipped_hook_edit_moves_the_release_fingerprint(self):
        with tempfile.TemporaryDirectory() as td:
            pkg = _recipe(Path(td), pinned=True)
            hook = pkg.template_path.parent / "hooks" / "post-install.sh"
            hook.parent.mkdir()
            hook.write_text("echo one\n")
            before = content_fingerprint(pkg, None)
            hook.write_text("echo two\n")
            self.assertNotEqual(
                content_fingerprint(pkg, None), before,
                "editing a shipped hook must advance the release fingerprint")

    def test_a_shipped_hook_edit_moves_the_rebuild_key_too(self):
        # If it did not, the release would advance while --skip-built skipped
        # the build: a new release number over the previous bytes.
        with tempfile.TemporaryDirectory() as td:
            pkg = _recipe(Path(td), pinned=True)
            hook = pkg.template_path.parent / "hooks" / "post-install.sh"
            hook.parent.mkdir()
            hook.write_text("echo one\n")
            before = template_hash(pkg, None)
            hook.write_text("echo two\n")
            self.assertNotEqual(
                template_hash(pkg, None), before,
                "editing a shipped hook must also force a rebuild")

    def test_a_new_directory_nobody_predicted_is_covered(self):
        # The set is positional, not a list of known directory names — which
        # is the whole point, since a named list goes stale the same way.
        with tempfile.TemporaryDirectory() as td:
            pkg = _recipe(Path(td), pinned=True)
            before = content_fingerprint(pkg, None)
            odd = pkg.template_path.parent / "a-directory-invented-later"
            odd.mkdir()
            (odd / "shipped.conf").write_text("x\n")
            self.assertNotEqual(content_fingerprint(pkg, None), before)

    def test_a_retargeted_symlink_is_a_change(self):
        # Hashed as target TEXT, never followed: a shipped compatibility link
        # can point at a directory or at nothing, and retargeting it changes
        # what lands on an installed system.
        with tempfile.TemporaryDirectory() as td:
            pkg = _recipe(Path(td), pinned=True)
            link = pkg.template_path.parent / "files" / "bin"
            link.parent.mkdir()
            link.symlink_to("usr/bin")
            before = content_fingerprint(pkg, None)
            link.unlink()
            link.symlink_to("usr/sbin")
            self.assertNotEqual(
                content_fingerprint(pkg, None), before,
                "retargeting a shipped symlink must move the fingerprint")

    def test_the_old_definition_is_still_reproducible(self):
        # The re-baseline mode depends on this: it proves a package's drift is
        # explained by the definition change alone before absorbing it.
        with tempfile.TemporaryDirectory() as td:
            pkg = _recipe(Path(td), pinned=True)
            hook = pkg.template_path.parent / "hooks" / "post-install.sh"
            hook.parent.mkdir()
            hook.write_text("echo one\n")
            old_a = content_fingerprint(pkg, None, include_siblings=False)
            hook.write_text("echo two\n")
            old_b = content_fingerprint(pkg, None, include_siblings=False)
            self.assertEqual(
                old_a, old_b,
                "the old definition must be blind to siblings — that blindness "
                "is what the re-baseline proof measures against")


class UnpinnedRecipeIsNotDoubleFolded(unittest.TestCase):
    """Coverage that churns the whole corpus for no gain is its own defect."""

    def test_regular_files_are_left_to_the_existing_fold(self):
        with tempfile.TemporaryDirectory() as td:
            pkg = _recipe(Path(td), pinned=False)
            (pkg.template_path.parent / "data.txt").write_text("x\n")
            self.assertEqual(
                sibling_shipped_bytes(pkg), b"",
                "an unpinned package's regular files are already folded by "
                "source_content_hash; folding them again would move every "
                "existing baseline and rebuild the first-party set for nothing")

    def test_symlinks_are_still_covered_for_an_unpinned_package(self):
        # The existing fold selects on is_file(), so a link to a directory is
        # invisible to it. That hole is closed for both kinds of package.
        with tempfile.TemporaryDirectory() as td:
            pkg = _recipe(Path(td), pinned=False)
            link = pkg.template_path.parent / "files" / "lib"
            link.parent.mkdir()
            link.symlink_to("usr/lib")
            self.assertNotEqual(sibling_shipped_bytes(pkg), b"")

    def test_a_bare_recipe_keeps_its_exact_legacy_hash(self):
        with tempfile.TemporaryDirectory() as td:
            pkg = _recipe(Path(td), pinned=True)
            self.assertEqual(
                sibling_shipped_bytes(pkg), b"",
                "a recipe that is only package.yml + build.sh must contribute "
                "nothing new, so no unaffected package re-baselines")


class RebaselineModeIsNotABypass(unittest.TestCase):
    """--rebaseline exists for a change to the fingerprint DEFINITION. The one
    way it could hide something is by absorbing a package whose content really
    changed, so it proves what it absorbs and refuses anything else."""

    def _tree(self, td: Path, hook_text: str, recorded: str):
        d = td / "packages" / "extra" / "demo"
        d.mkdir(parents=True)
        (d / "package.yml").write_text(
            "name: demo\nversion: '1.0'\nrelease: 1\n"
            f"content_hash: {recorded}\n"
            "description: d\nlicense: MIT\ntier: extra\nbuild_style: custom\n"
            "source:\n- url: https://example.invalid/demo-1.0.tar.gz\n"
            f"  sha256: {'ab' * 32}\n"
            "dependencies:\n  build: []\n  host: []\n  runtime: []\n")
        (d / "build.sh").write_text("do_install() { :; }\n")
        (d / "hooks").mkdir()
        (d / "hooks" / "post-install.sh").write_text(hook_text)
        return d

    def _run(self, td: Path, *args):
        return subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "bump-changed-releases.py"),
             "--packages-dir", str(td / "packages"),
             "--sources-dir", str(td / "sources"), *args],
            capture_output=True, text=True, errors="replace")

    def test_it_refuses_a_package_that_really_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            (td / "sources").mkdir()
            # A baseline that matches NEITHER definition: this package's own
            # content moved, which --rebaseline must not quietly absorb.
            self._tree(td, "echo changed\n", recorded="0" * 16)
            r = self._run(td, "--rebaseline")
            self.assertNotEqual(r.returncode, 0,
                                f"stdout={r.stdout}\nstderr={r.stderr}")
            # Assert on what the refusal MEANS, not on one prose fragment: the
            # run must announce a refusal, say which comparison produced it, and
            # name the correction. This assertion used to pin the exact sentence
            # and broke when the message was reworded while the behaviour was
            # unchanged, which is a test failing for the wrong reason.
            out = r.stdout + r.stderr
            self.assertIn("refused", out)
            self.assertIn("previous fingerprint definition", out)
            self.assertIn("run without --rebaseline", out)


class AgainstTheRealTree(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.pkgs = []
        for yml in discover_templates(REPO_ROOT / "packages"):
            try:
                cls.pkgs.append(parse_template(yml))
            except TemplateError:
                continue

    def test_the_population_is_real(self):
        self.assertGreater(len(self.pkgs), 1000,
                           "a small population would make the checks vacuous")

    def test_every_trackable_package_records_a_baseline(self):
        # "No baseline recorded" must never again mean "silently exempt".
        missing = []
        for pkg in self.pkgs:
            pinned = any(getattr(s, "sha256", None) for s in (pkg.source or []))
            trackable = (not pinned) or bool(getattr(pkg, "source_tree", None)) \
                or bool(sibling_shipped_bytes(pkg))
            if not trackable:
                continue
            text = pkg.template_path.read_text(encoding="utf-8", errors="replace")
            if "\ncontent_hash:" not in text and not text.startswith("content_hash:"):
                missing.append(pkg.name)
        self.assertEqual(
            sorted(missing), [],
            "these packages carry first-party content but record no "
            f"content_hash baseline, so nothing watches their bytes: {missing}")

    def test_the_hashed_file_set_matches_what_git_tracks(self):
        # The fold enumerates the filesystem, because the chroot recipe copy
        # carries no .git and `git ls-files` cannot run builder-side. That
        # makes "these are the tracked files" an assumption — so it is checked
        # here instead of trusted, on the one tree where git IS available.
        proc = subprocess.run(
            ["git", "ls-files", "packages/"],
            cwd=REPO_ROOT, capture_output=True, text=True, errors="replace")
        if proc.returncode != 0:
            self.skipTest("not a git checkout")
        tracked = {p for p in proc.stdout.split() if p}
        self.assertGreater(len(tracked), 1000)
        # Apply the SAME ephemeral filter the fold applies. Without it this
        # compares against raw disk, and the .pyc files the test run itself
        # creates look like untracked shipped content — a false alarm about
        # the very thing being checked.
        on_disk = set()
        for p in (REPO_ROOT / "packages").rglob("*"):
            if not (p.is_symlink() or p.is_file()):
                continue
            rel = p.relative_to(REPO_ROOT)
            if _is_ephemeral(rel):
                continue
            on_disk.add(str(rel))
        untracked = sorted(on_disk - tracked)
        self.assertEqual(
            untracked, [],
            "files under packages/ that git does not track would be folded "
            f"into a fingerprint the builder cannot reproduce: {untracked[:20]}")


if __name__ == "__main__":
    unittest.main()
