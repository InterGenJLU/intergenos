#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# gimp 3.2.4 — GNU Image Manipulation Program
# BLFS 13.x

configure() {
    set -e
    # The gexiv2-0.16 migration is applied in the builder PATCH phase
    # (package.yml, sha-pinned) via GIMP's OWN bundled fix — see the package.yml
    # patch comment. The 3.0.6 L20 seds are RETIRED: 3.2.4 changed the probe
    # shape (a new `<0.15` maxver clamp, plus API-versioned GIR/vapi names), so
    # the two-line sed is insufficient; the bundled patch is the complete,
    # upstream-authored, version-matched migration and runs before this phase.

    mkdir -p gimp-build
    cd    gimp-build

    meson setup ..              \
          --prefix=/usr         \
          --libdir=/usr/lib     \
          --buildtype=release   \
          -D headless-tests=disabled
}

build() {
    set -e
    cd gimp-build
    # GIMP's splash generation runs gimp-console with Python-Fu batch
    # mode, which doesn't work in a chroot. Pre-extract the splash from
    # the source XCF so the custom_target is satisfied without running GIMP.
    # ImageMagick can handle XCF files directly.
    if command -v magick >/dev/null 2>&1; then
        xz -dk ../gimp-data/images/gimp-splash.xcf.xz 2>/dev/null || true
        magick ../gimp-data/images/gimp-splash.xcf \
            gimp-data/images/gimp-splash.png 2>/dev/null || true
    fi

    ninja
}

check() {
    set -e
    cd gimp-build
    # Three tests (save-and-export, single-window-mode, ui) are known to fail
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        ninja test
}

do_install() {
    set -e
    cd gimp-build
    DESTDIR="$DESTDIR" ninja install
}

post_install() {
    set -e
    gtk-update-icon-cache -qtf /usr/share/icons/hicolor 2>/dev/null || true
    update-desktop-database -q 2>/dev/null || true
}
