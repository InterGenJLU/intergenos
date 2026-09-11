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
declared hash stops matching the file on disk, or a kernel patch file sits in
build/patches undeclared, the suite fails.

The SIZE of the set is not asserted. It was, until 2026-09-11: three tests
required at least four CVE-named patches, which was true of the 6.18.10
declaration and false the day the kernel moved to 6.18.51 (which carries
those fixes upstream, so the backports were retired). A count is a snapshot
of one declaration; the invariants are identity between the passes, presence
of every declared file, and no undeclared kernel patch on disk.
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
    wrong reason — two empty sets are equal. The COUNT is not an invariant: the
    set shrank from six to one on 2026-09-11 when the kernel moved to a release
    that carries five of the fixes upstream, and it will grow and shrink again.
    What must hold is that the declaration is non-empty and readable."""
    assert len(declared(PASS1)) >= 1, "linux-kernel declares no patches"


def test_pass2_declares_patches():
    assert len(declared(PASS2)) >= 1, (
        "linux-kernel-pass2 declares no patches. It supersedes pass 1 and its payload "
        "lands last on an installed system, so an undeclared patch set here means the "
        "kernel the user boots is missing whatever pass 1 applies."
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


def kernel_patch_files_on_disk() -> set:
    """Every patch file in build/patches that belongs to the kernel: the
    linux-<version>-* local patches and any CVE-named file whose body touches a
    kernel source path. build/patches is shared by every package, so a name
    alone does not say which package a CVE file is for; the body does."""
    out = set()
    for path in PATCH_DIR.iterdir():
        if not path.is_file() or path.suffix != ".patch":
            continue
        if path.name.startswith("linux-"):
            out.add(path.name)
        elif re.search(r"CVE-\d{4}-\d+", path.name):
            body = path.read_text(encoding="utf-8", errors="replace")
            if re.search(r"^\+\+\+ b/(net|crypto|drivers|kernel|fs|mm|arch|include|lib|security|sound|block)/", body, re.M):
                out.add(path.name)
    return out


def test_every_kernel_patch_on_disk_is_declared_and_every_declared_one_exists():
    """The two directions of the same invariant. A kernel patch file that no
    recipe declares is a fix that silently stopped being applied (the shape the
    2026-08-11 measurement found); a declared file that is absent refuses the
    build. Before 2026-09-11 this test asserted "at least four CVE-named
    patches" — a count that encoded one moment's declaration. The kernel moved
    to a release carrying those fixes upstream, the CVE patches were retired,
    and the count became false without any fix having been lost. The invariant
    that survives a version move is set equality, not a number."""
    on_disk = kernel_patch_files_on_disk()
    declared_names = {name for name, _ in declared(PASS2)}
    undeclared = sorted(on_disk - declared_names)
    assert not undeclared, (
        "kernel patch files exist in build/patches that neither pass declares, so "
        f"nothing applies them: {undeclared}. Either declare them in BOTH recipes or "
        "remove them with a release note saying why the fix is no longer needed."
    )
    missing = sorted(declared_names - on_disk)
    assert not missing, (
        f"linux-kernel-pass2 declares patches that are not in build/patches: {missing}"
    )
