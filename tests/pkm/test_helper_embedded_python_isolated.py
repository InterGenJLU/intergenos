# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Embedded Python in the helper library never imports from the caller's directory.

A program read from standard input (`python3 -`) runs with the working
directory first on its import path, so a helper started from a directory
that another account can write would import that account's module in place
of the standard library's. Each embedded program in helper-lib.sh and in the
CUDA toolkit helper runs isolated (`python3 -I -`): neither the working
directory, the user's site directory nor a PYTHON* variable reaches the
import path.

Asserted here at each of the four sites: run from a directory that holds a
module named after one the program imports, the function still does its work
and the planted module is never imported.
"""

import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LIBRARY = REPO / "packages/core/intergenos-helper-lib/helper-lib.sh"
CUDA_HELPER = REPO / "packages/compute/cuda-toolkit/helper/igos-install-cuda-toolkit"

# Every embedded program imports json. The planted module notes that it was
# imported, then hands the importer the standard library's module, so the
# function under test completes and its own output can be checked as well.
PLANTED = '''
import os as _os, sys as _sys
with open(_os.path.join(_os.getcwd(), "PLANTED-MARKER"), "a") as _marker:
    _marker.write(__name__ + " imported\\n")
_name = __name__
del _sys.modules[_name]
_saved = list(_sys.path)
_sys.path[:] = [p for p in _sys.path if p not in ("", _os.getcwd())]
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
