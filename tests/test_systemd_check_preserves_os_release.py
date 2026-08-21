# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""systemd's check phase must not destroy the chroot's OS identity file.

WHY THIS EXISTS. The Chapter 8 driver stages a minimal /etc/os-release before
systemd builds, and that stub exists for one reason: to provide ID, which
systemd's own configure step reads for sd-boot resource paths. systemd's check
phase used to write that same file unconditionally with a single NAME line,
discarding ID. Nothing put it back: the driver's stub is guarded by a
file-exists test, so it does not rewrite a file that is present, and the full
identity file only arrives in Chapter 9. Every Chapter 8 package built after
systemd saw the reduced file.

These tests drive the recipe's own os-release fragment — the real text, with
only the absolute path redirected into a temporary directory — rather than
describing what it should do.
"""

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_SH = REPO_ROOT / "packages" / "core" / "systemd" / "build.sh"

# What the Chapter 8 driver stages, verbatim in shape: the point of the stub is
# the ID line, so that is what a regression has to be able to destroy.
CH8_STUB = 'NAME="InterGenOS"\nID=intergenos\nID_LIKE=lfs\nPRETTY_NAME="InterGenOS (Chapter 8 build stub)"\n'


def os_release_fragment(target: Path) -> str:
    """The os-release-writing part of check(), with the absolute path
    redirected. Everything that needs a built source tree — the directory
    change and the test-suite call — is dropped, so the fragment runs
    standalone and measures only the file handling."""
    text = BUILD_SH.read_text()
    start = text.index("check()")
    body = text[start:text.index("\n}\n", start)]

    kept, skip_continuation = [], False
    for line in body.split("\n")[1:]:
        stripped = line.strip()
        if skip_continuation:
            skip_continuation = stripped.endswith("\\")
            continue
        if stripped.startswith("#") or stripped in ("", "set -e"):
            continue
        if stripped.startswith("cd "):
            continue
        if "pkg_run_tests" in stripped:
            skip_continuation = stripped.endswith("\\")
            continue
        kept.append(line)

    fragment = "\n".join(kept)
    assert "os-release" in fragment, f"no os-release handling found in check():\n{body}"
    return fragment.replace("/etc/os-release", str(target))


def run_fragment(target: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", "set -e\n" + os_release_fragment(target)],
        capture_output=True, text=True,
    )


def test_an_existing_identity_file_is_left_exactly_as_it_was(tmp_path):
    """The regression this pins: the staged stub must survive the check phase
    byte for byte, ID line included."""
    target = tmp_path / "os-release"
    target.write_text(CH8_STUB)

    result = run_fragment(target)

    assert result.returncode == 0, result.stdout + result.stderr
    assert target.read_text() == CH8_STUB


def test_the_id_field_the_stub_exists_to_provide_is_not_dropped(tmp_path):
    """Stated separately from byte-equality because ID is the field with a
    named consumer: systemd's configure reads it for sd-boot resource paths."""
    target = tmp_path / "os-release"
    target.write_text(CH8_STUB)

    run_fragment(target)

    assert "ID=intergenos" in target.read_text()


def test_a_genuinely_absent_file_is_created_with_name_and_id(tmp_path):
    """The tests need an os-release to exist, so the fallback still has to
    write one when nothing staged it — and it must carry ID too, or the
    from-nothing case reproduces the defect it replaces."""
    target = tmp_path / "os-release"
    assert not target.exists()

    result = run_fragment(target)

    assert result.returncode == 0, result.stdout + result.stderr
    assert target.exists()
    written = target.read_text()
    assert "NAME=" in written
    assert "ID=intergenos" in written


def test_the_write_is_guarded_by_an_absence_test(tmp_path):
    """Read as text as well as driven: an unguarded write is the shape of the
    defect, and naming it here makes a future edit that removes the guard fail
    for a reason a reader can act on."""
    fragment = os_release_fragment(tmp_path / "os-release")
    assert re.search(r"if\s+\[\s+!\s+-f\s+\S*os-release\s+\]", fragment), fragment
