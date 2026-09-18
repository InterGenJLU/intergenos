# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""RED/GREEN tests for scripts/check-license-spelling.py.

The gate refuses the British spelling of the license word anywhere this project
speaks in its own voice, and exempts — by name, one entry per piece of somebody
else's text, with the reason beside it — the identifiers it does not own.

Each synthetic case builds a small git repository in a tempdir and runs the gate
against it with --root, so the gate is exercised as the program it is rather
than as an importable function. One case runs it against the REAL tree, which is
what keeps the tree swept after this change lands: a new British spelling in a
document, a comment or a recipe fails this test.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GATE = REPO / "scripts" / "check-license-spelling.py"


def _repo(tmp_path, files):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def _run(root, *args):
    return subprocess.run([sys.executable, str(GATE), "--root", str(root), *args],
                          capture_output=True, text=True)


# The exemptions' own files, minimal but at their real paths and carrying their
# real markers, so a synthetic repository can exercise them.
EXEMPT_FILES = {
    "packages/core/sof-firmware/build.sh":
        "install -m644 LICENCE.Intel LICENCE.NXP \\\n",
    "igos-build/license_bundle.py":
        'LICENSE_SUBDIRS = {"licenses", "license-files", "licence-files"}\n'
        '# LICENCE (British), COPYING\n',
    "scripts/pkg-functions.sh":
        "  -o -iname 'LICENCE' -o -iname 'LICENCE.*' \\\n",
    "packages/extra/wxwidgets/package.yml":
        "# upstream license file is docs/licence.txt in the source tarball.\n",
    "docs/research/packaging/nvidia_driver_open_packaging_2026-04-20.md":
        "- redistributable via `linux-firmware` under its own LICENCE\n",
    "docs/governance/license-policy.md":
        '| linux-firmware | "Various" — see /lib/firmware/LICENCE.* | No |\n',
    "config/spdx-license-list.json":
        '{"_about": "Identifier sets from the official SPDX licence list."}\n',
    "assets/theming/extensions/AlphabeticalAppGrid@stuarthayhurst.zip":
        "PK\x00\x00binary-ish\n",
    # The gate and this file are exempt as whole files, and the gate fails
    # closed when a named exemption's file is missing, so a synthetic repo has
    # to carry them at their real paths.
    "scripts/check-license-spelling.py":
        "BRITISH = 'licence'  # the spelling this gate looks for\n",
    "tests/preflight/test_license_spelling_gate.py":
        "FIXTURE = 'The licence is yours.'\n",
}

# The canary the two whole-file exemptions promise. Both files must carry the
# spelling to do their job, so neither can be gated line by line — but a NEW
# line in either is a change somebody makes on purpose, and this is where they
# record it. A wrong number here is not a style complaint: it means the one
# place the gate cannot see grew, and nobody said so.
SELF_EXEMPT_LINE_COUNTS = {
    "scripts/check-license-spelling.py": 22,
    "tests/preflight/test_license_spelling_gate.py": 32,
}


def test_the_real_tree_passes():
    """The sweep's own gate, run against the tree it swept."""
    result = _run(REPO)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("name", sorted(SELF_EXEMPT_LINE_COUNTS))
def test_the_whole_file_exemptions_have_not_grown_quietly(name):
    """The two files the gate cannot check are counted instead."""
    import re
    british = re.compile(r"\blicenc(e|es|ed|ing)\b", re.IGNORECASE)
    text = (REPO / name).read_text(encoding="utf-8")
    hits = sum(1 for line in text.splitlines() if british.search(line))
    assert hits == SELF_EXEMPT_LINE_COUNTS[name], (
        f"{name} carries {hits} lines with the British spelling, recorded "
        f"{SELF_EXEMPT_LINE_COUNTS[name]}. If the new one belongs there — a new "
        f"exemption's marker, a new fixture — record it here in the same commit.")


def test_a_new_prose_line_is_refused(tmp_path):
    root = _repo(tmp_path, dict(EXEMPT_FILES,
                                **{"docs/new.md": "The licence is yours.\n"}))
    result = _run(root)
    assert result.returncode == 1
    assert "docs/new.md:1" in result.stdout


@pytest.mark.parametrize("name", sorted(EXEMPT_FILES))
def test_each_named_exemption_is_accepted(tmp_path, name):
    """Every exempted identifier, one test each, so a broken exemption names
    itself instead of hiding in a pass over the set."""
    root = _repo(tmp_path, EXEMPT_FILES)
    result = _run(root)
    assert result.returncode == 0, f"{name} was refused:\n{result.stdout}"


def test_an_exemption_does_not_cover_a_new_sentence_in_the_same_file(tmp_path):
    """The exemptions are per line and per marker, not per file: a file that
    carries an upstream name may not become a place where anything goes."""
    files = dict(EXEMPT_FILES)
    files["packages/extra/wxwidgets/package.yml"] += \
        "# our own note about the licence terms\n"
    root = _repo(tmp_path, files)
    result = _run(root)
    assert result.returncode == 1
    assert "packages/extra/wxwidgets/package.yml:2" in result.stdout


def test_a_tree_line_that_names_two_upstream_files_is_accepted(tmp_path):
    """The firmware recipe installs two upstream files on one line. Each is its
    own named exemption, and the markers come out longest first, so neither one
    leaves a fragment that reads as this project's own word."""
    root = _repo(tmp_path, EXEMPT_FILES)
    result = _run(root)
    assert result.returncode == 0, result.stdout
    assert "sof-firmware" not in result.stdout


def test_a_tree_line_keeps_only_the_names_it_quotes(tmp_path):
    """The masking case, on the tree surface: a line that quotes an upstream
    name may not also use the word this project does not use. Until the two
    surfaces were aligned this line passed, because an exempted line was
    exempt whole."""
    files = dict(EXEMPT_FILES)
    files["packages/extra/wxwidgets/package.yml"] = (
        "# upstream license file is docs/licence.txt, and our own licence note "
        "sits beside it.\n")
    root = _repo(tmp_path, files)
    result = _run(root)
    assert result.returncode == 1
    assert "packages/extra/wxwidgets/package.yml:1" in result.stdout


def test_a_stale_exemption_is_a_setup_error_not_a_pass(tmp_path):
    """An exemption whose named text is gone has stopped covering anything. It
    is re-made or dropped deliberately; it is never left to rot, because a rule
    that quietly covers nothing is indistinguishable from one that works."""
    files = dict(EXEMPT_FILES)
    files["packages/core/sof-firmware/build.sh"] = "install -m644 README\n"
    root = _repo(tmp_path, files)
    result = _run(root)
    assert result.returncode == 2, result.stdout
    assert "SETUP ERROR" in result.stdout
    assert "sof-firmware-upstream-licence-files" in result.stdout


def test_a_commit_message_in_the_range_is_scanned(tmp_path):
    root = _repo(tmp_path, EXEMPT_FILES)
    env_args = ["-c", "user.email=t@example.invalid", "-c", "user.name=t"]
    subprocess.run(["git", *env_args, "commit", "-qm", "base"], cwd=root, check=True)
    (root / "docs/ok.md").parent.mkdir(parents=True, exist_ok=True)
    (root / "docs/ok.md").write_text("nothing to see\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", *env_args, "commit", "-qm",
                    "docs: the licence wording"], cwd=root, check=True)
    result = _run(root, "--range", "HEAD~1..HEAD")
    assert result.returncode == 1
    assert "commit " in result.stdout
    assert "licence wording" in result.stdout


def test_a_word_that_merely_contains_the_letters_is_not_a_hit(tmp_path):
    root = _repo(tmp_path, dict(EXEMPT_FILES,
                                **{"docs/new.md": "licenceless is not a word "
                                                  "anyone writes, but it is not "
                                                  "this word either.\n"}))
    result = _run(root)
    assert result.returncode == 0, result.stdout


# Three lines from real commit messages, quoted exactly as they were written:
# each is a true sentence that names an upstream identifier while explaining
# why that identifier is exempt. A push was refused for all three on
# 2026-09-17, because the message scan applied the pattern and nothing else.
MESSAGE_LINES_NAMING_AN_UPSTREAM_IDENTIFIER = [
    "alternation, its explaining comment and the licence-files metadata directory in",
    "and the reason: the file names in Intel's firmware tarball, the licence-files",
    "find expression that match files literally called LICENCE, the wxWidgets tarball",
]


def _committed(tmp_path, message):
    """A synthetic repository whose newest commit carries `message`."""
    root = _repo(tmp_path, EXEMPT_FILES)
    env_args = ["-c", "user.email=t@example.invalid", "-c", "user.name=t"]
    subprocess.run(["git", *env_args, "commit", "-qm", "base"], cwd=root, check=True)
    note = root / "docs" / "note.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("nothing to see\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", *env_args, "commit", "-qm", message], cwd=root, check=True)
    return root


@pytest.mark.parametrize("line", MESSAGE_LINES_NAMING_AN_UPSTREAM_IDENTIFIER)
def test_a_message_line_carrying_a_named_marker_is_accepted(tmp_path, line):
    """A commit message that names an exempted identifier is a true statement
    about somebody else's bytes, and the exemption reaches it."""
    root = _committed(tmp_path, "docs: why the exemptions exist\n\n" + line + "\n")
    result = _run(root, "--range", "HEAD~1..HEAD")
    assert result.returncode == 0, result.stdout


def test_a_new_sentence_beside_an_exempt_line_in_the_same_message_is_refused(tmp_path):
    """Per line and per marker in a message too: naming an upstream identifier
    on one line does not open the rest of the message."""
    root = _committed(
        tmp_path,
        "docs: why the exemptions exist\n\n"
        "find expression that match files literally called LICENCE, the wxWidgets tarball\n"
        "and our own note about the licence terms\n")
    result = _run(root, "--range", "HEAD~1..HEAD")
    assert result.returncode == 1
    assert "our own note about the licence terms" in result.stdout
    assert "literally called LICENCE" not in result.stdout


def test_a_marker_is_matched_exactly_so_the_lowercase_word_is_not_a_name(tmp_path):
    """`LICENCE` is an upstream file name; `licence` is the word this project
    does not use. The marker is matched as written, so the second is refused
    even though its letters sit inside the first."""
    root = _committed(tmp_path, "docs: a sentence about the licence policy\n")
    result = _run(root, "--range", "HEAD~1..HEAD")
    assert result.returncode == 1
    assert "licence policy" in result.stdout


def test_a_quoted_name_does_not_exempt_the_rest_of_its_own_line(tmp_path):
    """The exemption covers the marker's own characters, not the sentence they
    sit in. A line may name LICENCE and may not also use the word this project
    does not use, however close together the two are."""
    root = _committed(
        tmp_path,
        "docs: files literally called LICENCE, and our licence wording beside them\n")
    result = _run(root, "--range", "HEAD~1..HEAD")
    assert result.returncode == 1
    assert "our licence wording" in result.stdout


def test_a_marker_is_taken_out_whole_so_no_shorter_one_leaves_a_hit(tmp_path):
    """LICENCE.Intel is removed as itself rather than as LICENCE plus .Intel,
    so a line naming two upstream files is accepted for both of them."""
    root = _committed(
        tmp_path,
        "build: install LICENCE.Intel and LICENCE.NXP from the firmware tarball\n")
    result = _run(root, "--range", "HEAD~1..HEAD")
    assert result.returncode == 0, result.stdout


def test_a_whole_file_exemption_exempts_nothing_in_a_message(tmp_path):
    """A whole-file exemption is a statement about one file's bytes. A message
    is not that file, so naming the file does not carry the exemption along."""
    root = _committed(
        tmp_path,
        "test: scripts/check-license-spelling.py still carries the licence spelling\n")
    result = _run(root, "--range", "HEAD~1..HEAD")
    assert result.returncode == 1
