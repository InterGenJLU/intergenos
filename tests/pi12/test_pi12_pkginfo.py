"""PI-12 — .PKGINFO build-time gate: python-tool contracts (gen-pkginfo / inject-pkginfo).

Covers the tests that drive the two python tools directly (deterministic, no chroot):

  T1  recipe-bearing package emits its REAL tier  (--fallback-tier ignored)
  T2  recipe-less package + --fallback-tier core  -> minimal core .PKGINFO
  T3  recipe-less + NO --fallback-tier             -> rc=2, NOTHING written (the loud
                                                       contract pkg_archive surfaces)
  T6  inject-pkginfo loud detector (edit-4)        -> a real injection => rc=1 + GATE ESCAPE
  T7  recipe-less-core drift guard                 -> the canonical 19 classify INJECT_MIN,
                                                       UNMATCHED stays empty (the build-VM
                                                       enumeration frozen as a regression)

The bash-side gate assertions (2A in pkg_archive, the Step 4.7 pre-squashfs sweep, the empty-set
refuse-to-seal, and the edit-5 backfill) are exercised by test_pi12_gates.sh — they need the real
build env. See README.md.
"""
from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
GEN = _PROJECT_ROOT / "scripts" / "gen-pkginfo.py"
INJECT = _PROJECT_ROOT / "scripts" / "inject-pkginfo.py"
BACKFILL = _PROJECT_ROOT / "scripts" / "backfill-pkginfo.py"

# The recipe-less LFS-Ch8 core set enumerated on the build VM (inject --dry-run:
# 19 recipe-less = core, 0 UNMATCHED, 2026-06-16). Frozen here as the T7 drift ground truth.
CANONICAL_RECIPELESS_CORE = [
    "binutils", "bison", "coreutils", "diffutils", "findutils", "gawk", "gcc", "glibc",
    "grep", "gzip", "m4", "make", "ncurses", "patch", "perl", "sed", "tar", "texinfo",
    "util-linux",
]

# Linux-only tools the inject path shells out to (tar/gzip/date). On a non-Linux host the
# inject-driven tests skip rather than false-fail.
_HAVE_TAR = shutil.which("tar") and shutil.which("gzip")


def _pkginfo_dict(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


def _write_recipe(repo_root: Path, tier: str, name: str, **fields) -> None:
    """Create packages/<tier>/<name>/package.yml with name: == name."""
    d = repo_root / "packages" / tier / name
    d.mkdir(parents=True, exist_ok=True)
    lines = [f"name: {name}"]
    for k, v in fields.items():
        lines.append(f"{k}: {v}")
    (d / "package.yml").write_text("\n".join(lines) + "\n")


def _files_dir(tmp_path: Path) -> Path:
    """A staged-files dir gen-pkginfo can walk for size/filecount."""
    fd = tmp_path / "staged"
    (fd / "usr" / "bin").mkdir(parents=True)
    (fd / "usr" / "bin" / "tool").write_text("#!/bin/sh\n")
    return fd


def _run_gen(args, repo_root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GEN), "--repo-root", str(repo_root), *args],
        capture_output=True, text=True,
    )


# --------------------------------------------------------------------------- T1
def test_t1_recipe_bearing_emits_real_tier(tmp_path):
    """A package WITH a package.yml keeps its real tier; --fallback-tier core is IGNORED."""
    repo = tmp_path / "repo"
    _write_recipe(repo, "desktop", "widget", release=3, description="a widget",
                  license="GPL-3.0")
    fd = _files_dir(tmp_path)
    r = _run_gen(["--name", "widget", "--version", "1.2",
                  "--files-dir", str(fd), "--fallback-tier", "core"], repo)
    assert r.returncode == 0, r.stderr
    info = _pkginfo_dict((fd / ".PKGINFO").read_text())
    assert info["pkgname"] == "widget"
    assert info["pkgver"] == "1.2"
    assert info["pkgrel"] == "3"
    assert info["tier"] == "desktop"        # real tier wins; fallback NOT applied


# --------------------------------------------------------------------------- T2
def test_t2_recipeless_gets_core_fallback(tmp_path):
    """A recipe-less package + --fallback-tier core -> minimal, well-formed core .PKGINFO."""
    repo = tmp_path / "repo"
    (repo / "packages").mkdir(parents=True)        # exists but holds no matching recipe
    fd = _files_dir(tmp_path)
    r = _run_gen(["--name", "glibc", "--version", "2.39",
                  "--files-dir", str(fd), "--fallback-tier", "core"], repo)
    assert r.returncode == 0, r.stderr
    info = _pkginfo_dict((fd / ".PKGINFO").read_text())
    assert info["pkgname"] == "glibc"
    assert info["pkgver"] == "2.39"
    assert info["pkgrel"] == "1"
    assert info["tier"] == "core"


# -------------------------------------------------------------------------- T2b
def test_t2b_force_tier_overrides_toolchain_recipe(tmp_path):
    """Dual-built finding (2026-06-16): glibc/m4/ncurses then carried a toolchain-tier
    recipe under the plain ship name, so plain gen-pkginfo MATCHED it and emitted
    tier=toolchain (--fallback-tier never applies — a recipe matched). But the staged
    archive is the FINAL core build, so the emitted tier is wrong. edit-5's --force-tier
    core is the fix. This is the emission check T7's inject BUCKETING never performed.
    The three real recipes were renamed to -tmp on 2026-08-25; the fixture below keeps
    the shape so the override stays proven for any recipe that takes it later.
    Skips until --force-tier lands (merges with edit-5)."""
    if "--force-tier" not in GEN.read_text():
        pytest.skip("gen-pkginfo --force-tier not present yet (merges with edit-5)")
    repo = tmp_path / "repo"
    _write_recipe(repo, "toolchain", "glibc", release=1, description="GNU C Library")
    fd = _files_dir(tmp_path)
    # WITHOUT --force-tier: the toolchain recipe matches -> tier=toolchain (the mis-stamp)
    r = _run_gen(["--name", "glibc", "--version", "2.39", "--files-dir", str(fd)], repo)
    assert r.returncode == 0, r.stderr
    assert _pkginfo_dict((fd / ".PKGINFO").read_text())["tier"] == "toolchain"
    # WITH --force-tier core: final-core artifact correctly tiered, other fields kept
    r = _run_gen(["--name", "glibc", "--version", "2.39",
                  "--files-dir", str(fd), "--force-tier", "core"], repo)
    assert r.returncode == 0, r.stderr
    info = _pkginfo_dict((fd / ".PKGINFO").read_text())
    assert info["tier"] == "core"
    assert info["pkgdesc"] == "GNU C Library"      # --force-tier keeps every other field


# -------------------------------------------------------------------------- T2c
def test_t2c_ships_as_twin_outranks_plain_name_recipe(tmp_path):
    """Dual-name collision (found 2026-07-30 on the glibc release-4 re-seal):
    when a `<name>-core` twin declares `ships_as: <name>` AND a plain-name
    toolchain recipe also exists, the sealed archive is the final ch8 build,
    so the twin's release/tier must win. Before the fix the exact-name lookup
    matched the toolchain recipe and stamped pkgrel=1/tier=toolchain
    regardless of the twin's release — the `-core` alias never fired because
    a recipe HAD matched."""
    repo = tmp_path / "repo"
    _write_recipe(repo, "toolchain", "glibc", version='"2.43"', release=1,
                  description="GNU C Library")
    d = repo / "packages" / "core" / "glibc-core"
    d.mkdir(parents=True)
    (d / "package.yml").write_text(
        'name: glibc-core\nships_as: glibc\nversion: "2.43"\nrelease: 4\n'
        "description: GNU C Library (final system)\n")
    fd = _files_dir(tmp_path)
    r = _run_gen(["--name", "glibc", "--version", "2.43",
                  "--files-dir", str(fd)], repo)
    assert r.returncode == 0, r.stderr
    info = _pkginfo_dict((fd / ".PKGINFO").read_text())
    assert info["pkgrel"] == "4"            # the twin's release, not the cross build's
    assert info["tier"] == "core"           # the shipped artifact's tier
    # A version-mismatched ships_as declaration must NOT hijack the lookup:
    # the staged version falls back to the exact-name recipe.
    r = _run_gen(["--name", "glibc", "--version", "2.44",
                  "--files-dir", str(fd)], repo)
    assert r.returncode == 0, r.stderr
    assert _pkginfo_dict((fd / ".PKGINFO").read_text())["pkgrel"] == "1"


# --------------------------------------------------------------------------- T3
def test_t3_recipeless_without_fallback_fails_loud(tmp_path):
    """Recipe-less + NO --fallback-tier -> rc=2 and NOTHING written. This is the fault
    pkg_archive now surfaces loudly (it drops the old `|| true` swallow)."""
    repo = tmp_path / "repo"
    (repo / "packages").mkdir(parents=True)
    fd = _files_dir(tmp_path)
    r = _run_gen(["--name", "glibc", "--version", "2.39", "--files-dir", str(fd)], repo)
    assert r.returncode == 2
    assert not (fd / ".PKGINFO").exists()           # no half-written metadata


# --------------------------------------------------------------------------- T6
def _make_archive(path: Path, members: list[tuple[str, str]]) -> None:
    with tarfile.open(path, "w:gz") as tar:
        for arcname, content in members:
            data = content.encode()
            info = tarfile.TarInfo(name=arcname)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


@pytest.mark.skipif(not _HAVE_TAR, reason="inject path needs tar+gzip (Linux build env)")
def test_t6_inject_is_a_loud_detector(tmp_path):
    """edit-4: post-Block-A, a non-empty injection means an archive escaped the build-time
    gate -> inject repacks it (mirror stays installable) but returns 1 + a GATE ESCAPE banner.
    Skipped (not failed) until edit-4 lands in scripts/inject-pkginfo.py."""
    if "GATE ESCAPE" not in INJECT.read_text():
        pytest.skip("edit-4 loud detector not present yet (merges with Block A)")
    repo = tmp_path / "repo"
    (repo / "packages").mkdir(parents=True)
    arch = tmp_path / "archives"
    arch.mkdir()
    excl = tmp_path / "exclude"
    # a recipe-less core archive missing .PKGINFO -> INJECT_MIN -> done>0
    _make_archive(arch / "glibc-2.39.igos.tar.gz", [("usr/bin/x", "x")])
    r = subprocess.run(
        [sys.executable, str(INJECT), "--archive-dir", str(arch),
         "--exclude-dir", str(excl), "--repo-root", str(repo)],
        capture_output=True, text=True,
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "GATE ESCAPE" in (r.stdout + r.stderr)


# --------------------------------------------------------------------------- T7
@pytest.mark.skipif(not _HAVE_TAR, reason="inject path needs tar+gzip (Linux build env)")
def test_t7_recipeless_core_set_stays_core(tmp_path):
    """Drift guard: the canonical 19 must classify INJECT_MIN (recipe-less core, or
    toolchain-dual) — never recipe-bearing INJECT, never UNMATCHED — against the REAL repo
    recipe set. If a future package.yml gives one of the 19 a non-toolchain recipe, or a NEW
    recipe-less core archive appears UNMATCHED, this fails before it mis-stamps tier."""
    arch = tmp_path / "archives"
    arch.mkdir()
    excl = tmp_path / "exclude"
    for name in CANONICAL_RECIPELESS_CORE:
        _make_archive(arch / f"{name}-9.9.igos.tar.gz", [("usr/bin/x", "x")])
    r = subprocess.run(
        [sys.executable, str(INJECT), "--archive-dir", str(arch),
         "--exclude-dir", str(excl), "--repo-root", str(_PROJECT_ROOT), "--dry-run"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    out = r.stdout
    # UNMATCHED must be empty for the canonical set
    assert "UNMATCHED  : 0" in out, f"a canonical core pkg went UNMATCHED:\n{out}"
    # every canonical name must appear in the INJECT_MIN minimal: list
    minimal_line = next((l for l in out.splitlines() if l.strip().startswith("minimal:")), "")
    for name in CANONICAL_RECIPELESS_CORE:
        assert f"'{name}'" in minimal_line, (
            f"{name} did not classify INJECT_MIN (recipe-less core) — fallback-tier drift:\n{out}"
        )


# --------------------------------------------------------------------------- T9
def _tier_of(archive: Path) -> str:
    with tarfile.open(archive) as t:
        m = next(x for x in t.getmembers() if x.name.endswith(".PKGINFO"))
        return _pkginfo_dict(t.extractfile(m).read().decode())["tier"]


def _members(archive: Path) -> list[str]:
    with tarfile.open(archive) as t:
        return sorted(x.name for x in t.getmembers())


@pytest.mark.skipif(not _HAVE_TAR, reason="backfill needs tar+gzip (Linux build env)")
def test_t9_backfill_missing_only_idempotent_dualbuilt(tmp_path):
    """edit-5: in-build post-python backfill enforces the 3 acceptance criteria + dual-built routing.
    Skips until backfill-pkginfo.py lands (merges with edit-5)."""
    if not BACKFILL.exists():
        pytest.skip("backfill-pkginfo.py not present yet (merges with edit-5)")
    repo = tmp_path / "repo"
    _write_recipe(repo, "toolchain", "glibc", release=1, description="GNU C Library")
    _write_recipe(repo, "desktop", "widget", release=1)
    arch = tmp_path / "archives"
    arch.mkdir()
    _make_archive(arch / "gcc-13.2.igos.tar.gz", [("usr/bin/gcc", "x")])         # recipe-less
    _make_archive(arch / "glibc-2.39.igos.tar.gz", [("usr/lib/libc.so", "x")])   # dual-built
    _make_archive(arch / "widget-1.0.igos.tar.gz", [                             # already good
        ("usr/bin/widget", "x"),
        ("./.PKGINFO", "pkgname=widget\npkgver=1.0\npkgrel=1\ntier=desktop\n"),
    ])
    r = subprocess.run(
        [sys.executable, str(BACKFILL), "--archive-dir", str(arch),
         "--repo-root", str(repo)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _tier_of(arch / "gcc-13.2.igos.tar.gz") == "core"          # recipe-less -> core
    assert _tier_of(arch / "glibc-2.39.igos.tar.gz") == "core"        # dual-built FORCED core
    assert _tier_of(arch / "widget-1.0.igos.tar.gz") == "desktop"     # untouched, NOT clobbered
    assert "usr/bin/gcc" in _members(arch / "gcc-13.2.igos.tar.gz")   # lossless: member kept
    # idempotent: a 2nd run finds everything well-formed and stamps nothing
    r2 = subprocess.run(
        [sys.executable, str(BACKFILL), "--archive-dir", str(arch),
         "--repo-root", str(repo)], capture_output=True, text=True)
    assert "stamped 0" in r2.stdout, r2.stdout


@pytest.mark.skipif(not _HAVE_TAR, reason="backfill needs tar+gzip (Linux build env)")
def test_t9b_backfill_fail_loud_on_unparseable_name(tmp_path):
    """edit-5 FAIL-LOUD: a name with no parseable -<version> can't be stamped -> abort (rc=1),
    never seal a metadata-less archive silently."""
    if not BACKFILL.exists():
        pytest.skip("backfill-pkginfo.py not present yet (merges with edit-5)")
    repo = tmp_path / "repo"
    (repo / "packages").mkdir(parents=True)
    arch = tmp_path / "archives"
    arch.mkdir()
    _make_archive(arch / "noversion.igos.tar.gz", [("usr/bin/x", "x")])  # no -<digit> boundary
    r = subprocess.run(
        [sys.executable, str(BACKFILL), "--archive-dir", str(arch),
         "--repo-root", str(repo)], capture_output=True, text=True)
    assert r.returncode == 1, r.stdout + r.stderr
