# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The three changelog-enforcement surfaces, driven as real programs.

CHANGELOG.md was absent for R001 and its frame still read v1.0-Unreleased
afterwards, because nothing connected changing what ships to recording it.
Three surfaces close that, and each is tested here against a REAL git
repository built in a temporary directory — no mocked git, no stubbed
subprocess — because a gate that has only ever seen a fixture object has
not been shown to work on the thing it will actually meet.

  scripts/check-changelog-accumulation.py         (pre-push, fail-closed)
  scripts/preflight-changelog-release-lockstep.py (mint/publish, fail-closed)
  scripts/draft-changelog-section.py              (a convenience, never a gate)

The lockstep cases matter as much as the behaviour ones: three separate
files parse the same `release:` and `version:` shapes, and the moment they
disagree the gate enforces one set while the draft tool reports another.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
ACCUM = SCRIPTS / "check-changelog-accumulation.py"
PREFLIGHT = SCRIPTS / "preflight-changelog-release-lockstep.py"
DRAFT = SCRIPTS / "draft-changelog-section.py"
RELEASE_NOTES = SCRIPTS / "check-release-notes.py"

OS_RELEASE_REL = ("packages/core/intergenos-base-files/files/etc/os-release")


def _run(script, *args):
    p = subprocess.run([sys.executable, str(script), *args],
                       capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


class _GitFixture(unittest.TestCase):
    """A real repository, built commit by commit."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="changelog-gate-test-")
        self.repo = Path(self._tmp)
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.email", "test@example.invalid")
        self._git("config", "user.name", "Test")
        self._git("config", "commit.gpgsign", "false")
        # core.hooksPath is inherited from the outer repo otherwise, which
        # would run the real pre-commit chain against this fixture.
        self._git("config", "core.hooksPath", str(self.repo / ".nohooks"))
        self.write("README.md", "fixture\n")
        self.commit("root")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args],
                              check=True, capture_output=True,
                              text=True).stdout

    def write(self, rel, text):
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def remove(self, rel):
        (self.repo / rel).unlink()

    def commit(self, message):
        self._git("add", "-A")
        self._git("commit", "-q", "-m", message, "--no-verify")
        return self._git("rev-parse", "HEAD").strip()

    def recipe(self, tier, name, version, release=1, extra=""):
        self.write(f"packages/{tier}/{name}/package.yml",
                   f"name: {name}\nversion: \"{version}\"\n"
                   f"release: {release}  # r{release}: note\n{extra}")


# ----------------------------------------------------------------------
# The accumulation gate
# ----------------------------------------------------------------------
class AccumulationGateTest(_GitFixture):

    def _check(self, base, head="HEAD"):
        return _run(ACCUM, "--repo", str(self.repo), "--base", base,
                    "--head", head)

    def test_adding_a_package_without_a_changelog_entry_blocks(self):
        base = self._git("rev-parse", "HEAD").strip()
        self.recipe("extra", "newthing", "1.0")
        self.commit("add newthing")
        rc, out = self._check(base)
        self.assertEqual(rc, 1, out)
        self.assertIn("adds package newthing", out)

    def test_touching_the_changelog_anywhere_in_the_range_satisfies_it(self):
        base = self._git("rev-parse", "HEAD").strip()
        self.recipe("extra", "newthing", "1.0")
        self.commit("add newthing")
        self.write("CHANGELOG.md", "# Changelog\n\n- newthing arrives\n")
        self.commit("record it")
        rc, out = self._check(base)
        self.assertEqual(rc, 0, out)

    def test_a_version_move_blocks(self):
        self.recipe("extra", "thing", "1.0")
        base = self.commit("thing 1.0")
        self.recipe("extra", "thing", "2.0", release=1)
        self.commit("thing 2.0")
        rc, out = self._check(base)
        self.assertEqual(rc, 1, out)
        self.assertIn("moves thing 1.0 -> 2.0", out)

    def test_a_release_bump_alone_does_not_block(self):
        """The narrowed trigger, and the reason for it.

        A release bump with the upstream version unchanged is a rebuild or a
        metadata change. The recipe's own `# rNN:` chain already records it
        and check-release-notes.py enforces that. Firing here as well would
        demand an exemption on most pushes, which turns the exemption into
        boilerplate.
        """
        self.recipe("extra", "thing", "1.0", release=1)
        base = self.commit("thing r1")
        self.recipe("extra", "thing", "1.0", release=2)
        self.commit("thing r2 — rebuild only")
        rc, out = self._check(base)
        self.assertEqual(rc, 0, out)

    def test_removing_a_package_blocks(self):
        self.recipe("extra", "goner", "1.0")
        base = self.commit("goner")
        self.remove("packages/extra/goner/package.yml")
        self.commit("drop goner")
        rc, out = self._check(base)
        self.assertEqual(rc, 1, out)
        self.assertIn("removes package goner", out)

    def test_a_reasoned_trailer_satisfies_it(self):
        base = self._git("rev-parse", "HEAD").strip()
        self.recipe("extra", "internalonly", "1.0")
        self.commit("add internalonly\n\nChangelog-Exempt: a build-time only "
                    "helper that no installed system receives")
        rc, out = self._check(base)
        self.assertEqual(rc, 0, out)

    def test_a_placeholder_trailer_is_refused_and_says_why(self):
        base = self._git("rev-parse", "HEAD").strip()
        self.recipe("extra", "thing", "1.0")
        self.commit("add thing\n\nChangelog-Exempt: n/a")
        rc, out = self._check(base)
        self.assertEqual(rc, 1, out)
        self.assertIn("placeholder, not a reason", out)

    def test_an_empty_trailer_is_refused(self):
        base = self._git("rev-parse", "HEAD").strip()
        self.recipe("extra", "thing", "1.0")
        self.commit("add thing\n\nChangelog-Exempt:")
        rc, out = self._check(base)
        self.assertEqual(rc, 1, out)
        self.assertIn("no reason at all", out)

    def test_a_one_word_trailer_is_refused(self):
        base = self._git("rev-parse", "HEAD").strip()
        self.recipe("extra", "thing", "1.0")
        self.commit("add thing\n\nChangelog-Exempt: internalrefactoring")
        rc, out = self._check(base)
        self.assertEqual(rc, 1, out)
        self.assertIn("one word", out)

    def test_no_gate_override_is_honored(self):
        base = self._git("rev-parse", "HEAD").strip()
        self.recipe("extra", "thing", "1.0")
        self.commit("add thing\n\nNO-GATE: emergency, reason recorded")
        rc, out = self._check(base)
        self.assertEqual(rc, 0, out)

    def test_an_unreadable_range_fails_closed(self):
        """A gate that cannot read its range must never pass it."""
        rc, out = self._check("definitelynotarealref", "alsonotreal")
        self.assertEqual(rc, 1, out)
        self.assertIn("could not read the range", out)

    def test_an_empty_range_is_clean(self):
        head = self._git("rev-parse", "HEAD").strip()
        rc, out = self._check(head, head)
        self.assertEqual(rc, 0, out)


# ----------------------------------------------------------------------
# The mint/publish preflight
# ----------------------------------------------------------------------
class ReleaseLockstepPreflightTest(_GitFixture):

    def _seed(self, version_id="r001.1", changelog=None):
        self.write(OS_RELEASE_REL,
                   f'NAME="InterGenOS"\nID=intergenos\n'
                   f'VERSION_ID={version_id}\n')
        if changelog is not None:
            self.write("CHANGELOG.md", changelog)
        self.commit("seed")

    GOOD = ("# Changelog\n\n## [Unreleased]\n\nNothing yet.\n\n"
            "## [R001.1] — 2026-08-20\n\n### Added\n- a real entry\n")

    def _check(self, *extra):
        return _run(PREFLIGHT, "--repo", str(self.repo), *extra)

    def test_a_correct_changelog_is_ready(self):
        self._seed(changelog=self.GOOD)
        rc, out = self._check()
        self.assertEqual(rc, 0, out)
        self.assertIn("READY", out)

    def test_the_identity_comes_from_os_release_not_a_literal(self):
        """Change only os-release and the verdict must change with it."""
        self._seed(version_id="r002", changelog=self.GOOD)
        rc, out = self._check()
        self.assertEqual(rc, 1, out)
        self.assertIn("the release is r002", out)

    def test_a_placeholder_date_is_refused(self):
        self._seed(changelog=self.GOOD.replace("2026-08-20", "2026-08-XX"))
        rc, out = self._check()
        self.assertEqual(rc, 1, out)
        self.assertIn("placeholder rather than a date", out)

    def test_an_empty_section_is_refused(self):
        self._seed(changelog=("# Changelog\n\n## [R001.1] — 2026-08-20\n\n"
                              "<!-- nothing yet -->\n"))
        rc, out = self._check()
        self.assertEqual(rc, 1, out)
        self.assertIn("is empty", out)

    def test_only_unreleased_is_refused(self):
        self._seed(changelog="# Changelog\n\n## [Unreleased]\n\nNothing.\n")
        rc, out = self._check()
        self.assertEqual(rc, 1, out)
        self.assertIn("only an [Unreleased] section", out)

    def test_a_heading_with_trailing_text_is_still_the_top_section(self):
        """The measured fail-open, pinned.

        With the earlier end-anchored heading pattern this exact document
        printed READY: the top heading carried text after the date, matched
        nothing, and the gate walked past the release being cut to evaluate
        the one below it — reporting a clean placeholder-dated changelog as
        ready to publish. The verdict must come from the TOP section.
        """
        self._seed(changelog=(
            "# Changelog\n\n"
            "## [R001.1] — 2026-08-XX (date set on publication day)\n\n"
            "### Added\n- something\n\n"
            "## [R001.1] — 2026-08-16\n\n### Added\n- the previous entry\n"))
        rc, out = self._check()
        self.assertEqual(rc, 1, out)
        self.assertIn("placeholder rather than a date", out)

    def test_an_unparseable_heading_above_the_release_refuses(self):
        """The same fail-open in its other spelling: brackets omitted.

        Reading past a heading it cannot parse would mean certifying
        whichever section sat below it, so the gate refuses and names the
        heading instead.
        """
        self._seed(changelog=(
            "# Changelog\n\n## R001.2 — 2026-09-01\n\nsomething\n\n"
            "## [R001.1] — 2026-08-16\n\n### Added\n- fine\n"))
        rc, out = self._check()
        self.assertEqual(rc, 1, out)
        self.assertIn("sits above the first release section", out)

    def test_a_trailing_non_release_heading_does_not_break_the_walk(self):
        """`## Earlier history` is legitimate and sits BELOW the release."""
        self._seed(changelog=(self.GOOD + "\n## Earlier history\n\nsee git.\n"))
        rc, out = self._check()
        self.assertEqual(rc, 0, out)

    def test_a_missing_os_release_fails_closed(self):
        self.write("CHANGELOG.md", self.GOOD)
        self.commit("changelog only, no os-release")
        rc, out = self._check()
        self.assertEqual(rc, 1, out)
        self.assertIn("cannot read the packaged os-release", out)

    def test_require_site_without_a_path_fails_closed(self):
        self._seed(changelog=self.GOOD)
        rc, out = self._check("--require-site")
        self.assertEqual(rc, 1, out)
        self.assertIn("refuses to guess", out)

    def test_require_site_with_an_unreadable_path_fails_closed(self):
        self._seed(changelog=self.GOOD)
        rc, out = self._check("--require-site", "--site-repo",
                              str(self.repo / "nope"))
        self.assertEqual(rc, 1, out)
        self.assertIn("not a readable directory", out)

    def test_a_ready_site_entry_satisfies_the_publish_preflight(self):
        self._seed(changelog=self.GOOD)
        site = self.repo / "sitefixture"
        (site / "content").mkdir(parents=True)
        (site / "content" / "updates.md").write_text(
            "# Updates\n\n## InterGenOS R001.1 — READY\nThe point release.\n")
        rc, out = self._check("--require-site", "--site-repo", str(site))
        self.assertEqual(rc, 0, out)

    def test_a_site_entry_not_marked_ready_is_refused(self):
        self._seed(changelog=self.GOOD)
        site = self.repo / "sitefixture"
        (site / "content").mkdir(parents=True)
        (site / "content" / "updates.md").write_text(
            "# Updates\n\n## InterGenOS R001.1 — DRAFT\nThe point release.\n")
        rc, out = self._check("--require-site", "--site-repo", str(site))
        self.assertEqual(rc, 1, out)
        self.assertIn("marked READY", out)

    def test_a_site_with_no_updates_file_is_refused(self):
        self._seed(changelog=self.GOOD)
        site = self.repo / "sitefixture"
        (site / "content").mkdir(parents=True)
        (site / "content" / "index.md").write_text("# Home\n")
        rc, out = self._check("--require-site", "--site-repo", str(site))
        self.assertEqual(rc, 1, out)
        self.assertIn("no Updates file found", out)


# ----------------------------------------------------------------------
# The draft generator
# ----------------------------------------------------------------------
class DraftGeneratorTest(_GitFixture):

    def test_it_reports_adds_moves_and_removes_separately(self):
        self.recipe("extra", "mover", "1.0")
        self.recipe("extra", "goner", "3.0")
        base = self.commit("seed")
        self.recipe("extra", "mover", "2.0")
        self.recipe("base", "arriver", "0.9")
        self.remove("packages/extra/goner/package.yml")
        self.commit("a bit of everything")
        rc, out = _run(DRAFT, "--repo", str(self.repo), "--base", base,
                       "--head", "HEAD")
        self.assertEqual(rc, 0, out)
        self.assertIn("### Added", out)
        self.assertIn("`arriver` 0.9", out)
        self.assertIn("### Changed", out)
        self.assertIn("`mover` 1.0 → 2.0", out)
        self.assertIn("### Removed", out)
        self.assertIn("`goner`", out)

    def test_rebuild_only_bumps_are_shown_but_marked_probably_not_user_facing(self):
        self.recipe("extra", "thing", "1.0", release=1)
        base = self.commit("seed")
        self.recipe("extra", "thing", "1.0", release=2)
        self.commit("rebuild")
        rc, out = _run(DRAFT, "--repo", str(self.repo), "--base", base,
                       "--head", "HEAD")
        self.assertEqual(rc, 0, out)
        self.assertIn("probably NOT user", out)
        self.assertIn("thing: release 1 -> 2", out)
        # and it must NOT be offered as an Added/Changed line
        self.assertNotIn("### Changed", out)

    def test_it_writes_nothing(self):
        """It is a convenience. It must never modify the repository."""
        before = self._git("status", "--porcelain")
        head = self._git("rev-parse", "HEAD").strip()
        _run(DRAFT, "--repo", str(self.repo), "--base", head, "--head", head)
        self.assertEqual(self._git("status", "--porcelain"), before)


# ----------------------------------------------------------------------
# Lockstep — three files, one shape
# ----------------------------------------------------------------------
class LockstepTest(unittest.TestCase):
    """The moment these disagree, the gate enforces one set and the draft
    tool reports another. That divergence would be silent, so it is pinned."""

    def test_release_shape_matches_the_release_note_gate(self):
        a = re.search(r"RELEASE_RE = re\.compile\(\n?\s*(r'.*?')",
                      ACCUM.read_text(), re.S).group(1)
        b = re.search(r"RELEASE_RE = re\.compile\(\n?\s*(r'.*?')",
                      RELEASE_NOTES.read_text(), re.S).group(1)
        self.assertEqual(a, b,
                         "the accumulation gate and the release-note gate "
                         "disagree about what a release line looks like")

    def test_version_shape_matches_the_draft_tool(self):
        a = re.search(r"VERSION_RE = re\.compile\(\n?\s*(r'.*?')",
                      ACCUM.read_text(), re.S).group(1)
        b = re.search(r"VERSION_RE = re\.compile\(\n?\s*(r'.*?')",
                      DRAFT.read_text(), re.S).group(1)
        self.assertEqual(a, b,
                         "the accumulation gate and the draft tool disagree "
                         "about what a version line looks like")

    def test_the_draft_tool_is_not_wired_into_any_gate_chain(self):
        """It is a convenience and must never become a gate.

        The hook's existence is ASSERTED rather than treated as optional. A
        `if hook.exists()` guard here would make this test pass silently on
        any tree where the hook was renamed or removed — which is exactly
        the tree where the assertion matters most.
        """
        hook = REPO_ROOT / ".githooks" / "pre-push"
        self.assertTrue(hook.is_file(),
                        f"{hook} is missing; this test cannot verify what it "
                        "claims to verify")
        self.assertNotIn("draft-changelog-section", hook.read_text())


if __name__ == "__main__":
    unittest.main()
