# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Python in the helper library never imports from the caller's directory or environment.

A program read from standard input (`python3 -`) runs with the working
directory first on its import path, so a helper started from a directory
that another account can write would import that account's module in place
of the standard library's. Each embedded program in helper-lib.sh and in the
CUDA toolkit helper runs isolated (`python3 -I -`): neither the working
directory, the user's site directory nor a PYTHON* variable reaches the
import path.

The library also runs its own Debian metadata reader by path, for the
verified package lookup and for the upgrade query. A script run by path has
its own directory first on its import path, not the working directory, but
PYTHONPATH and the user's site directory still reach it, so the reader runs
isolated too (`python3 -I`).

Asserted here at each of the four embedded sites: run from a directory that
holds a module named after one the program imports, the function still does
its work and the planted module is never imported. At the two reader sites
the same module is planted once in the working directory and once in a
directory named by PYTHONPATH, and each lookup reads a signed local
repository.

A static case reads both files at the tree under test and requires -I on
every call of the interpreter in them, so a call added to either file without
it fails here whether or not a case above runs it.
"""

import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest

from tests.pkm import test_helper_weak_digest_verify as signed_repo_fixture

REPO = Path(__file__).resolve().parents[2]
LIBRARY = REPO / "packages/core/intergenos-helper-lib/helper-lib.sh"
CUDA_HELPER = REPO / "packages/compute/cuda-toolkit/helper/igos-install-cuda-toolkit"

# Every program imports json. The planted module notes that it was imported,
# and by which program, then hands the importer the standard library's
# module, so the function under test completes and its own output can be
# checked as well.
PLANTED = '''
import os as _os, sys as _sys
_here = _os.path.dirname(_os.path.realpath(__file__))
with open(_os.path.join(_here, "PLANTED-MARKER"), "a") as _marker:
    _marker.write(__name__ + " imported by " + " ".join(_sys.argv[:2]) + "\\n")
_name = __name__
del _sys.modules[_name]
_saved = list(_sys.path)
_sys.path[:] = [p for p in _sys.path if _os.path.realpath(p or _os.curdir) != _here]
import importlib as _importlib
_real = _importlib.import_module(_name)
_sys.path[:] = _saved
_sys.modules[_name] = _real
'''


def _stamp_function_source():
    src = CUDA_HELPER.read_text()
    start = src.index("stamp_release_into_manifest() {")
    end = src.index("\n}\n", start) + 3
    return src[start:end]


def _setup(manifest_dir, payload):
    return (f"source {shlex.quote(str(LIBRARY))}\n"
            f"export IGOS_HELPER_MANIFEST_DIR={shlex.quote(str(manifest_dir))}\n"
            "export PKM_HELPER_INVOCATION=1\n"
            "igos_helper_init sample || exit 90\n"
            "igos_helper_set_version 1.0\n"
            f"igos_helper_record_file {shlex.quote(str(payload))} || exit 91\n")


def _acceptance(tmp, planted):
    record = tmp / "accepted.json"
    script = (f"source {shlex.quote(str(LIBRARY))}\n"
              f"cd {shlex.quote(str(planted))} || exit 89\n"
              f"igos_helper_write_acceptance {shlex.quote(str(record))} sample 1.0 MIT\n")
    return script, lambda: json.loads(record.read_text())["helper"] == "sample"


def _partial(tmp, planted):
    manifest_dir = tmp / "helpers"
    script = (_setup(manifest_dir, tmp / "payload")
              + f"cd {shlex.quote(str(planted))} || exit 89\n"
              "exit 1\n")
    partial = manifest_dir / "sample.manifest.partial"
    return script, lambda: json.loads(partial.read_text())["partial"] is True


def _commit(tmp, planted):
    manifest_dir = tmp / "helpers"
    script = (_setup(manifest_dir, tmp / "payload")
              + f"cd {shlex.quote(str(planted))} || exit 89\n"
              "igos_helper_commit || exit 92\n")
    manifest = manifest_dir / "sample.manifest"
    return script, lambda: json.loads(manifest.read_text())["files"] == [str(tmp / "payload")]


def _stamp(tmp, planted):
    manifest_dir = tmp / "helpers"
    script = (_setup(manifest_dir, tmp / "payload")
              + "igos_helper_commit || exit 92\n"
              + f"mv {shlex.quote(str(manifest_dir / 'sample.manifest'))} "
              + f"{shlex.quote(str(manifest_dir / 'cuda-toolkit.manifest'))} || exit 93\n"
              + "HELPER_RELEASE=7\n"
              + _stamp_function_source()
              + f"cd {shlex.quote(str(planted))} || exit 89\n"
              "stamp_release_into_manifest || exit 94\n")
    manifest = manifest_dir / "cuda-toolkit.manifest"
    return script, lambda: json.loads(manifest.read_text())["release_installed"] == 7


SITES = {
    "igos_helper_write_acceptance": _acceptance,
    "_igos_helper_emit_partial": _partial,
    "igos_helper_commit": _commit,
    "stamp_release_into_manifest": _stamp,
}


@pytest.mark.parametrize("site", sorted(SITES))
def test_a_module_in_the_working_directory_is_not_imported(tmp_path, site):
    planted = tmp_path / "planted"
    planted.mkdir()
    (planted / "json.py").write_text(PLANTED, encoding="utf-8")
    (tmp_path / "payload").write_bytes(b"payload\n")
    script, work_done = SITES[site](tmp_path, planted)
    env = dict(os.environ)
    env.pop("SUDO_USER", None)
    result = subprocess.run(["/bin/bash", "-c", script], env=env, cwd=tmp_path,
                            capture_output=True, text=True, timeout=60)
    expected = 1 if site == "_igos_helper_emit_partial" else 0
    assert result.returncode == expected, result.stdout + result.stderr
    assert work_done(), result.stdout + result.stderr
    marker = planted / "PLANTED-MARKER"
    assert not marker.exists(), f"{site} imported the planted module: {marker.read_text()}"


@pytest.fixture(scope="module")
def repository():
    fixture = signed_repo_fixture.WeakDigestScopedVerifyTests
    fixture.setUpClass()
    try:
        yield fixture
    finally:
        fixture.tearDownClass()


# The reader's two command shapes, each reached through the library function
# that runs it: "select" for the verified lookup, "query" after it for the
# upgrade check. "$1" is the signed repository's address, "$2" its keyring.
def _lookup(repository):
    call = "igos_helper_find_verified_deb_in_packages steam-launcher \"$1\" \"$2\" stable steam '' ''"
    return call, lambda out: out.strip().split("|") == [
        signed_repo_fixture.DEB_FILENAME, "1:1.0.85", signed_repo_fixture.POOL_PATH, repository.deb_sha]


def _query(repository):
    call = "igos_helper_query_deb_upgrade 1.0 steam-launcher \"$1\" \"$2\" stable steam ''"
    return call, lambda out: json.loads(out) == {"version": "1:1.0.85", "comparison": 1}


READER_SITES = {
    "igos_helper_find_verified_deb_in_packages": _lookup,
    "igos_helper_query_deb_upgrade": _query,
}


@pytest.mark.parametrize("route", ["working-directory", "PYTHONPATH"])
@pytest.mark.parametrize("site", sorted(READER_SITES))
def test_the_metadata_reader_never_imports_a_planted_module(tmp_path, repository, site, route):
    planted = tmp_path / "planted"
    planted.mkdir()
    (planted / "json.py").write_text(PLANTED, encoding="utf-8")
    call, work_done = READER_SITES[site](repository)
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    if route == "PYTHONPATH":
        env["PYTHONPATH"] = str(planted)
    cwd = planted if route == "working-directory" else tmp_path
    result = subprocess.run(
        ["/bin/bash", "-c", 'source "$1"\nshift\n' + call + "\n", "_", str(LIBRARY),
         f"http://127.0.0.1:{repository.port}/sha512-a", str(repository.keyring)],
        env=env, cwd=cwd, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert work_done(result.stdout), result.stdout + result.stderr
    marker = planted / "PLANTED-MARKER"
    assert not marker.exists(), f"{site} imported the planted module through {route}: {marker.read_text()}"


# A word naming the interpreter (python, python3, python3.14, with or without a
# directory) is a call unless it sits on a comment line, in a presence probe
# whose output is discarded, in the literal message of igos_helper_emit, or in
# the word list of a loop that only probes each word. A call must carry -I as
# a word of its own among the interpreter's options, before the program.
INTERPRETER = re.compile(r"(?<![\w.])python[0-9.]*(?![\w-])")
COMMENT = re.compile(r"\s*#")
MESSAGE = re.compile(r'\s*igos_helper_emit\s+"[^"`]*"\s*$')
TOOL_LIST = re.compile(r"\s*for\s+(\w+)\s+in\s+[\w. -]+;\s*do\s*$")
DISCARDED = re.compile(r"\s*&?>\s*/dev/null")
OPTIONS_WITH_A_VALUE = {"-W", "-X", "--check-hash-based-pycs"}


def _logical_lines(text):
    """Each line with its continuations joined, and the line number where each joined piece starts."""
    logical, joined, pieces = [], "", []
    for number, line in enumerate(text.splitlines(), 1):
        pieces.append((len(joined), number))
        # A backslash ends a comment rather than continuing it.
        if line.endswith("\\") and not COMMENT.match(line):
            joined += line[:-1] + " "
            continue
        logical.append((joined + line, pieces))
        joined, pieces = "", []
    if pieces:
        logical.append((joined, pieces))
    return logical


def _is_message(line):
    return bool(MESSAGE.match(line)) and "$(" not in line


def _probes_only(logical, index, name):
    """The loop opened at logical[index] uses its variable only in discarded probes and messages."""
    use = re.compile(r"\$\{?%s\b" % re.escape(name))
    probe = re.compile(r'command -v "?\$\{?%s\}?"?%s' % (re.escape(name), DISCARDED.pattern))
    depth = 0
    for line, _ in logical[index + 1:]:
        uses = len(use.findall(line))
        if uses and not (_is_message(line) or (uses == 1 and probe.search(line))):
            return False
        if re.search(r"(?:^|[;\s])do\s*$", line):
            depth += 1
        elif re.match(r"\s*done\b", line):
            if depth == 0:
                return True
            depth -= 1
    return False


def _isolated(rest):
    """-I comes among the interpreter's options, before the program (-, a path, -c or -m)."""
    words = iter(rest.split())
    for word in words:
        if word == "-I":
            return True
        if word in OPTIONS_WITH_A_VALUE:
            next(words, None)
        elif word in ("-", "-c", "-m", "--") or not word.startswith("-"):
            return False
    return False


def _interpreter_calls(text):
    """Every call of the interpreter in a shell file, as (line number, command, isolated)."""
    logical = _logical_lines(text)
    calls = []
    for index, (line, pieces) in enumerate(logical):
        if COMMENT.match(line) or _is_message(line):
            continue
        loop = TOOL_LIST.match(line)
        if loop and _probes_only(logical, index, loop.group(1)):
            continue
        for found in INTERPRETER.finditer(line):
            rest = line[found.end():]
            if line[:found.start()].endswith("command -v ") and DISCARDED.match(rest):
                continue
            number = max(n for offset, n in pieces if offset <= found.start())
            calls.append((number, line.strip(), _isolated(rest)))
    return calls


def test_every_python_call_in_the_helpers_runs_isolated():
    unisolated = []
    for path in (LIBRARY, CUDA_HELPER):
        calls = _interpreter_calls(path.read_text(encoding="utf-8"))
        assert calls, f"no interpreter call was read in {path}"
        unisolated += [f"{path.relative_to(REPO)}:{number}: {command}"
                       for number, command, isolated in calls if not isolated]
    assert not unisolated, "python3 runs without -I:\n" + "\n".join(unisolated)
