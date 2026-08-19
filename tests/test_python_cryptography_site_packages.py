# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""python-cryptography must stage only its own module into site-packages.

WHAT WENT WRONG. The wheel this recipe builds carries the sdist's entire top
level, so `pip install` put tests/, docs/, rust/, vendor/, _cffi_src/, the
Cargo files and three licence files straight into
/usr/lib/python3.14/site-packages — a namespace every Python package on the
system shares. Read from the shipped archive of the first release
(python-cryptography-44.0.0.igos.tar.gz on the published mirror), the archive
holds 115 members under site-packages/tests alone.

The measured consequence: a later package shipping its own generic tests/
directory overwrote site-packages/tests/conftest.py, and the pre-capture
metadata-sync gate refused the image on the resulting co-ownership split.
The wider one is plain namespace capture — on an installed system
`import tests` reached this package's test suite.

These tests drive the recipe's do_install() against a fake DESTDIR with
pip3 stubbed, so they exercise the shipped shell code itself rather than a
description of it.
"""

import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_SH = REPO_ROOT / "packages" / "core" / "python-cryptography" / "build.sh"
PKG_YML = REPO_ROOT / "packages" / "core" / "python-cryptography" / "package.yml"
VERSION = "44.0.0"
SITE = f"usr/lib/python3.14/site-packages"

# Exactly what the shipped archive holds at the top of site-packages.
ARCHIVE_TOP_LEVEL = [
    "cryptography", f"cryptography-{VERSION}.dist-info",
    "tests", "docs", "rust", "vendor", "_cffi_src",
    "Cargo.toml", "Cargo.lock", "CHANGELOG.rst", "CONTRIBUTING.rst",
    "LICENSE", "LICENSE.APACHE", "LICENSE.BSD",
]
DIRS = {"cryptography", f"cryptography-{VERSION}.dist-info",
        "tests", "docs", "rust", "vendor", "_cffi_src"}


def make_destdir(tmp_path, entries):
    dest = tmp_path / "destdir"
    sp = dest / SITE
    sp.mkdir(parents=True)
    for name in entries:
        if name in DIRS:
            (sp / name).mkdir()
            (sp / name / "__init__.py").write_text("")
        else:
            (sp / name).write_text("x")
    # the library's own load-bearing member
    (sp / "cryptography" / "__init__.py").write_text("__version__ = '44.0.0'\n")
    return dest, sp


def run_do_install(tmp_path, dest):
    """Source the recipe and run do_install with pip3 stubbed to a no-op."""
    stub = tmp_path / "stubs"
    stub.mkdir(exist_ok=True)
    (stub / "pip3").write_text("#!/usr/bin/env bash\nexit 0\n")
    (stub / "pip3").chmod(0o755)
    script = textwrap.dedent(f"""
        export PATH="{stub}:$PATH"
        export DESTDIR="{dest}"
        export PKG_VERSION="{VERSION}"
        source "{BUILD_SH}"
        do_install
    """)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def top_level(sp):
    return sorted(p.name for p in sp.iterdir())


class TestStrip:
    def test_only_the_module_and_dist_info_survive(self, tmp_path):
        dest, sp = make_destdir(tmp_path, ARCHIVE_TOP_LEVEL)
        r = run_do_install(tmp_path, dest)
        assert r.returncode == 0, r.stdout + r.stderr
        assert top_level(sp) == sorted(
            ["cryptography", f"cryptography-{VERSION}.dist-info"]), top_level(sp)

    def test_every_removal_is_announced(self, tmp_path):
        dest, _ = make_destdir(tmp_path, ARCHIVE_TOP_LEVEL)
        r = run_do_install(tmp_path, dest)
        for stray in ("tests", "docs", "rust", "vendor", "_cffi_src"):
            assert f"site-packages: {stray}" in r.stdout, r.stdout

    def test_the_library_itself_is_untouched(self, tmp_path):
        dest, sp = make_destdir(tmp_path, ARCHIVE_TOP_LEVEL)
        run_do_install(tmp_path, dest)
        assert (sp / "cryptography" / "__init__.py").read_text().strip() == \
            "__version__ = '44.0.0'"

    def test_absent_strays_are_not_an_error(self, tmp_path):
        """An upstream that stops shipping its sdist top level must not
        break this recipe."""
        dest, sp = make_destdir(
            tmp_path, ["cryptography", f"cryptography-{VERSION}.dist-info"])
        r = run_do_install(tmp_path, dest)
        assert r.returncode == 0, r.stdout + r.stderr
        assert top_level(sp) == sorted(
            ["cryptography", f"cryptography-{VERSION}.dist-info"])


class TestFailClosed:
    def test_an_unknown_top_level_entry_fails_the_build(self, tmp_path):
        """The list is by name, so something new gets a decision — it is
        neither shipped into the shared namespace nor deleted silently."""
        dest, _ = make_destdir(
            tmp_path, ARCHIVE_TOP_LEVEL + ["benchmarks"])
        r = run_do_install(tmp_path, dest)
        assert r.returncode != 0
        assert "benchmarks" in r.stderr, r.stderr

    def test_a_missing_library_fails_the_build(self, tmp_path):
        dest, sp = make_destdir(tmp_path, ARCHIVE_TOP_LEVEL)
        for child in (sp / "cryptography").iterdir():
            child.unlink()
        (sp / "cryptography").rmdir()
        r = run_do_install(tmp_path, dest)
        assert r.returncode != 0
        assert "did not stage" in r.stderr, r.stderr


class TestVerifyPaths:
    def test_declares_concrete_load_bearing_paths(self):
        text = PKG_YML.read_text()
        assert "/usr/lib/python3.14/site-packages/cryptography/__init__.py" in text
        # the bare site-packages directory claim proved nothing about this
        # package landing — it exists whatever happens
        assert "\n  - /usr/lib/python3.14/site-packages\n" not in text
