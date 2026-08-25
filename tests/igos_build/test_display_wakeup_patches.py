#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The two patches that stop a graphics-card wake-up from rebuilding the desktop.

WHAT WENT WRONG, measured on a three-card workstation running the shipped
kernel and the shipped compositor. A card that drives no display serves compute
work; it runtime suspends five seconds after its last client closes it, and the
next client to open its render node resumes it. Two things then happened, each
a defect on its own:

  * the kernel announced the resume as a hotplug for that card, unconditionally,
    although all four of its connectors were disconnected and none had changed;

  * the compositor, which holds no file descriptor on an idle device it is not
    using, tried to reopen the card, was refused once by the session manager
    while it was still processing the same device event, and treated that
    refusal as evidence the device had changed - freeing its planes, CRTCs and
    connectors and reporting a full resource change, which made the monitor
    manager rebuild every monitor configuration on the machine.

The user saw windows move to the primary monitor and the desktop background
disappear, repeatedly, for a month.

Neither fix is a line of first-party code: both are patches to upstream sources,
declared in the recipes that build them. A declared patch is only as good as its
declaration - a patch file with no recipe entry is never applied, a recipe entry
with no file refuses the build, and a patch that reaches only one of the two
kernel passes never reaches the kernel a user boots (the class
test_kernel_patch_lockstep.py exists for). These tests hold the declarations to
what they claim, and hold each patch body to the change it is named for, so that
a truncated or replaced patch file fails here rather than silently building a
kernel or a compositor without the fix.

What these tests do NOT prove, and nothing in this suite can: that the built
kernel and the built compositor behave as intended on real hardware. The patches
are proven to apply to the pristine upstream sources and to compile; the
behaviour is proven by a reproduction on an installed system.
"""
import hashlib
import pathlib
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PARSER = REPO_ROOT / "scripts/parse-package-yml-patches.py"
PATCH_DIR = REPO_ROOT / "build/patches"

KERNEL_PATCH = "linux-6.18.10-amdgpu-runtime-resume-hotplug-only-on-change-1.patch"
MUTTER_PATCH = "mutter-49.4-headless_device_reopen_keeps_state-1.patch"

KERNEL_RECIPES = [
    REPO_ROOT / "packages/core/linux-kernel",
    REPO_ROOT / "packages/core/linux-kernel-pass2",
]
MUTTER_RECIPE = REPO_ROOT / "packages/desktop/mutter"


def declared(recipe_dir):
    """The patch set as the SHARED parser reads it, so this test cannot agree
    with a declaration the build would read differently."""
    result = subprocess.run(
        [sys.executable, str(PARSER), str(recipe_dir / "package.yml")],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"the shared patch parser failed on {recipe_dir.name}: {result.stderr}"
    )
    out = {}
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        name, _, sha = line.partition("|")
        out[name.strip()] = sha.strip()
    return out


def recipe_yaml(recipe_dir):
    with open(recipe_dir / "package.yml", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@pytest.mark.parametrize(
    "recipe,patch",
    [(KERNEL_RECIPES[0], KERNEL_PATCH),
     (KERNEL_RECIPES[1], KERNEL_PATCH),
     (MUTTER_RECIPE, MUTTER_PATCH)],
    ids=["linux-kernel", "linux-kernel-pass2", "mutter"],
)
def test_recipe_declares_the_patch_and_the_hash_matches(recipe, patch):
    """A declaration whose hash does not match the file refuses at build time;
    catch it here instead. The expected hash is computed from the file rather
    than written down, so this test states a relationship, not a constant."""
    entries = declared(recipe)
    assert patch in entries, (
        f"{recipe.name} does not declare {patch}. Nothing else applies it: the "
        "declaration in package.yml is what the builder reads."
    )
    path = PATCH_DIR / patch
    assert path.is_file(), (
        f"{recipe.name} declares {patch}, which is not in build/patches/. The "
        "declared-patch mechanism reads from there, so the build would refuse."
    )
    recorded = entries[patch]
    assert recorded, f"{recipe.name} declares {patch} with no sha256"
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    assert actual == recorded, (
        f"{recipe.name} declares {patch} with a sha256 that does not match the "
        "file on disk. Either the patch changed and the declaration did not, or "
        "the declaration is wrong; the build refuses on this mismatch."
    )


@pytest.mark.parametrize(
    "recipe,patch",
    [(KERNEL_RECIPES[0], KERNEL_PATCH),
     (KERNEL_RECIPES[1], KERNEL_PATCH),
     (MUTTER_RECIPE, MUTTER_PATCH)],
    ids=["linux-kernel", "linux-kernel-pass2", "mutter"],
)
def test_the_patch_rides_in_the_corresponding_source(recipe, patch):
    """A patch in build/patches does not travel with a recipe on its own:
    build-source-archives.py bundles a recipe's OWN patches/ directory, so a
    canonical-directory patch must be declared in sources_extra or the published
    source cannot rebuild the published binary. The kernel's CVE patches carry
    the same entries for the same reason."""
    meta = recipe_yaml(recipe)
    extras = meta.get("sources_extra") or []
    assert f"build/patches/{patch}" in extras, (
        f"{recipe.name} applies {patch} but does not list it in sources_extra, "
        "so the published corresponding source would not carry it."
    )


def test_the_kernel_patch_changes_the_resume_path_it_is_named_for():
    """Names are not evidence. A patch body that no longer carries the guard is
    a patch that no longer does anything, and a build applies it without
    complaint as long as it still applies."""
    body = (PATCH_DIR / KERNEL_PATCH).read_text(encoding="utf-8")
    assert "drivers/gpu/drm/amd/amdgpu/amdgpu_device.c" in body, (
        "the kernel patch no longer touches the file whose resume path emits the "
        "event"
    )
    assert "in_runpm" in body, (
        "the kernel patch no longer distinguishes a runtime resume from any other "
        "resume, which is the whole of its subject"
    )
    assert "drm_helper_probe_detect" in body, (
        "the kernel patch no longer probes the connectors, so it can only be "
        "suppressing the event rather than answering it"
    )


def test_the_compositor_patch_keeps_state_instead_of_tearing_it_down():
    body = (PATCH_DIR / MUTTER_PATCH).read_text(encoding="utf-8")
    assert "src/backends/native/meta-kms-impl-device.c" in body, (
        "the compositor patch no longer touches the file that frees the device "
        "state on a failed reopen"
    )
    assert "META_KMS_RESOURCE_CHANGE_NONE" in body, (
        "the compositor patch no longer reports 'nothing changed' for a device "
        "with no connected connector, which is the change that keeps the user's "
        "windows where they are"
    )
    assert "has_connected_connector" in body, (
        "the compositor patch no longer asks whether any connector is connected, "
        "so it would either tear down always or keep state always - one of which "
        "loses a real hotplug"
    )
