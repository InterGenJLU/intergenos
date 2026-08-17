# SPDX-License-Identifier: GPL-3.0-or-later
# Wedge suite for the builder's lib32 lane (GE arc, Wave 2 infra): every
# style keys the lane on the recipe's elf_class governance field — recipes
# never restate profile values (the G2/T2 one-definition rule).
#
# The two contracts under test:
#   1. ZERO behavior change on the 64-bit tree: an elf_class-64 (default)
#      package generates byte-identical commands to the pre-lane styles.
#   2. The 32-bit lane: meson gets the cross file + lib32 libdir + verbose
#      ninja (RT-8 compile-evidence mandate); autotools/make get the sourced
#      bash profile per command (each command is its own subprocess) plus
#      the mechanism configure flags; cmake FAIL-CLOSES — the AUTO style
#      has no lib32 lane; build_style custom consuming THE toolchain file
#      (lib32-llvm) is the supported path. Refusing beats silently
#      answering 64-bit (the RT-7 leakage class).

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

parser = importlib.import_module("igos-build.parser")
base = importlib.import_module("igos-build.styles.base")
autotools_mod = importlib.import_module("igos-build.styles.autotools")
meson_mod = importlib.import_module("igos-build.styles.meson")
make_mod = importlib.import_module("igos-build.styles.make")
cmake_mod = importlib.import_module("igos-build.styles.cmake")


def _pkg(style: str, elf_class: str = "64", flags=None) -> parser.Package:
    return parser.Package(
        name="testpkg",
        version="1.0",
        release=1,
        description="wedge fixture",
        license="MIT",
        source=[],
        dependencies=parser.Dependencies(),
        build_style=style,
        elf_class=elf_class,
        configure_flags=flags or [],
    )


def _cmds(style_obj, pkg):
    return {p.name: p.commands for p in style_obj.all_phases(pkg)}


# ------------------------------------------------ 64-bit zero change ----

@pytest.mark.parametrize(
    "mod,style_cls",
    [
        (autotools_mod, "AutotoolsStyle"),
        (meson_mod, "MesonStyle"),
        (make_mod, "MakeStyle"),
        (cmake_mod, "CMakeStyle"),
    ],
)
def test_default_64_commands_carry_no_lib32_lane(mod, style_cls):
    style = getattr(mod, style_cls)()
    cmds = _cmds(style, _pkg("any"))
    flat = " ".join(c for phase in cmds.values() for c in phase)
    assert "lib32" not in flat
    assert "cross-file" not in flat
    assert "ninja -v" not in flat
    assert base.LIB32_ENV_SOURCE not in flat


# ------------------------------------------------------- meson lane ----

def test_meson_32_gets_cross_file_and_lib32_libdir():
    cmds = _cmds(meson_mod.MesonStyle(), _pkg("meson", "32"))
    cfg = cmds["configure"][0]
    assert f"--cross-file {base.LIB32_CROSS_FILE}" in cfg
    assert "--libdir=/usr/lib32" in cfg
    assert "--wrap-mode=nodownload" in cfg, "the offline model must survive the lane"


def test_meson_32_builds_verbose():
    cmds = _cmds(meson_mod.MesonStyle(), _pkg("meson", "32"))
    assert cmds["build"] == ["ninja -v -C build -j${IGOS_JOBS}"]


def test_meson_32_keeps_recipe_flags():
    cmds = _cmds(meson_mod.MesonStyle(), _pkg("meson", "32", ["-Dvulkan-drivers=amd"]))
    assert "-Dvulkan-drivers=amd" in cmds["configure"][0]


# --------------------------------------------------- autotools lane ----

def test_autotools_32_sources_profile_on_every_phase():
    cmds = _cmds(autotools_mod.AutotoolsStyle(), _pkg("autotools", "32"))
    for phase in ("configure", "build", "check", "install"):
        for c in cmds[phase]:
            assert c.startswith(f"{base.LIB32_ENV_SOURCE}; "), (
                f"{phase} must re-source the profile (each command is its "
                f"own subprocess): {c}"
            )


def test_autotools_32_injects_mechanism_flags():
    cfg = _cmds(autotools_mod.AutotoolsStyle(), _pkg("autotools", "32", ["--prefix=/usr"]))["configure"][0]
    assert "--host=${LIB32_HOST}" in cfg
    assert "--libdir=/usr/lib32" in cfg
    assert "--disable-silent-rules" in cfg, "RT-8 compile-evidence mandate"
    assert "--prefix=/usr" in cfg, "recipe flags survive the lane"


# --------------------------------------------------------- make lane ----

def test_make_32_sources_profile():
    cmds = _cmds(make_mod.MakeStyle(), _pkg("make", "32"))
    assert cmds["build"][0].startswith(f"{base.LIB32_ENV_SOURCE}; ")
    assert cmds["install"][0].startswith(f"{base.LIB32_ENV_SOURCE}; ")


# ------------------------------------------- install staged-copy ----

def test_meson_32_install_is_staged_copy():
    # A full ninja install into ${DESTDIR} would ship headers/binaries that
    # collide with the 64-bit sibling — the lane installs to a private root
    # and allowlist-stages /usr/lib32 with the fail-loud assertion.
    cmds = _cmds(meson_mod.MesonStyle(), _pkg("meson", "32"))["install"]
    assert 'DESTDIR="$PWD/m32root"' in cmds[0]
    assert "lib32_stage_libs" in cmds[1] and "lib32_assert_only_lib32" in cmds[1]
    assert not any("DESTDIR=${DESTDIR} ninja" in c for c in cmds)


def test_autotools_32_install_is_staged_copy():
    cmds = _cmds(autotools_mod.AutotoolsStyle(), _pkg("autotools", "32"))["install"]
    assert 'DESTDIR="$PWD/m32root"' in cmds[0]
    assert "lib32_stage_libs" in cmds[1] and "lib32_assert_only_lib32" in cmds[1]


def test_make_32_install_is_staged_copy():
    cmds = _cmds(make_mod.MakeStyle(), _pkg("make", "32"))["install"]
    assert 'DESTDIR="$PWD/m32root"' in cmds[0]
    assert "lib32_stage_libs" in cmds[1]


def test_64_installs_unchanged():
    assert _cmds(meson_mod.MesonStyle(), _pkg("meson"))["install"] == [
        "DESTDIR=${DESTDIR} ninja -C build install"
    ]
    assert _cmds(autotools_mod.AutotoolsStyle(), _pkg("autotools"))["install"] == [
        "make DESTDIR=${DESTDIR} install"
    ]


# ---------------------------------------------- cmake fail-closed ----

def test_cmake_32_refuses_loudly():
    with pytest.raises(ValueError, match="no lib32 lane"):
        cmake_mod.CMakeStyle().configure(_pkg("cmake", "32"))


def test_cmake_64_unaffected():
    cmds = _cmds(cmake_mod.CMakeStyle(), _pkg("cmake"))
    assert any("cmake -B build" in c for c in cmds["configure"])
