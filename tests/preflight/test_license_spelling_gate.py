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
}


def test_the_real_tree_passes():
    """The sweep's own gate, run against the tree it swept."""
    result = _run(REPO)
    assert result.returncode == 0, result.stdout + result.stderr


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
