# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The public-content scanner loads its identity patterns from a private file.

Those patterns used to live in the scanner's own public source, which published
the exact strings the scanner exists to keep out of public text — and SKIP_PATHS
excuses that file from its own scan, so nothing measured the leak. They now load
at run time from a file outside any repository.

The property that matters most here is the FAILURE direction. A scanner that
cannot find its patterns must REFUSE (exit 2 — the scan did not happen), never
run a reduced tier set and print PASS, because a reduced scan and a clean tree
look identical to every caller. Each way the file can be unusable is asserted
separately: absent, empty, short of a required group, malformed, and carrying a
regex that will not compile.

Every token here is SYNTHETIC and coined in this file, and every pattern file is
written under tmp_path — these tests never read the real private file, so the
suite stays runnable on a machine that does not have one.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
SCANNER = REPO / "scripts" / "check-public-content.py"

# Synthetic tokens — coined here, meaningless outside this file.
TOK_NAME = "ZQXNAMEA"
TOK_ABBREV = "ZQXABBR"
TOK_HOME = "/home/zqxuser/"
TOK_HOST = "ZQXHOSTC"

GOOD_PATTERNS = (
    "[AGENT_NAMES]\n"
    f"AGENT-NAME\t{TOK_NAME}\n"
    "[AGENT_ABBREV]\n"
    f"AGENT-ABBREV\tby\\s+{TOK_ABBREV}\n"
    "[HOME_PATH]\n"
    f"HOME-PATH\t{TOK_HOME}\n"
    "[FLEET_HOST_BLOCK]\n"
    f"FLEET-HOST\t(?i)\\b{TOK_HOST}\\b\n"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("check_public_content", SCANNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _scan(tmp_path: Path, patterns: str | None, content: str,
          patterns_name: str = "patterns") -> subprocess.CompletedProcess:
    """Drive the REAL scanner over a fixture tree with a given pattern file."""
    tree = tmp_path / "tree"
    tree.mkdir(exist_ok=True)
    (tree / "f.txt").write_text(content, encoding="utf-8")
    env = dict(os.environ)
    pfile = tmp_path / patterns_name
    if patterns is not None:
        pfile.write_text(patterns, encoding="utf-8")
    env["IGOS_PUBLIC_CONTENT_PATTERNS"] = str(pfile)
    return subprocess.run(
        ["python3", str(SCANNER), "--dir", str(tree)],
        capture_output=True, text=True, timeout=180, env=env,
    )


def _assert_refused(result: subprocess.CompletedProcess, because: str) -> None:
    assert result.returncode == 2, (
        f"{because}: expected the refusal exit (2 — the scan did not happen), "
        f"got {result.returncode}. Exit 1 would read as a detection and exit 0 "
        f"as a clean tree; both would be a scan that never ran, reported as a "
        f"result.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "REFUSED" in result.stderr, (
        f"{because}: a refusal must say so loudly on stderr.\n{result.stderr}")
    assert "PASS" not in result.stdout, (
        f"{because}: a refusal must never print a pass.\n{result.stdout}")


class FailClosedTests:
    """Grouping marker only — pytest collects the module-level tests below."""


def test_absent_pattern_file_refuses(tmp_path):
    result = _scan(tmp_path, None, "an ordinary neutral line\n")
    _assert_refused(result, "absent pattern file")
    assert "not found" in result.stderr


def test_empty_pattern_file_refuses(tmp_path):
    result = _scan(tmp_path, "# only comments\n\n", "an ordinary neutral line\n")
    _assert_refused(result, "pattern file with no entries")


def test_missing_required_group_refuses(tmp_path):
    partial = f"[AGENT_NAMES]\nAGENT-NAME\t{TOK_NAME}\n"
    result = _scan(tmp_path, partial, "an ordinary neutral line\n")
    _assert_refused(result, "pattern file short of a required group")
    assert "AGENT_ABBREV" in result.stderr, (
        "the refusal must name the groups that are missing — the reader is "
        "someone whose push just stopped")


def test_group_present_but_empty_refuses(tmp_path):
    hollow = GOOD_PATTERNS.replace(f"HOME-PATH\t{TOK_HOME}\n", "")
    result = _scan(tmp_path, hollow, "an ordinary neutral line\n")
    _assert_refused(result, "required group present but carrying no entries")
    assert "HOME_PATH" in result.stderr


def test_malformed_entry_refuses(tmp_path):
    bad = f"[AGENT_NAMES]\nAGENT-NAME {TOK_NAME} with no tab\n"
    result = _scan(tmp_path, bad, "an ordinary neutral line\n")
    _assert_refused(result, "entry with no tab separator")
    assert ":2:" in result.stderr, "the refusal must name the offending line"


def test_entry_before_any_group_header_refuses(tmp_path):
    bad = f"AGENT-NAME\t{TOK_NAME}\n[AGENT_NAMES]\n"
    result = _scan(tmp_path, bad, "an ordinary neutral line\n")
    _assert_refused(result, "entry before any group header")


def test_uncompilable_regex_refuses(tmp_path):
    bad = GOOD_PATTERNS.replace(f"AGENT-NAME\t{TOK_NAME}\n", "AGENT-NAME\t(unclosed\n")
    result = _scan(tmp_path, bad, "an ordinary neutral line\n")
    _assert_refused(result, "entry whose regex will not compile")
    assert "invalid regex" in result.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file")
def test_unreadable_pattern_file_refuses(tmp_path):
    pfile = tmp_path / "patterns"
    pfile.write_text(GOOD_PATTERNS, encoding="utf-8")
    pfile.chmod(0o000)
    try:
        tree = tmp_path / "tree"
        tree.mkdir(exist_ok=True)
        (tree / "f.txt").write_text("an ordinary neutral line\n", encoding="utf-8")
        env = dict(os.environ)
        env["IGOS_PUBLIC_CONTENT_PATTERNS"] = str(pfile)
        result = subprocess.run(
            ["python3", str(SCANNER), "--dir", str(tree)],
            capture_output=True, text=True, timeout=180, env=env)
        _assert_refused(result, "unreadable pattern file")
    finally:
        pfile.chmod(0o600)


def test_loaded_patterns_still_block_through_the_new_path(tmp_path):
    """The whole point: a moved pattern must still fire, from its new home."""
    content = (
        f"one {TOK_NAME} here\n"
        f"two reviewed by {TOK_ABBREV} here\n"
        f"three the path {TOK_HOME}x here\n"
        f"four a bare {TOK_HOST} here\n"
        f"five a lowercase {TOK_HOST.lower()} here\n"
    )
    result = _scan(tmp_path, GOOD_PATTERNS, content)
    assert result.returncode == 1, (
        f"loaded identity patterns must block.\n{result.stdout}\n{result.stderr}")
    for category in ("AGENT-NAME", "AGENT-ABBREV", "HOME-PATH", "FLEET-HOST"):
        assert f"[{category}]" in result.stdout, (
            f"{category} did not fire through the private-file path.\n{result.stdout}")


def test_legitimate_neighbours_do_not_block(tmp_path):
    """The false-positive direction, as load-bearing as the true-positive one.

    Note what is NOT asserted here: an identity name is matched as a SUBSTRING,
    so the same letters inside a longer token do hit. That is this scanner's
    long-standing behavior for the name tier (its patterns carry no word-boundary
    anchors) and it errs toward over-blocking a coined identifier, which is the
    safe direction for this tier. The contextual tier is the one that must not
    fire on ordinary text, and that is what this test pins.
    """
    content = (
        f"a sentence merely containing {TOK_ABBREV} without the preposition\n"
        "an ordinary line naming no identifier at all\n"
    )
    result = _scan(tmp_path, GOOD_PATTERNS, content)
    assert result.returncode == 0, (
        f"a legitimate neighbour must not block.\n{result.stdout}\n{result.stderr}")
    assert "PASS" in result.stdout


def test_env_override_beats_the_default_path(tmp_path, monkeypatch):
    """The override is what lets a runner point at a file it actually has.

    monkeypatch, not a bare os.environ write: an earlier version of this test
    deleted the variable in its own teardown and so cleared a value the RUN had
    set, which silently changed what every later test in the session scanned
    with. A test that edits process-global state must put back exactly what it
    found.
    """
    mod = _load_module()
    override = tmp_path / "elsewhere"
    assert mod.resolve_patterns_path(str(override)) == override
    monkeypatch.setenv("IGOS_PUBLIC_CONTENT_PATTERNS", str(override))
    assert mod.resolve_patterns_path() == override
    monkeypatch.delenv("IGOS_PUBLIC_CONTENT_PATTERNS", raising=False)
    assert mod.resolve_patterns_path() == mod.DEFAULT_PATTERNS_PATH


def test_identity_pattern_lists_are_gone_from_public_source():
    """The leak this change closes: no identity tier is defined in this file.

    Asserted against the source text rather than the imported module, because
    the point is what the PUBLISHED bytes carry, not what the process holds in
    memory.
    """
    src = SCANNER.read_text(encoding="utf-8")
    for name in ("AGENT_NAMES", "AGENT_ABBREV", "HOME_PATH", "FLEET_HOST_BLOCK"):
        assert f"\n{name} = [" not in src, (
            f"{name} is defined as a literal list in public source again. Its "
            f"entries name identities; they belong in the private pattern file.")


def test_every_required_group_is_spliced_into_the_block_tiers():
    """A required group that loads but is never used would be a silent hole."""
    mod = _load_module()
    private = {g: [(f"CAT-{g}", f"zqx{g.lower()}")] for g in mod.REQUIRED_PRIVATE_GROUPS}
    built = mod.build_block_patterns(private)
    categories = [c for c, _ in built]
    for group in mod.REQUIRED_PRIVATE_GROUPS:
        assert f"CAT-{group}" in categories, (
            f"group {group} is required at load time but never reaches the "
            f"BLOCK tiers — it would be loaded and then ignored.")


def test_block_tier_order_is_unchanged_by_the_split():
    """Findings keep their reporting order: private groups splice back in place."""
    mod = _load_module()
    private = {g: [(f"CAT-{g}", f"zqx{g.lower()}")] for g in mod.REQUIRED_PRIVATE_GROUPS}
    categories = [c for c, _ in mod.build_block_patterns(private)]
    assert categories[0] == "CAT-AGENT_NAMES"
    assert categories[1] == "CAT-AGENT_ABBREV"
    assert categories.index("CAT-HOME_PATH") > categories.index("CAT-AGENT_ABBREV")
    assert categories.index("CAT-FLEET_HOST_BLOCK") > categories.index("CAT-HOME_PATH")
    assert categories.index("PERSONA-ATTRIBUTION") > categories.index("CAT-FLEET_HOST_BLOCK")
    assert categories[-1] == "HOST-SHORTHAND"
