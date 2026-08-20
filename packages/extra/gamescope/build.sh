#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# gamescope 3.16.24 — Valve's SteamOS session micro-compositor.
#
# Built entirely from source with the vendored subprojects Valve pins at this tag,
# carried as secondary sources and extracted here (Rule 5). meson runs with
# --wrap-mode=nodownload so the build never reaches the network: any missing vendor
# is a loud, fail-closed error rather than a silent fetch.
#
# Minimal surface: OpenVR, input-emulation (libei), PipeWire capture, the SDL2 backend,
# AVIF screenshots, benchmarks and unit tests are all disabled. The DRM atomic backend
# and the Vulkan WSI layer (the reason gamescope exists on this box) stay on.
#
# NOTE (recorded for the next version bump): at 3.16.24 the reshade shader-effect
# runtime has no meson toggle — it is compiled unconditionally from src/reshade — so the
# reshade source is vendored. Its extra needs (luajit, sol2) are already satisfied:
# luajit is an in-tree system package and sol2 ships inside gamescope's own thirdparty/.

configure() {
    set -e
    local S="${IGOS_SOURCES:-/sources}"

    # --- git-submodule-shaped vendors: extract into their exact in-tree paths ---
    tar xf "${S}/gamescope-wlroots-c08d9943.tar.gz"      --strip-components=1 -C subprojects/wlroots
    tar xf "${S}/gamescope-libliftoff-8b08dc1c.tar.gz"   --strip-components=1 -C subprojects/libliftoff
    tar xf "${S}/gamescope-vkroots-5106d8a0.tar.gz"      --strip-components=1 -C subprojects/vkroots
    mkdir -p thirdparty/SPIRV-Headers
    tar xf "${S}/gamescope-spirv-headers-d790ced7.tar.gz" --strip-components=1 -C thirdparty/SPIRV-Headers
    mkdir -p src/reshade
    tar xf "${S}/gamescope-reshade-696b14cd.tar.gz"      --strip-components=1 -C src/reshade

    # --- meson-wrap vendors (glm, stb): populate the dir + the packagefile meson.build,
    #     then drop the .wrap so meson uses the pre-populated subproject offline. ---
    mkdir -p subprojects/glm
    tar xf "${S}/gamescope-glm-0af55cce.tar.gz"          --strip-components=1 -C subprojects/glm
    cp subprojects/packagefiles/glm/meson.build subprojects/glm/meson.build
    mkdir -p subprojects/stb
    tar xf "${S}/gamescope-stb-5736b15f.tar.gz"          --strip-components=1 -C subprojects/stb
    cp subprojects/packagefiles/stb/meson.build subprojects/stb/meson.build
    rm -f subprojects/glm.wrap subprojects/stb.wrap

    # Fail closed if any vendored source did not land where meson will look for it.
    for f in subprojects/wlroots/meson.build \
             subprojects/libliftoff/meson.build \
             subprojects/vkroots/meson.build \
             subprojects/glm/meson.build \
             subprojects/glm/glm/glm.hpp \
             subprojects/stb/meson.build \
             subprojects/stb/stb_image.h \
             thirdparty/SPIRV-Headers/include/spirv/unified1/spirv.h \
             src/reshade/source/effect_codegen_spirv.cpp; do
        [ -e "$f" ] || { echo "FATAL: vendored gamescope source missing: $f" >&2; exit 1; }
    done

    mkdir -p build
    cd    build
    meson setup ..                       \
          --prefix=/usr                  \
          --libdir=/usr/lib              \
          --buildtype=release            \
          --wrap-mode=nodownload         \
          -Denable_gamescope=true        \
          -Denable_gamescope_wsi_layer=true \
          -Denable_openvr_support=false  \
          -Denable_tests=false           \
          -Dinput_emulation=disabled     \
          -Dpipewire=disabled            \
          -Dsdl2_backend=disabled        \
          -Davif_screenshots=disabled    \
          -Dbenchmark=disabled
}

build() {
    set -e
    cd build
    ninja
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install
}
