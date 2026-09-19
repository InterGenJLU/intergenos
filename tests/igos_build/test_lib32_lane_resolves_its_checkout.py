#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
"""The 32-bit lane takes its build inputs from the checkout the build runs out of.

WHAT WENT WRONG. The styles inject the 32-bit build profile into every
elf_class-32 package: the meson lane passes `--cross-file <path>` and the
autotools/make lanes prefix every command with `source <path>`. Both paths were
the literal strings `/mnt/intergenos/config/lib32/lib32-cross.ini` and
`/mnt/intergenos/scripts/lib32-env.sh`. Inside the build chroot that absolute
path IS the repository, so it was right there. On a live machine it is right
only by accident: a `--stage-only` build run out of a second checkout — a lane
worktree, a clone, a review tree — takes the RECIPE from that checkout and the
cross file and profile from `/mnt/intergenos`, a different tree at a different
commit, and nothing says so. This is the same class of fixed path the shell
helper carried until it was routed through the checkout the recipe is in
(landed 2026-09-19); these two lines are what was left of it.

The two files decide the compilers, the target triplet, the pkg-config
directory and the staging assertions, so a lane that CHANGES either one would
have been "proven" with the other tree's copy of it.

WHAT THE CHANGE DOES. Both paths are resolved, in order, from the checkout the
recipe is in (the directory above its `packages/` tree), then from the checkout
the builder module itself lives in, and only then from the chroot's
`/mnt/intergenos` path as the last fallback — the same resolution, and now the
same code, the shell helper uses. Inside the chroot the first step already IS
`/mnt/intergenos`, so the composed commands there are the strings they have
always been. The builder writes one line per 32-bit package naming the inputs
it will use, so a person reading a build log can see which tree they came from.

WHAT THESE TESTS PROVE. The resolution, its order, its fallback, that every
phase of every 32-bit lane uses the resolved paths, and that no style module
still carries a fixed lib32 path outside the two named fallback constants. The
positive controls are the chroot shape and the unchanged 64-bit commands.

What they do NOT prove: that a real 32-bit build reads those files
successfully. That is proven by running the real builder out of a lane
worktree and reading the lib32 lines and the compile lines out of the build
log, which is done once against reality and recorded in the delivery.
"""
import importlib
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# The builder's package directory is named with a hyphen, so every test in this
# tree reaches it through importlib rather than a plain import.
parser_mod = importlib.import_module("igos-build.parser")
base_mod = importlib.import_module("igos-build.styles.base")
autotools_mod = importlib.import_module("igos-build.styles.autotools")
meson_mod = importlib.import_module("igos-build.styles.meson")
make_mod = importlib.import_module("igos-build.styles.make")

parse_template = parser_mod.parse_template


def _symbol(name):
    """Fetch a name from the base style module, FAILING the test if it is absent.

    Taken lazily on purpose: a module-level import of a name the tree does not
    have yet raises at COLLECTION, which aborts every test in the file and
    reports an error rather than a failure. A test that must fail on the
    previous tree should say what is missing, in its own assertion.
    """
    value = getattr(base_mod, name, None)
    assert value is not None, (
        f"igos-build/styles/base.py defines no {name}: the 32-bit lane still "
        "injects a hardcoded path, so a build run out of any other checkout "
        "uses another tree's cross file or build profile"
    )
    return value


def lib32_cross_file(*args, **kwargs):
    return _symbol("lib32_cross_file")(*args, **kwargs)


def lib32_env_script(*args, **kwargs):
    return _symbol("lib32_env_script")(*args, **kwargs)


def lib32_env_source(*args, **kwargs):
    return _symbol("lib32_env_source")(*args, **kwargs)


CROSS_BODY = "[binaries]\n# a stand-in cross file; only its path matters here\n"
ENV_BODY = "# a stand-in 32-bit profile; only its path matters here\n"

RECIPE_YML = """\
name: demo32
version: '1.0'
release: 1
description: a recipe used only by this test
license: MIT
source: []
dependencies:
  build: []
  host: []
  runtime: []
tier: desktop
elf_class: "32"
build_style: {style}
"""


def _make_checkout(root: pathlib.Path, style: str = "autotools",
                   with_inputs: bool = True) -> pathlib.Path:
    """Build a miniature checkout: the two lib32 inputs plus a 32-bit recipe."""
    recipe_dir = root / "packages" / "desktop" / "demo32"
    recipe_dir.mkdir(parents=True)
    template = recipe_dir / "package.yml"
    template.write_text(RECIPE_YML.format(style=style))
    if with_inputs:
        (root / "config" / "lib32").mkdir(parents=True)
        (root / "config" / "lib32" / "lib32-cross.ini").write_text(CROSS_BODY)
        (root / "scripts").mkdir()
        (root / "scripts" / "lib32-env.sh").write_text(ENV_BODY)
    return template


def _cmds(style_obj, pkg):
    return {p.name: p.commands for p in style_obj.all_phases(pkg)}


# ------------------------------------- the recipe's own checkout answers ----

def test_the_cross_file_comes_from_the_checkout_the_recipe_is_in(tmp_path):
    lane = tmp_path / "a-lane-worktree"
    template = _make_checkout(lane, style="meson")
    pkg = parse_template(template)
    expected = str(lane / "config" / "lib32" / "lib32-cross.ini")
    assert lib32_cross_file(pkg) == expected, (
        "a 32-bit build of a recipe in one checkout must use that checkout's "
        f"cross file; it resolved to {lib32_cross_file(pkg)!r}"
    )
    cfg = _cmds(meson_mod.MesonStyle(), pkg)["configure"][0]
    assert f"--cross-file {expected}" in cfg, (
        f"the meson configure command does not pass the resolved cross file: {cfg[:200]!r}"
    )
    assert "/mnt/intergenos/config" not in cfg, (
        f"the configure command still names the fixed path: {cfg[:200]!r}"
    )


def test_the_profile_comes_from_the_checkout_the_recipe_is_in(tmp_path):
    lane = tmp_path / "a-lane-worktree"
    template = _make_checkout(lane, style="autotools")
    pkg = parse_template(template)
    expected = str(lane / "scripts" / "lib32-env.sh")
    assert lib32_env_script(pkg) == expected, (
        f"expected the lane's own 32-bit profile; got {lib32_env_script(pkg)!r}"
    )
    assert lib32_env_source(pkg) == f"source {expected}"


@pytest.mark.parametrize("mod,style_cls,style_name", [
    (autotools_mod, "AutotoolsStyle", "autotools"),
    (make_mod, "MakeStyle", "make"),
])
def test_every_phase_of_the_env_lanes_sources_the_resolved_profile(
        tmp_path, mod, style_cls, style_name):
    lane = tmp_path / "a-lane-worktree"
    template = _make_checkout(lane, style=style_name)
    pkg = parse_template(template)
    expected = f"source {lane / 'scripts' / 'lib32-env.sh'}; "
    cmds = _cmds(getattr(mod, style_cls)(), pkg)
    for phase in ("configure", "build", "check", "install"):
        for command in cmds[phase]:
            assert command.startswith(expected), (
                f"the {style_name} {phase} phase does not source the resolved "
                f"profile first: {command[:200]!r}"
            )
            assert "/mnt/intergenos/scripts" not in command, (
                f"the {style_name} {phase} phase still names the fixed profile "
                f"path although the recipe's own checkout has one: {command[:200]!r}"
            )


def test_the_meson_install_stage_sources_the_resolved_profile(tmp_path):
    """The meson lane's staged-copy assertion sources the profile too."""
    lane = tmp_path / "a-lane-worktree"
    template = _make_checkout(lane, style="meson")
    pkg = parse_template(template)
    staged = _cmds(meson_mod.MesonStyle(), pkg)["install"][1]
    assert staged.startswith(f"source {lane / 'scripts' / 'lib32-env.sh'}; "), (
        f"the staged-copy command does not source the resolved profile: {staged[:200]!r}"
    )


# --------------------------------------------- order, fallback, controls ----

def test_the_module_checkout_answers_when_the_recipe_tree_has_neither(tmp_path):
    """Second in order: the tree the builder itself is running out of."""
    recipe_only = tmp_path / "recipes-without-inputs"
    template = _make_checkout(recipe_only, with_inputs=False)
    module_checkout = tmp_path / "the-builder-checkout"
    (module_checkout / "config" / "lib32").mkdir(parents=True)
    (module_checkout / "config" / "lib32" / "lib32-cross.ini").write_text(CROSS_BODY)
    (module_checkout / "scripts").mkdir()
    (module_checkout / "scripts" / "lib32-env.sh").write_text(ENV_BODY)
    pkg = parse_template(template)
    assert lib32_cross_file(pkg, module_root=module_checkout) == str(
        module_checkout / "config" / "lib32" / "lib32-cross.ini")
    assert lib32_env_script(pkg, module_root=module_checkout) == str(
        module_checkout / "scripts" / "lib32-env.sh")


def test_the_fallback_is_taken_only_when_no_checkout_has_the_inputs(tmp_path):
    recipe_only = tmp_path / "recipes-without-inputs"
    template = _make_checkout(recipe_only, with_inputs=False)
    empty_module_root = tmp_path / "a-checkout-without-inputs"
    empty_module_root.mkdir()
    pkg = parse_template(template)
    assert lib32_cross_file(pkg, module_root=empty_module_root) == _symbol(
        "CHROOT_LIB32_CROSS_FILE")
    assert lib32_env_script(pkg, module_root=empty_module_root) == _symbol(
        "CHROOT_LIB32_ENV_SCRIPT")


def test_the_fallbacks_are_the_paths_the_chroot_bind_mounts():
    assert _symbol("CHROOT_LIB32_CROSS_FILE") == "/mnt/intergenos/config/lib32/lib32-cross.ini"
    assert _symbol("CHROOT_LIB32_ENV_SCRIPT") == "/mnt/intergenos/scripts/lib32-env.sh"


def test_the_chroot_case_composes_the_strings_it_always_has(tmp_path):
    """Positive control: when the recipe's tree IS the chroot root, nothing moves.

    The chroot cannot be created here, so its shape is reproduced: a checkout
    that holds the recipe AND both inputs, whose input paths are also the
    fallbacks. The resolved strings must equal the fallbacks, which is what the
    chroot has always passed and sourced.
    """
    chroot_like = tmp_path / "mnt-intergenos-stand-in"
    template = _make_checkout(chroot_like, style="meson")
    pkg = parse_template(template)
    cross_fallback = str(chroot_like / "config" / "lib32" / "lib32-cross.ini")
    env_fallback = str(chroot_like / "scripts" / "lib32-env.sh")
    assert lib32_cross_file(pkg, fallback=cross_fallback) == cross_fallback
    assert lib32_env_script(pkg, fallback=env_fallback) == env_fallback


def test_a_recipe_with_no_template_path_still_resolves(tmp_path):
    """A package built without a template path must not crash the composer."""
    module_checkout = tmp_path / "the-builder-checkout"
    (module_checkout / "scripts").mkdir(parents=True)
    (module_checkout / "scripts" / "lib32-env.sh").write_text(ENV_BODY)
    pkg = parser_mod.Package(
        name="demo32", version="1.0", release=1, description="fixture",
        license="MIT", source=[], dependencies=parser_mod.Dependencies(),
        build_style="autotools", elf_class="32",
    )
    assert lib32_env_script(pkg, module_root=module_checkout) == str(
        module_checkout / "scripts" / "lib32-env.sh")


def test_no_style_module_carries_a_fixed_lib32_path(tmp_path):
    """Source check: the only fixed lib32 paths left are the two fallbacks.

    A comment may describe the chroot path, so comment lines are dropped
    before matching — a source check that passes on its own comment proves
    nothing.
    """
    offenders = []
    for path in sorted((REPO_ROOT / "igos-build" / "styles").glob("*.py")):
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            if "/mnt/intergenos/config/lib32" not in line and \
               "/mnt/intergenos/scripts/lib32-env.sh" not in line:
                continue
            if stripped.startswith(("CHROOT_LIB32_CROSS_FILE =",
                                    "CHROOT_LIB32_ENV_SCRIPT =")):
                continue
            offenders.append(f"{path.name}:{number}: {stripped}")
    assert not offenders, (
        "a style still names a fixed 32-bit build input outside the two "
        "fallback constants, so a build out of another checkout uses that "
        "tree's file:\n" + "\n".join(offenders)
    )


def test_the_builder_logs_the_lib32_inputs_it_will_use():
    """The build log must name the inputs, so a person can see which tree ran.

    This reads the builder source for the call. It is a weak check on its own
    and is not the proof: the delivery carries a real 32-bit build whose log
    shows the lines naming the lane's files.
    """
    builder_src = (REPO_ROOT / "igos-build" / "builder.py").read_text()
    lines = [
        ln for ln in builder_src.splitlines()
        if "lib32_paths" in ln and not ln.strip().startswith("#")
    ]
    assert lines, (
        "builder.py makes no non-comment call to the style's lib32_paths(), so no "
        "build log names the cross file and profile a 32-bit build used"
    )


def test_a_64_bit_package_carries_no_lib32_input_and_no_change(tmp_path):
    """Control: the 64-bit tree's commands and lib32 inputs are untouched."""
    lane = tmp_path / "a-lane-worktree"
    recipe_dir = lane / "packages" / "desktop" / "demo64"
    recipe_dir.mkdir(parents=True)
    template = recipe_dir / "package.yml"
    template.write_text(RECIPE_YML.format(style="autotools").replace(
        'elf_class: "32"\n', "").replace("demo32", "demo64"))
    pkg = parse_template(template)
    cmds = _cmds(autotools_mod.AutotoolsStyle(), pkg)
    flat = " ".join(c for phase in cmds.values() for c in phase)
    assert "lib32" not in flat and "cross-file" not in flat
    assert getattr(autotools_mod.AutotoolsStyle(), "lib32_paths", lambda p: {})(pkg) == {}, (
        "a 64-bit package must report no 32-bit build inputs"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
