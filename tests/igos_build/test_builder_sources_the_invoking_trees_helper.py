#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""A build takes its shell helper from the checkout it is running out of.

WHAT WENT WRONG. Every build.sh phase command is composed as
`source <helper> && source_profile_d && source <recipe>/build.sh && …`, and the
helper path was the literal string `/mnt/intergenos/scripts/pkg-functions.sh`.
Inside the build chroot that absolute path IS the repository, so it was right
there. On a live machine it is only right by accident: a `--stage-only` build
run out of a second checkout — a lane worktree, a clone, a review tree — took
the RECIPE from that checkout and the HELPER from `/mnt/intergenos`, which is a
different tree at a different commit. Measured 2026-09-19 on a lane whose copy
of `scripts/pkg-functions.sh` differed from the master checkout's by 66 lines.

Nothing reported the mixture. A lane that CHANGES the helper would have been
"proven" with the other tree's copy, and a build proof is only worth the tree
it was taken from.

WHAT THE CHANGE DOES. The helper is resolved from the checkout the recipe came
from — the directory above the `packages/` tree the recipe sits in — and, if
that tree has no helper, from the checkout this builder module itself lives in.
The literal `/mnt/intergenos` path remains as the last fallback, so the chroot
behaves exactly as before: there the computed root IS `/mnt/intergenos` and the
composed command is the same string it has always been. The builder writes one
line at the start of each package naming the helper it actually sourced, so a
person reading a build log can see which tree's helper ran.

WHAT THESE TESTS PROVE. That resolution, its fallback order, and that EVERY
phase (not only configure) uses the resolved path. The positive control is the
chroot case: when the recipe's own tree is the fallback root, the composed
string is the one the chroot has always run.

What they do NOT prove: that a real build sources the file successfully — that
is proven by running the real builder and reading the helper line and the
sha256 of the file it names out of the build log, which is done once against
reality and recorded in the delivery.
"""
import importlib
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# The builder's package directory is named with a hyphen, so every test in this
# tree reaches it through importlib rather than a plain import (the convention
# test_styles_lib32.py established).
parser_mod = importlib.import_module("igos-build.parser")
custom_mod = importlib.import_module("igos-build.styles.custom")

parse_template = parser_mod.parse_template
CustomStyle = custom_mod.CustomStyle


def _symbol(name):
    """Fetch a name from the custom style, FAILING the test if it is absent.

    Taken lazily on purpose: a module-level import of a name the tree does not
    have yet raises at COLLECTION, which aborts every test in the file and
    reports an error rather than a failure. A test that must fail on the
    previous tree should say what is missing, in its own assertion.
    """
    value = getattr(custom_mod, name, None)
    assert value is not None, (
        f"igos-build/styles/custom.py defines no {name}: the build style still "
        "composes its phase commands with a hardcoded helper path, so a build run "
        "out of any other checkout sources another tree's helper"
    )
    return value


def resolve_pkg_functions(*args, **kwargs):
    return _symbol("resolve_pkg_functions")(*args, **kwargs)


def _chroot_fallback():
    return _symbol("CHROOT_PKG_FUNCTIONS")

HELPER_BODY = "# a stand-in helper; only its path matters to these tests\n"
RECIPE_YML = """\
name: demo
version: '1.0'
release: 1
description: a recipe used only by this test
license: MIT
source: []
dependencies:
  build: []
  host: []
  runtime: []
tier: core
build_style: custom
"""


def _make_checkout(root: pathlib.Path, with_helper: bool = True) -> pathlib.Path:
    """Build a miniature checkout: <root>/scripts/pkg-functions.sh + a recipe."""
    recipe_dir = root / "packages" / "core" / "demo"
    recipe_dir.mkdir(parents=True)
    (recipe_dir / "package.yml").write_text(RECIPE_YML)
    (recipe_dir / "build.sh").write_text("#!/bin/bash\ndo_install() { :; }\n")
    if with_helper:
        (root / "scripts").mkdir()
        (root / "scripts" / "pkg-functions.sh").write_text(HELPER_BODY)
    return recipe_dir / "package.yml"


def test_the_helper_comes_from_the_checkout_the_recipe_is_in(tmp_path):
    other_checkout = tmp_path / "a-lane-worktree"
    template = _make_checkout(other_checkout)
    resolved = resolve_pkg_functions(template)
    assert resolved == str(other_checkout / "scripts" / "pkg-functions.sh"), (
        "a build of a recipe in one checkout must source that checkout's helper; "
        f"it resolved to {resolved!r}"
    )


def test_every_phase_command_names_the_resolved_helper(tmp_path):
    other_checkout = tmp_path / "a-lane-worktree"
    template = _make_checkout(other_checkout)
    pkg = parse_template(template)
    style = CustomStyle()
    expected = f"source {other_checkout / 'scripts' / 'pkg-functions.sh'} && source_profile_d && "
    phases = [
        style.configure(pkg), style.build(pkg), style.check(pkg),
        style.install(pkg), style.post_install(pkg),
    ]
    for phase in phases:
        for command in phase.commands:
            assert command.startswith(expected), (
                f"the {phase.name} phase does not source the resolved helper first: "
                f"{command[:160]!r}"
            )
            assert _chroot_fallback() not in command, (
                f"the {phase.name} phase still names the fallback path although the "
                f"recipe's own checkout has a helper: {command[:160]!r}"
            )


def test_the_module_checkout_answers_when_the_recipe_tree_has_no_helper(tmp_path):
    """Second in order: the tree the builder itself is running out of."""
    recipe_only = tmp_path / "recipes-without-scripts"
    template = _make_checkout(recipe_only, with_helper=False)
    module_checkout = tmp_path / "the-builder-checkout"
    (module_checkout / "scripts").mkdir(parents=True)
    (module_checkout / "scripts" / "pkg-functions.sh").write_text(HELPER_BODY)
    resolved = resolve_pkg_functions(template, module_root=module_checkout)
    assert resolved == str(module_checkout / "scripts" / "pkg-functions.sh"), (
        f"expected the builder's own checkout to answer; got {resolved!r}"
    )


def test_the_fallback_is_taken_only_when_no_checkout_has_the_helper(tmp_path):
    recipe_only = tmp_path / "recipes-without-scripts"
    template = _make_checkout(recipe_only, with_helper=False)
    empty_module_root = tmp_path / "a-checkout-without-scripts"
    empty_module_root.mkdir()
    resolved = resolve_pkg_functions(template, module_root=empty_module_root)
    assert resolved == _chroot_fallback(), (
        "with no helper in either checkout the fallback must be taken; got "
        f"{resolved!r}"
    )


def test_the_fallback_is_the_path_the_chroot_bind_mounts():
    fallback = _chroot_fallback()
    assert fallback == "/mnt/intergenos/scripts/pkg-functions.sh", (
        "the fallback is the chroot's own repository path; changing it changes what "
        f"a chroot build sources: {fallback!r}"
    )


def test_the_chroot_case_composes_the_string_it_always_has(tmp_path):
    """Positive control: when the recipe's tree IS the fallback root, nothing moves.

    The chroot cannot be created here, so its shape is reproduced: a checkout
    that holds the recipe AND the helper, whose helper path is also the
    fallback. The resolved string must equal the fallback, which is what the
    chroot has always sourced.
    """
    chroot_like = tmp_path / "mnt-intergenos-stand-in"
    template = _make_checkout(chroot_like)
    fallback = str(chroot_like / "scripts" / "pkg-functions.sh")
    resolved = resolve_pkg_functions(template, fallback=fallback)
    assert resolved == fallback, (
        f"the chroot-shaped case must resolve to its own repository helper: {resolved!r}"
    )


def test_a_recipe_with_no_template_path_still_resolves(tmp_path):
    """A parsed package without a template path must not crash the composer."""
    module_checkout = tmp_path / "the-builder-checkout"
    (module_checkout / "scripts").mkdir(parents=True)
    (module_checkout / "scripts" / "pkg-functions.sh").write_text(HELPER_BODY)
    resolved = resolve_pkg_functions(None, module_root=module_checkout)
    assert resolved == str(module_checkout / "scripts" / "pkg-functions.sh")


def test_the_builder_logs_which_helper_it_sourced():
    """The build log must name the helper, so a person can see which tree ran.

    This reads the builder source for the call. It is a weak check on its own
    and is not the proof: the delivery carries a real build whose log shows the
    line naming the lane's helper.
    """
    builder_src = (REPO_ROOT / "igos-build" / "builder.py").read_text()
    lines = [
        ln for ln in builder_src.splitlines()
        if "pkg_functions_path" in ln and not ln.strip().startswith("#")
    ]
    assert lines, (
        "builder.py makes no non-comment call to the style's pkg_functions_path(), so "
        "no build log names the helper that was sourced"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
