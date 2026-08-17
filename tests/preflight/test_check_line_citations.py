"""The extended line-citation gate refuses and passes what it should.

Pins the 2026-08-16 extension: both citation forms (markdown link +
backticked plain text), `.config` in the extension set, the semantic
anchor check, the zero-validated wording, and the basename-index
subtree exclusion (whose original single-part form excluded nothing).
Every class is tested in both directions: the violating shape refused,
the legitimate neighbour passed. The tests drive the REAL scanner via
subprocess against throwaway trees through IGOS_CITE_REPO_ROOT — the
same override a test harness is documented to use.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SCANNER = REPO / "scripts" / "check_line_citations.py"


def _tree(tmp_path: Path) -> Path:
    """A throwaway citation-target tree."""
    root = tmp_path / "tree"
    (root / "src").mkdir(parents=True)
    (root / "src" / "mod.py").write_text(
        "import os\n"
        "def do_thing_now():\n"
        "    return 1\n"
        "# filler\n"
        "# filler\n")
    (root / "cfg").mkdir()
    (root / "cfg" / "frag.config").write_text(
        "CONFIG_ALPHA_MODE=y\n"
        "# CONFIG_BETA_MODE is not set\n")
    return root


def _scan(root: Path, doc: Path, *extra: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "IGOS_CITE_REPO_ROOT": str(root)}
    args = [sys.executable, str(SCANNER)]
    args += list(extra) if extra else [str(doc)]
    return subprocess.run(args, capture_output=True, text=True, env=env,
                          cwd=root)


class TestBothCitationForms:
    def test_markdown_link_in_bounds_passes(self, tmp_path):
        root = _tree(tmp_path)
        doc = root / "doc.md"
        doc.write_text("see [src/mod.py:2](src/mod.py#L2) here\n")
        r = _scan(root, doc)
        assert r.returncode == 0
        assert "citations OK: 1" in r.stdout

    def test_markdown_link_out_of_bounds_refused(self, tmp_path):
        root = _tree(tmp_path)
        doc = root / "doc.md"
        doc.write_text("see [src/mod.py:99](src/mod.py#L99) here\n")
        r = _scan(root, doc)
        assert r.returncode == 1
        assert "OUT-OF-BOUNDS" in r.stdout

    def test_backtick_form_in_bounds_passes(self, tmp_path):
        root = _tree(tmp_path)
        doc = root / "doc.md"
        doc.write_text("see `src/mod.py:3` here\n")
        r = _scan(root, doc)
        assert r.returncode == 0
        assert "citations OK: 1" in r.stdout

    def test_backtick_form_out_of_bounds_refused(self, tmp_path):
        root = _tree(tmp_path)
        doc = root / "doc.md"
        doc.write_text("see `src/mod.py:40-60` here\n")
        r = _scan(root, doc)
        assert r.returncode == 1
        assert "OUT-OF-BOUNDS" in r.stdout


class TestConfigExtension:
    def test_config_citation_recognized_and_passes(self, tmp_path):
        root = _tree(tmp_path)
        doc = root / "doc.md"
        doc.write_text("`CONFIG_ALPHA_MODE=y` set at `cfg/frag.config:1`\n")
        r = _scan(root, doc)
        assert r.returncode == 0
        assert "citations OK: 1" in r.stdout

    def test_config_citation_wrong_line_is_semantic_mismatch(self, tmp_path):
        # In bounds, but line 2 does not carry the cited symbol — the
        # 7,000-lines-wrong-but-in-bounds class, shrunk to two lines.
        root = _tree(tmp_path)
        doc = root / "doc.md"
        doc.write_text("`CONFIG_ALPHA_MODE=y` set at `cfg/frag.config:2`\n")
        r = _scan(root, doc)
        assert r.returncode == 1
        assert "SEMANTIC-MISMATCH" in r.stdout
        assert "CONFIG_ALPHA_MODE" in r.stdout


class TestSemanticAnchor:
    def test_anchor_present_in_cited_range_passes(self, tmp_path):
        root = _tree(tmp_path)
        doc = root / "doc.md"
        doc.write_text("`do_thing_now` is defined at `src/mod.py:2-3`\n")
        r = _scan(root, doc)
        assert r.returncode == 0

    def test_anchor_absent_from_cited_range_refused(self, tmp_path):
        root = _tree(tmp_path)
        doc = root / "doc.md"
        doc.write_text("`do_thing_now` is defined at `src/mod.py:4-5`\n")
        r = _scan(root, doc)
        assert r.returncode == 1
        assert "SEMANTIC-MISMATCH" in r.stdout

    def test_no_anchor_keeps_bounds_only_check(self, tmp_path):
        # The citing line carries no backticked symbol beyond the citation
        # itself: an in-bounds citation passes on bounds alone.
        root = _tree(tmp_path)
        doc = root / "doc.md"
        doc.write_text("the filler region is `src/mod.py:4-5`\n")
        r = _scan(root, doc)
        assert r.returncode == 0
        assert "citations OK: 1" in r.stdout


class TestResolution:
    def test_missing_file_refused(self, tmp_path):
        root = _tree(tmp_path)
        doc = root / "doc.md"
        doc.write_text("see `nope/gone.py:3`\n")
        r = _scan(root, doc)
        assert r.returncode == 1
        assert "MISSING" in r.stdout

    def test_ambiguous_bare_filename_refused(self, tmp_path):
        root = _tree(tmp_path)
        (root / "a").mkdir()
        (root / "b").mkdir()
        (root / "a" / "same.py").write_text("x = 1\n")
        (root / "b" / "same.py").write_text("y = 2\n")
        doc = root / "doc.md"
        doc.write_text("see `same.py:1`\n")
        r = _scan(root, doc)
        assert r.returncode == 1
        assert "AMBIGUOUS" in r.stdout

    def test_unique_bare_filename_resolves(self, tmp_path):
        root = _tree(tmp_path)
        doc = root / "doc.md"
        doc.write_text("see `mod.py:1`\n")
        r = _scan(root, doc)
        assert r.returncode == 0
        assert "citations OK: 1" in r.stdout

    def test_inverted_range_refused(self, tmp_path):
        root = _tree(tmp_path)
        doc = root / "doc.md"
        doc.write_text("see `src/mod.py:5-2`\n")
        r = _scan(root, doc)
        assert r.returncode == 1
        assert "INVERTED RANGE" in r.stdout


class TestBasenameIndexExclusion:
    def test_vendored_subtree_not_a_bare_name_target(self, tmp_path):
        # The exclusion's original form put "docs/lfs-13.0" in a set
        # compared against single path components, which can never match.
        # Pin the fixed behavior: a file that exists ONLY inside the
        # vendored subtree does not resolve from a bare-name citation.
        root = _tree(tmp_path)
        vend = root / "docs" / "lfs-13.0"
        vend.mkdir(parents=True)
        (vend / "only_here.html").write_text("<p>book</p>\n")
        doc = root / "doc.md"
        doc.write_text("see `only_here.html:1`\n")
        r = _scan(root, doc)
        assert r.returncode == 1
        assert "MISSING" in r.stdout

    def test_same_basename_outside_subtree_resolves(self, tmp_path):
        root = _tree(tmp_path)
        vend = root / "docs" / "lfs-13.0"
        vend.mkdir(parents=True)
        (vend / "page.html").write_text("<p>book</p>\n")
        (root / "docs" / "page.html").write_text("<p>ours</p>\n")
        doc = root / "doc.md"
        doc.write_text("see `page.html:1`\n")
        r = _scan(root, doc)
        # The vendored twin is invisible, so the bare name is UNIQUE, not
        # ambiguous — and resolves to the non-vendored file.
        assert r.returncode == 0
        assert "citations OK: 1" in r.stdout


class TestZeroValidatedWording:
    def test_zero_citations_says_so_and_never_prints_all_clear(self, tmp_path):
        root = _tree(tmp_path)
        doc = root / "doc.md"
        doc.write_text("no citations live here\n")
        r = _scan(root, doc)
        assert r.returncode == 0
        assert "validated ZERO citations" in r.stdout
        assert "all citations resolve cleanly" not in r.stdout

    def test_nonzero_run_prints_the_all_clear(self, tmp_path):
        root = _tree(tmp_path)
        doc = root / "doc.md"
        doc.write_text("see `src/mod.py:1`\n")
        r = _scan(root, doc)
        assert r.returncode == 0
        assert "all citations resolve cleanly" in r.stdout


class TestDiffOnlyMode:
    def _git_root(self, tmp_path: Path) -> Path:
        root = _tree(tmp_path)
        run = lambda *a: subprocess.run(["git", *a], cwd=root,
                                        capture_output=True, text=True,
                                        check=True)
        run("init", "-q")
        run("config", "user.name", "t")
        run("config", "user.email", "t@t.invalid")
        (root / "doc.md").write_text("baseline, and a PRE-EXISTING bad "
                                     "citation `src/mod.py:99` that diff-only "
                                     "must ignore\n")
        run("add", "-A")
        run("commit", "-q", "-m", "chore: baseline")
        return root

    def _diff_only(self, root: Path) -> subprocess.CompletedProcess:
        env = {**os.environ, "IGOS_CITE_REPO_ROOT": str(root)}
        return subprocess.run(
            [sys.executable, str(SCANNER), "--diff-only"],
            capture_output=True, text=True, env=env, cwd=root)

    def test_staged_good_citation_passes_despite_baseline_drift(self, tmp_path):
        root = self._git_root(tmp_path)
        doc = root / "doc.md"
        doc.write_text(doc.read_text() + "new line citing `src/mod.py:2`\n")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        r = self._diff_only(root)
        assert r.returncode == 0
        assert "new citations OK: 1" in r.stdout

    def test_staged_stale_citation_refused(self, tmp_path):
        root = self._git_root(tmp_path)
        doc = root / "doc.md"
        doc.write_text(doc.read_text() + "new line citing `src/mod.py:77`\n")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        r = self._diff_only(root)
        assert r.returncode == 1
        assert "OUT-OF-BOUNDS" in r.stdout

    def test_staged_semantic_mismatch_refused(self, tmp_path):
        root = self._git_root(tmp_path)
        doc = root / "doc.md"
        doc.write_text(doc.read_text() +
                       "`do_thing_now` moved to `src/mod.py:4`\n")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        r = self._diff_only(root)
        assert r.returncode == 1
        assert "SEMANTIC-MISMATCH" in r.stdout

    def test_no_new_citations_states_zero(self, tmp_path):
        root = self._git_root(tmp_path)
        doc = root / "doc.md"
        doc.write_text(doc.read_text() + "a plain new line\n")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        r = self._diff_only(root)
        assert r.returncode == 0
        assert "validated ZERO citations" in r.stdout
