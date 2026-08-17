"""The public-content scanner's 2026-08-16 scope fixes stay fixed.

The adversarial release verification found four ways content the gate was
built to refuse could sit in the tree while the gate reported PASS, plus one
way a --dir scan of another repository flagged that repository's own scanner:

1. Everything under assets/ and images/ was skipped wholesale as "binary",
   although 100+ tracked TEXT files live there and ship to installed systems.
2. The coined-seat-name tier was compiled case-sensitively, so a lowercase
   spelling passed.
3. A token wrapped across a comment-line break defeated the per-line scan.
4. The private ledger's filename had no tier at all.
5. SKIP_PATHS was consulted only for tracked-file scans, never in --dir mode.

These tests drive the REAL scanner via subprocess, in the same both-directions
style as test_public_language_detectors.py: the violating shape refused, the
legitimate neighbour passed. Fixture tokens are ASSEMBLED at runtime (the
sibling file's documented discipline) so this file never spells them.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SCANNER = REPO / "scripts" / "check-public-content.py"


def _scan_tree(tmp_path: Path, relpath: str, content: str) -> subprocess.CompletedProcess:
    tree = tmp_path / "tree"
    target = tree / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return subprocess.run(
        ["python3", str(SCANNER), "--dir", str(tree)],
        capture_output=True, text=True,
    )


class TestAssetsAreScanned:
    def test_banned_token_under_assets_refused(self, tmp_path):
        r = _scan_tree(tmp_path, "assets/ext/extension.js",
                       "// reviewed per " + "SP" "OC" + " note\n")
        assert r.returncode == 1
        assert "FLEET-HOST" in r.stdout or "AGENT-ABBREV" in r.stdout

    def test_clean_text_under_assets_passes(self, tmp_path):
        r = _scan_tree(tmp_path, "assets/ext/extension.js",
                       "// suppress the overview at startup\n")
        assert r.returncode == 0

    def test_png_under_assets_still_skipped(self, tmp_path):
        # A binary-extension file is skipped by extension, not by directory.
        # A clean sibling keeps the scan non-empty (an all-skipped tree is a
        # REFUSAL, exit 2 — correct scanner behavior, not a pass).
        tree = tmp_path / "tree"
        (tree / "assets/img").mkdir(parents=True)
        (tree / "assets/img/x.png").write_text("per " + "SP" "OC" + "\n")
        (tree / "README.md").write_text("clean\n")
        r = subprocess.run(["python3", str(SCANNER), "--dir", str(tree)],
                           capture_output=True, text=True)
        assert r.returncode == 0


class TestFleetHostCaseInsensitive:
    def test_lowercase_seat_name_refused(self, tmp_path):
        r = _scan_tree(tmp_path, "doc.md", "# handled by the " + "sp" "oc" + " kickoff\n")
        assert r.returncode == 1
        assert "FLEET-HOST" in r.stdout

    def test_unrelated_word_passes(self, tmp_path):
        r = _scan_tree(tmp_path, "doc.md", "# the sponsor kickoff\n")
        assert r.returncode == 0


class TestWrapScan:
    def test_internal_filename_wrapped_over_comment_break_refused(self, tmp_path):
        r = _scan_tree(
            tmp_path, "script.sh",
            "# see context_" + "carry" "over" + "_\n# 20260101_example_note.md here\n")
        assert r.returncode == 1
        assert "INTERNAL-FILE" in r.stdout
        assert "wrapped" in r.stdout

    def test_whole_token_reported_once_not_twice(self, tmp_path):
        r = _scan_tree(tmp_path, "script.sh",
                       "# see context_" + "carry" "over" + "_20260101_example_note.md\n")
        assert r.returncode == 1
        assert r.stdout.count("INTERNAL-FILE") == 1

    def test_ordinary_two_line_comment_passes(self, tmp_path):
        r = _scan_tree(tmp_path, "script.sh",
                       "# this build step compiles the\n# kernel modules cleanly\n")
        assert r.returncode == 0


class TestLedgerTier:
    def test_ledger_citation_refused(self, tmp_path):
        r = _scan_tree(tmp_path, "notes.py",
                       "# tracked in " + "TRACKER" + "_3.0.md row L1\n")
        assert r.returncode == 1
        assert "INTERNAL-LEDGER" in r.stdout

    def test_generic_tracker_word_passes(self, tmp_path):
        r = _scan_tree(tmp_path, "notes.py",
                       "# reported on the upstream issue tracker\n")
        assert r.returncode == 0

    def test_exempt_tooling_passes_in_repo_file_mode(self):
        # The audit tooling that must LOCATE the ledger stays exempt.
        r = subprocess.run(
            ["python3", str(SCANNER), "--file", "scripts/anchor-tracker.sh"],
            capture_output=True, text=True, cwd=REPO,
        )
        assert r.returncode == 0


class TestDirModeSkipPaths:
    def test_foreign_repo_scanner_skipped_in_dir_mode(self, tmp_path):
        # A --dir scan of another repository must not flag that repository's
        # own scanner (scanner-class: a denylist contains its own terms).
        tree = tmp_path / "tree"
        (tree / "scripts").mkdir(parents=True)
        (tree / "scripts/check-web-content.py").write_text(
            "# FLEET-OPS   " + "SP" "OC" + " + fleet-coordination phrasing\n")
        (tree / "README.md").write_text("clean\n")
        r = subprocess.run(["python3", str(SCANNER), "--dir", str(tree)],
                           capture_output=True, text=True)
        assert r.returncode == 0

    def test_non_scanner_file_still_scanned_in_dir_mode(self, tmp_path):
        r = _scan_tree(tmp_path, "scripts/other-tool.py", "# per " + "SP" "OC" + "\n")
        assert r.returncode == 1
