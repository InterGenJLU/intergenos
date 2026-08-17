#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""Both kernel passes must apply the IDENTICAL patch set.

WHAT WENT WRONG, measured 2026-08-11. Four CVE patches were applied only by
linux-kernel's build.sh, by globbing a directory inside that recipe.
linux-kernel-pass2 applied none — the string "patch" occurred zero times in its
build.sh and it declared no patches. That is not a cosmetic asymmetry:

  * pass 2 declares `supersedes: [linux-kernel]`;
  * both passes derive the same KVER and stage the identical
    /boot/vmlinuz-<KVER> and /usr/lib/modules/<KVER>;
  * the installer ENFORCES that a package declaring supersedes installs AFTER
    its predecessor (installer/backend/packages.py), so pass 2's payload is
    written last on a user's machine.

The kernel a user booted was therefore built from unpatched source. It stayed
invisible because the BUILD chroot's phase order is the opposite — core-extra
(pass 2) runs before kernel (pass 1) — so in the chroot the patched kernel was
written last and the ISO looked correct. The inversion appears only on a real
install, which is exactly the class a virtual-machine evaluation cannot exhibit.

The fix is one declared patch set, in package.yml, on both recipes, with the
files in the canonical build/patches directory. These tests are the guard that
keeps it that way: the moment the two declarations differ by one hash, or a
declared hash stops matching the file on disk, the suite fails.
"""
import hashlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PARSER = REPO_ROOT / "scripts/parse-package-yml-patches.py"
PATCH_DIR = REPO_ROOT / "build/patches"
PASS1 = REPO_ROOT / "packages/core/linux-kernel"
PASS2 = REPO_ROOT / "packages/core/linux-kernel-pass2"


def declared(recipe_dir: Path) -> list:
    """The patch set as the SHARED parser reads it — the same instrument the
    build uses, so this test cannot agree with a declaration the build would
    read differently."""
    result = subprocess.run(
        [sys.executable, str(PARSER), str(recipe_dir / "package.yml")],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"the shared patch parser failed on {recipe_dir.name}: {result.stderr}"
    )
    out = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        name, _, sha = line.partition("|")
        out.append((name.strip(), sha.strip()))
    return out


def test_pass1_declares_patches():
    """A parse yielding nothing would make the equality test below pass for the
    wrong reason — two empty sets are equal."""
    assert len(declared(PASS1)) >= 4, "linux-kernel declares fewer patches than expected"


def test_pass2_declares_patches():
    assert len(declared(PASS2)) >= 4, (
        "linux-kernel-pass2 declares no patches. It supersedes pass 1 and its payload "
        "lands last on an installed system, so an undeclared patch set here means the "
        "kernel the user boots is unpatched."
    )


def test_both_passes_declare_the_identical_patch_set():
    """The whole point. Compared by (file, sha256) so a same-named but different
    patch fails too."""
    one, two = declared(PASS1), declared(PASS2)
    # Non-emptiness asserted HERE, not only in the tests above: two empty sets
    # are equal, so without this the equality would pass on a tree where neither
    # pass declares anything — which is precisely the broken state this guard
    # exists to detect. Measured on this file's own red-first run, 2026-08-11.
    assert one and two, (
        "neither pass declares a patch set, so this equality would pass vacuously: "
        f"linux-kernel={one}, linux-kernel-pass2={two}"
    )
    assert one == two, (
        "the two kernel passes do not declare the same patch set.\n"
        f"  linux-kernel      : {one}\n"
        f"  linux-kernel-pass2: {two}\n"
        "Pass 2 ships the kernel the user boots; a patch only pass 1 declares is a "
        "patch the user does not get."
    )


@pytest.mark.parametrize("recipe", [PASS1, PASS2], ids=["linux-kernel", "linux-kernel-pass2"])
def test_every_declared_patch_exists_and_matches_its_recorded_hash(recipe):
    """The declaration carries a sha256 and the build verifies it before applying.
    A recorded hash that does not match the file on disk would refuse at build
    time — catch it in the suite instead."""
    entries = declared(recipe)
    assert entries, (
        f"{recipe.name} declares no patches, so this test would verify nothing. "
        "A vacuous pass here would report the patch set healthy while it is absent."
    )
    for name, sha in entries:
        path = PATCH_DIR / name
        assert path.is_file(), (
            f"{recipe.name} declares {name}, which is not in build/patches/. The "
            "declared-patch mechanism reads from there, so the build would refuse."
        )
        assert sha, f"{recipe.name} declares {name} with no sha256; the build cannot verify it"
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == sha, (
            f"{recipe.name} declares {name} with sha256 {sha}, but the file hashes to "
            f"{actual}. Either the patch changed and the declaration was not updated, "
            "or the declaration is wrong; the build refuses on this mismatch."
        )


def test_pass1_actually_applies_its_declaration():
    """Pass 1's driver (chroot-build-ch10.sh) has NO declared-patch support, so
    unlike pass 2 it must apply the declaration itself. A declaration nothing
    applies is a stub."""
    text = (PASS1 / "build.sh").read_text()
    assert "parse-package-yml-patches.py" in text, (
        "linux-kernel/build.sh no longer reads its declared patch set. Its driver does "
        "not apply declared patches, so nothing else will."
    )
    assert "patch -Np1" in text, "linux-kernel/build.sh no longer applies any patch"


def test_pass1_no_longer_globs_a_recipe_local_patch_directory():
    """The old shape is what hid the divergence: patches that existed only inside
    one recipe, invisible to the other and to any shared instrument."""
    text = (PASS1 / "build.sh").read_text()
    assert "linux-kernel/patches" not in text, (
        "linux-kernel/build.sh still points at a recipe-local patches directory. The "
        "canonical home is build/patches, which is what the declared mechanism reads "
        "and what makes the two passes comparable by one instrument."
    )
    assert not (PASS1 / "patches").exists(), (
        "packages/core/linux-kernel/patches/ still exists. Two homes for the same "
        "patches is how they drift."
    )


def test_pass2_driver_applies_declared_patches():
    """Pass 2 relies on its driver rather than its own build.sh, so the guard has
    to check the driver is still the thing that does it."""
    driver = (REPO_ROOT / "scripts/chroot-build-core-extra.sh").read_text()
    assert "apply_package_patches" in driver, (
        "chroot-build-core-extra.sh no longer applies declared patches, so "
        "linux-kernel-pass2 would build unpatched while still declaring a patch set."
    )


def test_the_declared_patches_are_the_cve_set():
    """Names are not evidence, but a set that stops mentioning CVEs at all is
    worth failing on: it means the security patches left without anyone saying so."""
    names = [name for name, _ in declared(PASS2)]
    cve = [n for n in names if re.search(r"CVE-\d{4}-\d+", n)]
    assert len(cve) >= 4, (
        f"only {len(cve)} of the {len(names)} declared patches name a CVE: {names}"
    )
