# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A recipe does not ship a command link another package owns as a file.

pkm refuses an install that would change the kind of a file another
installed package owns
(tests/pkm/test_install_refuses_a_kind_change_at_another_packages_path.py),
and across the lineage's archives the paths two packages ship as different
kinds of file are fixed at their recipes:

* /usr/bin/typer — erlang's upstream install links it to its own type
  checker, /usr/lib/erlang/bin/typer, while the Python `typer` package ships
  its command line there as a regular file. On a build root the Python
  script was written through erlang's link into erlang's own tool.

The command name stays with the package that ships a program by that name:
erlang's tool stays at /usr/lib/erlang/bin/typer. These tests run each
recipe's own do_install against a stand-in `make` that lays down what the
upstream install lays down, and read what the recipe leaves in DESTDIR.
"""
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _run_do_install(tmp_path, recipe, fake_make):
    """Run <recipe>/build.sh's do_install with `make` replaced by the given
    script; return DESTDIR."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    make = bindir / "make"
    make.write_text("#!/bin/bash\nset -e\n" + fake_make)
    make.chmod(0o755)
    destdir = tmp_path / "destdir"
    destdir.mkdir()
    src = tmp_path / "src"
    src.mkdir()
    env = dict(os.environ, DESTDIR=str(destdir),
               PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}")
    result = subprocess.run(
        ["bash", "-c", 'set -e; . "$1"; do_install', "do_install",
         str(REPO / "packages" / recipe / "build.sh")],
        cwd=src, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    return destdir


# Erlang/OTP's `make install` (its Install script with --prefix=/usr): the
# tools live in /usr/lib/erlang/bin and /usr/bin carries links to them.
ERLANG_MAKE = r'''
d="${DESTDIR:?}"
mkdir -p "$d/usr/lib/erlang/bin" "$d/usr/bin"
for tool in erl erlc escript dialyzer typer ct_run; do
    printf '#!/bin/sh\necho %s\n' "$tool" > "$d/usr/lib/erlang/bin/$tool"
    ln -s "../lib/erlang/bin/$tool" "$d/usr/bin/$tool"
done
'''


def test_erlang_does_not_install_usr_bin_typer(tmp_path):
    destdir = _run_do_install(tmp_path, "extra/erlang", ERLANG_MAKE)

    assert not os.path.lexists(destdir / "usr" / "bin" / "typer"), (
        "/usr/bin/typer is the Python typer package's command")
    assert (destdir / "usr" / "lib" / "erlang" / "bin" / "typer").is_file()
    # Every other link upstream installs stays.
    for tool in ("erl", "erlc", "escript", "dialyzer", "ct_run"):
        link = destdir / "usr" / "bin" / tool
        assert link.is_symlink(), tool
        assert os.readlink(link) == f"../lib/erlang/bin/{tool}"
