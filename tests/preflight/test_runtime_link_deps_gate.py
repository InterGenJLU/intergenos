# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
"""Wedge tests for scripts/check-runtime-link-deps.py.

The gate's job: a library a shipped object links must be pulled onto the
machine by that object's own package, through its declared runtime
dependencies. The defect that produced it is on the record — on the R001.1
install of 2026-08-22 every ROCm engine binary failed to start because
librocprofiler-register.so.0 and libroctx64.so.4 had no provider, and no
installed package declared the two mirror packages that ship them.

These wedges exercise the script against synthetic roots so they run anywhere,
with no ROCm and no built image:

  * RED — a consumer library NEEDs a soname nothing provides. The gate fails
    AND its message names the recipe that ships it, read from that recipe's own
    verify_paths, so the reader is told the fix rather than the symptom.
  * GREEN — the provider is present and declared: the gate passes.
  * UNDECLARED, failing — the provider is present but owned by a package the
    consumer does not declare, and the package database records it as installed
    only as some other package's dependency. `pkm autoremove` may take it, so
    this fails.
  * UNDECLARED, reported and not failed — the same shape with the provider
    installed as a base/manual package: printed, counted, exit 0. A gate that
    hid this class would teach nobody; a gate that failed on it would fire on
    every recipe that leans on the base toolchain and be switched off.
  * FAIL-CLOSED — a root with no dynamic ELF objects under the scanned prefix
    audited nothing, which is a failure and not a pass (the pi12-sweep rule
    igos-build/needclosure.py already applies to the sealed image).

The synthetic ELFs come from the needclosure wedge's own make_elf: one
ELF-crafting implementation in the tree, so the two gates can never disagree
about what a dynamic object looks like.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check-runtime-link-deps.py"


def _load_make_elf():
    """The needclosure wedge's ELF crafter, imported rather than copied."""
    path = REPO_ROOT / "tests" / "igos_build" / "test_needclosure.py"
    spec = importlib.util.spec_from_file_location("_needclosure_wedge", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod.make_elf


make_elf = _load_make_elf()


RECIPE = """\
name: {name}
version: "1.0"
release: 1
description: synthetic recipe for the runtime-link-deps wedge
license: MIT
tier: compute
build_style: custom
source:
  - url: https://example.invalid/{name}-1.0.tar.gz
    filename: {name}-1.0.tar.gz
    sha256: "0000000000000000000000000000000000000000000000000000000000000000"
dependencies:
  build: []
  host: []
  runtime: {runtime}
{verify}
"""


def write_recipe(repo: Path, name: str, runtime=(), verify_paths=()):
    d = repo / "packages" / "compute" / name
    d.mkdir(parents=True, exist_ok=True)
    rt = ("\n" + "\n".join(f"  - {r}" for r in runtime)) if runtime else "[]"
    vp = ""
    if verify_paths:
        vp = "verify_paths:\n" + "\n".join(f"  - {v}" for v in verify_paths)
    (d / "package.yml").write_text(
        RECIPE.format(name=name, runtime=rt, verify=vp))


def write_elf(root: Path, rel: str, needed=()):
    p = root / rel.lstrip("/")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(make_elf(needed=tuple(needed)))
    p.chmod(0o755)
    return p


def write_ld_conf(root: Path):
    etc = root / "etc"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "ld.so.conf").write_text("/opt/synth/lib\n")


def run_gate(repo: Path, root: Path, prefix="/opt/synth", pkgdb=None):
    cmd = [sys.executable, str(SCRIPT), "--repo", str(repo),
           "--root", str(root), "--prefix", prefix]
    cmd += ["--pkgdb", str(pkgdb) if pkgdb else str(root / "no-such-db")]
    return subprocess.run(cmd, capture_output=True, text=True)


def base_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "packages").mkdir(parents=True)
    return repo


# ---------------------------------------------------------------------------
# RED: the measured defect's shape
# ---------------------------------------------------------------------------

def test_unresolved_soname_fails_and_names_the_shipping_recipe(tmp_path):
    repo = base_repo(tmp_path)
    write_recipe(repo, "consumer-lib")
    write_recipe(repo, "provider-lib",
                 verify_paths=["/opt/synth/lib/libprovider.so"])

    root = tmp_path / "root"
    write_ld_conf(root)
    write_elf(root, "/opt/synth/lib/libconsumer.so.1",
              needed=["libprovider.so.4"])

    res = run_gate(repo, root)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "UNRESOLVED" in res.stderr
    assert "libprovider.so.4" in res.stderr
    # the fix, not just the symptom
    assert "provider-lib" in res.stderr


def test_green_once_the_provider_is_present(tmp_path):
    repo = base_repo(tmp_path)
    write_recipe(repo, "consumer-lib", runtime=["provider-lib"])
    write_recipe(repo, "provider-lib",
                 verify_paths=["/opt/synth/lib/libprovider.so"])

    root = tmp_path / "root"
    write_ld_conf(root)
    write_elf(root, "/opt/synth/lib/libconsumer.so.1",
              needed=["libprovider.so.4"])
    write_elf(root, "/opt/synth/lib/libprovider.so.4")

    res = run_gate(repo, root)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "PASS" in res.stdout


# ---------------------------------------------------------------------------
# FAIL-CLOSED: an empty audit is a failed audit
# ---------------------------------------------------------------------------

def test_zero_objects_is_a_failure_not_a_pass(tmp_path):
    repo = base_repo(tmp_path)
    write_recipe(repo, "consumer-lib")

    root = tmp_path / "root"
    write_ld_conf(root)
    (root / "opt" / "synth" / "lib").mkdir(parents=True)
    (root / "opt" / "synth" / "lib" / "README").write_text("not an ELF\n")

    res = run_gate(repo, root)
    assert res.returncode == 1
    assert "ZERO dynamic ELF objects" in res.stderr


def test_missing_recipe_tree_is_an_environment_error(tmp_path):
    root = tmp_path / "root"
    write_ld_conf(root)
    write_elf(root, "/opt/synth/lib/libconsumer.so.1")
    res = run_gate(tmp_path / "no-repo", root)
    assert res.returncode == 2


# ---------------------------------------------------------------------------
# DECLARATION half — needs a package database, so it builds a real one
# ---------------------------------------------------------------------------

def _synthetic_db(tmp_path: Path, entries):
    """entries: [(pkg, [paths], install_reason)] -> a real pkm database.

    Paths are registered POSIX-relative, which is how the package database
    stores what a package owns ("usr/bin/bash", not "/usr/bin/bash").
    """
    sys.path.insert(0, str(REPO_ROOT))
    from pkm.database import PackageDB

    dbp = tmp_path / "pkm.db"
    db = PackageDB(db_path=str(dbp))
    for name, paths, reason in entries:
        pid = db.add_installed(name, "1.0", tier="compute")
        db.add_files(pid, [p.lstrip("/") for p in paths])
        db.set_install_reason(name, reason)
    db.close()
    return dbp


def _declaration_root(tmp_path):
    root = tmp_path / "root"
    write_ld_conf(root)
    write_elf(root, "/opt/synth/lib/libconsumer.so.1",
              needed=["libprovider.so.4"])
    write_elf(root, "/opt/synth/lib/libprovider.so.4")
    return root


def test_undeclared_provider_installed_as_a_dependency_fails(tmp_path):
    repo = base_repo(tmp_path)
    write_recipe(repo, "consumer-lib")          # declares nothing
    write_recipe(repo, "provider-lib",
                 verify_paths=["/opt/synth/lib/libprovider.so"])
    root = _declaration_root(tmp_path)
    dbp = _synthetic_db(tmp_path, [
        ("consumer-lib", ["/opt/synth/lib/libconsumer.so.1"], "manual"),
        ("provider-lib", ["/opt/synth/lib/libprovider.so.4"], "dependency"),
    ])

    res = run_gate(repo, root, pkgdb=dbp)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "UNDECLARED" in res.stderr
    assert "autoremove" in res.stderr


def test_undeclared_base_provider_is_reported_not_failed(tmp_path):
    repo = base_repo(tmp_path)
    write_recipe(repo, "consumer-lib")          # still declares nothing
    write_recipe(repo, "provider-lib",
                 verify_paths=["/opt/synth/lib/libprovider.so"])
    root = _declaration_root(tmp_path)
    dbp = _synthetic_db(tmp_path, [
        ("consumer-lib", ["/opt/synth/lib/libconsumer.so.1"], "manual"),
        ("provider-lib", ["/opt/synth/lib/libprovider.so.4"], "manual"),
    ])

    res = run_gate(repo, root, pkgdb=dbp)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "UNDECLARED-BASE" in res.stderr
    assert "reported-not-failed" in res.stdout


def test_declared_provider_passes_the_declaration_half(tmp_path):
    repo = base_repo(tmp_path)
    write_recipe(repo, "consumer-lib", runtime=["provider-lib"])
    write_recipe(repo, "provider-lib",
                 verify_paths=["/opt/synth/lib/libprovider.so"])
    root = _declaration_root(tmp_path)
    dbp = _synthetic_db(tmp_path, [
        ("consumer-lib", ["/opt/synth/lib/libconsumer.so.1"], "manual"),
        ("provider-lib", ["/opt/synth/lib/libprovider.so.4"], "dependency"),
    ])

    res = run_gate(repo, root, pkgdb=dbp)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "resolution + declaration" in res.stdout


def test_declaration_half_is_reported_not_performed_without_a_database(tmp_path):
    repo = base_repo(tmp_path)
    write_recipe(repo, "consumer-lib")
    write_recipe(repo, "provider-lib",
                 verify_paths=["/opt/synth/lib/libprovider.so"])
    root = _declaration_root(tmp_path)

    res = run_gate(repo, root)          # no database
    assert res.returncode == 0, res.stdout + res.stderr
    assert "NOT PERFORMED" in res.stderr
    assert "resolution only" in res.stdout
