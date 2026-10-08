"""The release-note chain gate: a bump must move the chain head with it.

Origin 2026-07-30: machine bumps carried the release NUMBER while the
hand-authored `# rNN:` chain head stayed behind (intergen 130 vs r109,
forge the same, an 87-package no-note class). The gate closes the class
going forward; these tests pin its edges — including the ones that must
NOT fire, so the legacy backlog can never block a push that doesn't touch
a release line.
"""

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "check_release_notes_test",
        REPO_ROOT / "scripts" / "check-release-notes.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestReleaseNoteChainGate(unittest.TestCase):
    def setUp(self):
        self.mod = _load()
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        self._git("init", "-q")
        self._git("config", "user.email", "test@test")
        self._git("config", "user.name", "test")
        self.yml = Path(self.repo) / "packages" / "core" / "demo"
        self.yml.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _git(self, *args):
        subprocess.run(["git", "-C", self.repo, *args],
                       check=True, capture_output=True, text=True)

    def _commit(self, release_line, msg="chore: change"):
        (self.yml / "package.yml").write_text(
            f"name: demo\n{release_line}\nversion: 1.0\n")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", msg)

    def _range(self):
        return self.mod.check_range(self.repo, "HEAD~1", "HEAD")

    # -- must fire ---------------------------------------------------------

    def test_bump_with_stale_head_label_fails(self):
        self._commit("release: 1  # r1: initial")
        self._commit("release: 2  # r1: initial")
        v = self._range()
        self.assertEqual(len(v), 1)
        self.assertIn("still reads r1", v[0])
        self.assertIn("1->2", v[0])

    def test_bump_with_no_comment_fails(self):
        self._commit("release: 1  # r1: initial")
        self._commit("release: 2")
        v = self._range()
        self.assertEqual(len(v), 1)
        self.assertIn("no `# r2:` note", v[0])

    def test_new_package_above_release_one_without_note_fails(self):
        self._commit("release: 1  # r1: unrelated", msg="chore: seed")
        other = Path(self.repo) / "packages" / "extra" / "newpkg"
        other.mkdir(parents=True)
        (other / "package.yml").write_text("name: newpkg\nrelease: 3\n")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "feat: add newpkg")
        self.assertEqual(len(self._range()), 1)

    # -- must NOT fire -----------------------------------------------------

    def test_bump_with_matching_head_passes(self):
        self._commit("release: 1  # r1: initial")
        self._commit("release: 2  # r2: the fix. r1: initial")
        self.assertEqual(self._range(), [])

    def test_untouched_legacy_mismatch_never_fires(self):
        # The 87-package backlog shape: head label far behind the release.
        # A commit that does not touch the release line must pass, or the
        # legacy state would block every unrelated push.
        self._commit("release: 130  # r109: old news")
        (Path(self.repo) / "unrelated.txt").write_text("unrelated change\n")
        self._git("add", "unrelated.txt")
        self._git("commit", "-q", "-m", "docs: unrelated")
        self.assertEqual(self._range(), [])

    def test_no_gate_override_skips(self):
        self._commit("release: 1  # r1: initial")
        self._commit("release: 2",
                     msg="chore: bulk\n\nNO-GATE: bulk mechanical change")
        self.assertEqual(self._range(), [])

    def test_new_package_at_release_one_exempt(self):
        self._commit("release: 1  # r1: unrelated", msg="chore: seed")
        other = Path(self.repo) / "packages" / "extra" / "fresh"
        other.mkdir(parents=True)
        (other / "package.yml").write_text("name: fresh\nrelease: 1\n")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "feat: add fresh")
        self.assertEqual(self._range(), [])

    def test_non_package_yml_ignored(self):
        self._commit("release: 1  # r1: initial")
        other = Path(self.repo) / "docs"
        other.mkdir()
        (other / "notes.yml").write_text("release: 5\n")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "docs: unrelated yml")
        self.assertEqual(self._range(), [])

    def test_older_note_changes_are_refused(self):
        original = "r3: third. r2: second. r1: first."
        cases = {
            "dropped": ("release: 4  # r4: fourth.", "r3"),
            "shortened": ("release: 4  # r4: fourth. r3: third. r1: first.", "r2"),
            "edited": ("release: 4  # r4: fourth. r3: third. r2: revised. r1: first.", "r2"),
            "reordered": ("release: 4  # r4: fourth. r2: second. r3: third. r1: first.", "r2"),
            "off-line": ("release: 4  # r4: fourth.\n# " + original, "r3"),
            "trailing-text": ("release: 4  # r4: fourth. " + original + " extra", "r1"),
            "whitespace": ("release: 4  # r4: fourth. r3: third. r2:  second. r1: first.", "r2"),
            "comment-only": ("release: 3  # r3: third. r2: revised. r1: first.", "r2"),
            "embedded-label": ("release: 4  # r4: fourth. not-" + original, "r3"),
        }
        for shape, (line, label) in cases.items():
            with self.subTest(shape=shape):
                self._commit("release: 3  # " + original)
                self._commit(line)
                violations = self._range()
                self.assertEqual(len(violations), 1)
                self.assertIn("packages/core/demo/package.yml", violations[0])
                self.assertIn("commit ", violations[0])
                self.assertIn("first differing label " + label, violations[0])
                self.assertIn("byte-exact suffix", violations[0])

    def test_prepended_head_retains_exact_older_notes(self):
        for old in ("r3: third. r2: second. r1: first.",
                    "r3: third.  # r2: second.  # r1: first.",
                    "r3: third. r2 (1): second. r1: first.",
                    "r3: third. r2: earlier. r2: second. r1: first."):
            with self.subTest(chain=old):
                self._commit("release: 3  # " + old)
                self._commit("release: 4  # r4: fourth. " + old)
                self.assertEqual(self._range(), [])

    def test_comment_only_edit_honors_no_gate(self):
        self._commit("release: 3  # r3: original. r2: old.")
        self._commit("release: 3  # r3: corrected.",
                     msg="docs: correction\n\nNO-GATE: deliberate note correction")
        self.assertEqual(self._range(), [])

    def test_untouched_legacy_chain_on_a_changed_recipe_passes(self):
        self._commit("release: 130  # r109: legacy")
        path = self.yml / "package.yml"
        path.write_text(path.read_text().replace("version: 1.0", "version: 2.0"))
        self._git("add", str(path))
        self._git("commit", "-q", "-m", "chore: version only")
        self.assertEqual(self._range(), [])

    def test_merge_resolution_checks_the_first_parent_chain(self):
        for index, accepted in enumerate((False, True)):
            with self.subTest(accepted=accepted):
                self._commit("release: 3  # r3: old.")
                side = f"side-{index}"
                self._git("branch", side)
                self._commit("release: 4  # r4: main. r3: old.")
                self._git("checkout", side)
                self._commit("release: 4  # r4: side. r3: old.")
                self._git("checkout", "-")
                merge = subprocess.run(
                    ["git", "-C", self.repo, "merge", "--no-commit", side],
                    capture_output=True, text=True)
                self.assertEqual(merge.returncode, 1, merge.stdout + merge.stderr)
                older = "old." if accepted else "changed."
                self._commit("release: 5  # r5: merge. r4: main. r3: " + older)
                violations = self._range()
                if accepted:
                    self.assertEqual(violations, [])
                else:
                    self.assertEqual(len(violations), 1)
                    self.assertIn("first differing label r3", violations[0])

    def test_a_previously_unlabelled_chain_can_start(self):
        self._commit("release: 3  ")
        self._commit("release: 4  # r4: first documented change.")
        self.assertEqual(self._range(), [])

    # -- lockstep with the bump script -------------------------------------

    def test_release_regex_matches_bump_script(self):
        spec = importlib.util.spec_from_file_location(
            "bump_changed_releases_test",
            REPO_ROOT / "scripts" / "bump-changed-releases.py")
        bump = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bump)
        self.assertEqual(self.mod.RELEASE_RE.pattern, bump.RELEASE_RE.pattern)


if __name__ == "__main__":
    unittest.main()
