# SPDX-License-Identifier: GPL-3.0-or-later
# Wedge suite for the bash-tier lib32 guards (GE arc, Wave 1):
#   - lib32_assert_only_lib32 (scripts/lib32-env.sh) — the only-lib32
#     staged-payload assertion, hardened per Wave-1 adversarial-verify finding W1-b (a `-type f`
#     filter let a planted stray SYMLINK ship; the guard now matches every
#     non-directory).
#   - lib32_env_scrub (scripts/pkg-functions.sh) — the driver-side
#     marker-keyed env-leak scrub, added per Wave-1 adversarial-verify finding W1-a (a failed
#     do_install skips the trailing lib32_env_end under errexit).
#   - the lib32-glibc full-payload allowlist sweep (same W1-b class).
#
# Each wedge drives the REAL shipped bash (sourced from the tree, never a
# re-implementation) against a synthetic DESTDIR.

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LIB32_ENV = REPO / "scripts" / "lib32-env.sh"
PKG_FUNCTIONS = REPO / "scripts" / "pkg-functions.sh"


def run_bash(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True
    )


def assert_snippet(destdir: Path) -> str:
    """Source the real profile (exports are harmless in the subprocess) and
    run the real assertion against a synthetic DESTDIR."""
    return (
        f'source "{LIB32_ENV}" >/dev/null 2>&1; '
        f'DESTDIR="{destdir}"; lib32_assert_only_lib32'
    )


def scrub_snippet(pre: str) -> str:
    """Extract ONLY the lib32_env_scrub function text from the real
    pkg-functions.sh (sourcing the whole library has side effects), then run
    `pre` and the scrub."""
    return (
        f'source <(sed -n "/^lib32_env_scrub()/,/^}}/p" "{PKG_FUNCTIONS}"); '
        f"{pre}; lib32_env_scrub"
    )


# ---------------------------------------------------------------- W1-b ----

def test_clean_lib32_payload_passes(tmp_path):
    d = tmp_path / "dest"
    (d / "usr/lib32/pkgconfig").mkdir(parents=True)
    (d / "usr/lib32/libz.so.1").write_text("elf")
    (d / "usr/lib32/pkgconfig/zlib.pc").write_text("pc")
    r = run_bash(assert_snippet(d))
    assert r.returncode == 0, r.stderr


def test_stray_regular_file_fails(tmp_path):
    d = tmp_path / "dest"
    (d / "usr/lib32").mkdir(parents=True)
    (d / "usr/lib32/libz.so.1").write_text("elf")
    (d / "usr/include").mkdir(parents=True)
    (d / "usr/include/zlib.h").write_text("hdr")
    r = run_bash(assert_snippet(d))
    assert r.returncode != 0
    assert "FATAL" in r.stderr


def test_stray_symlink_fails(tmp_path):
    # The verify's exact W1-b plant: a symlink OUTSIDE the lib32 tree previously
    # shipped unflagged because the guard matched regular files only.
    d = tmp_path / "dest"
    (d / "usr/lib32").mkdir(parents=True)
    (d / "usr/lib32/libz.so.1").write_text("elf")
    (d / "usr/lib").mkdir(parents=True)
    (d / "usr/lib/libz.so").symlink_to("../lib32/libz.so.1")
    r = run_bash(assert_snippet(d))
    assert r.returncode != 0, "stray symlink outside /usr/lib32 must FATAL"
    assert "FATAL" in r.stderr


def test_stray_fifo_fails(tmp_path):
    # Breadth of the non-directory match: any special file outside the
    # lib32 tree halts too (device nodes need privilege to create; a FIFO
    # proves the !-type-d breadth without it).
    import os

    d = tmp_path / "dest"
    (d / "usr/lib32").mkdir(parents=True)
    (d / "usr/lib32/libz.so.1").write_text("elf")
    (d / "etc").mkdir(parents=True)
    os.mkfifo(d / "etc" / "stray-fifo")
    r = run_bash(assert_snippet(d))
    assert r.returncode != 0
    assert "FATAL" in r.stderr


def test_symlink_inside_lib32_passes(tmp_path):
    # Symlinks are normal INSIDE the lib32 tree (libfoo.so -> libfoo.so.N).
    d = tmp_path / "dest"
    (d / "usr/lib32").mkdir(parents=True)
    (d / "usr/lib32/libz.so.1").write_text("elf")
    (d / "usr/lib32/libz.so").symlink_to("libz.so.1")
    r = run_bash(assert_snippet(d))
    assert r.returncode == 0, r.stderr


def test_stray_empty_directory_fails(tmp_path):
    # The Wave-1 re-cert's flagged edge: a bare empty directory outside the
    # allowlist used to pass (directories were blanket-permitted). The
    # guard's contract is nothing-outside-the-allowlist, dirs included.
    d = tmp_path / "dest"
    (d / "usr/lib32").mkdir(parents=True)
    (d / "usr/lib32/libz.so.1").write_text("elf")
    (d / "usr/include/stray").mkdir(parents=True)
    r = run_bash(assert_snippet(d))
    assert r.returncode != 0, "stray empty directory outside the allowlist must FATAL"
    assert "EMPTY" in r.stderr


def test_allowlisted_parents_pass(tmp_path):
    # Parent dirs of allowlisted content (usr/, usr/lib32 itself, extras'
    # parents) are non-empty by construction and must never false-flag;
    # empty dirs INSIDE the lib32 tree are legitimate payload too.
    d = tmp_path / "dest"
    (d / "usr/lib32/pkgconfig").mkdir(parents=True)
    (d / "usr/lib32/libz.so.1").write_text("elf")
    (d / "usr/lib32/empty-subdir").mkdir()
    r = run_bash(assert_snippet(d))
    assert r.returncode == 0, r.stderr


# ---------------------------------------------------------------- W1-a ----

def test_scrub_noop_without_marker():
    r = run_bash(
        scrub_snippet('export CC="my-custom-cc"')
        + '; [ "$CC" = "my-custom-cc" ]'
    )
    assert r.returncode == 0, "scrub must not touch env when no lib32 marker is set"
    assert "WARN" not in r.stderr


def test_scrub_clears_leaked_lib32_env():
    # Simulate the W1-a repro: the profile was sourced, do_install died
    # before the trailing lib32_env_end, the next package starts in the
    # same shell — the driver-side scrub must clear everything, loudly.
    r = run_bash(
        scrub_snippet(f'source "{LIB32_ENV}" >/dev/null 2>&1')
        + '; [ -z "${CC:-}" ] && [ -z "${PKG_CONFIG_LIBDIR:-}" ]'
        + ' && [ -z "${IGOS_LIB32_ENV_ACTIVE:-}" ]'
    )
    assert r.returncode == 0, r.stderr
    assert "WARN" in r.stderr, "the scrub must be LOUD when it fires"


def test_profile_success_path_clears_marker():
    r = run_bash(
        f'source "{LIB32_ENV}" >/dev/null 2>&1; lib32_env_end; '
        '[ -z "${CC:-}" ] && [ -z "${IGOS_LIB32_ENV_ACTIVE:-}" ]'
    )
    assert r.returncode == 0, "lib32_env_end must clear the exports AND the marker"


# ------------------------------------------------- lib32-glibc T3 sweep ----

GLIBC_BUILD = REPO / "packages" / "core" / "lib32-glibc" / "build.sh"


def glibc_sweep_snippet(destdir: Path) -> str:
    """Run the exact allowlist-sweep find from lib32-glibc's build.sh by
    extracting it is brittle; instead REPRODUCE its allowlist here and keep
    a guard test (test_glibc_sweep_matches_recipe) that fails if the recipe's
    allowlist drifts from this list."""
    return (
        f'stray=$(find "{destdir}" ! -type d '
        f'! -path "{destdir}/usr/lib32/*" '
        f'! -path "{destdir}/usr/include/gnu/lib-names-32.h" '
        f'! -path "{destdir}/usr/include/gnu/stubs-32.h" '
        f'! -path "{destdir}/usr/lib/ld-linux.so.2" '
        f'! -path "{destdir}/etc/ld.so.conf.d/lib32-glibc.conf" | head -5); '
        f'[ -z "$stray" ]'
    )


def test_glibc_sweep_matches_recipe():
    text = GLIBC_BUILD.read_text()
    for token in [
        "! -type d",
        '! -path "${DESTDIR}/usr/lib32/*"',
        '! -path "${DESTDIR}/usr/include/gnu/lib-names-32.h"',
        '! -path "${DESTDIR}/usr/include/gnu/stubs-32.h"',
        '! -path "${DESTDIR}/usr/lib/ld-linux.so.2"',
        '! -path "${DESTDIR}/etc/ld.so.conf.d/lib32-glibc.conf"',
    ]:
        assert token in text, f"lib32-glibc T3 sweep drifted: {token} missing"


def _glibc_clean_destdir(tmp_path: Path) -> Path:
    d = tmp_path / "dest"
    (d / "usr/lib32").mkdir(parents=True)
    (d / "usr/lib32/libc.so.6").write_text("elf")
    (d / "usr/include/gnu").mkdir(parents=True)
    (d / "usr/include/gnu/lib-names-32.h").write_text("h")
    (d / "usr/include/gnu/stubs-32.h").write_text("h")
    (d / "usr/lib").mkdir(parents=True)
    (d / "usr/lib/ld-linux.so.2").symlink_to("../lib32/ld-linux.so.2")
    (d / "etc/ld.so.conf.d").mkdir(parents=True)
    (d / "etc/ld.so.conf.d/lib32-glibc.conf").write_text("/usr/lib32\n")
    return d


def test_glibc_clean_payload_passes(tmp_path):
    d = _glibc_clean_destdir(tmp_path)
    assert run_bash(glibc_sweep_snippet(d)).returncode == 0


def test_glibc_stray_include_symlink_fails(tmp_path):
    # The W1-b class on the glibc recipe: previously the include check was
    # `-type f`, so a stray symlink under include/ passed.
    d = _glibc_clean_destdir(tmp_path)
    (d / "usr/include/gnu/stubs.h").symlink_to("stubs-32.h")
    assert run_bash(glibc_sweep_snippet(d)).returncode != 0


def test_glibc_stray_outside_swept_dirs_fails(tmp_path):
    # Previously ONLY include/ and lib/ were checked — a stray under
    # usr/bin shipped silently. The full-tree sweep catches it.
    d = _glibc_clean_destdir(tmp_path)
    (d / "usr/bin").mkdir(parents=True)
    (d / "usr/bin/ldd").write_text("script")
    assert run_bash(glibc_sweep_snippet(d)).returncode != 0
