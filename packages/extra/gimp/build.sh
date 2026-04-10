#!/bin/bash
# gimp 3.0.6 — GNU Image Manipulation Program
# BLFS 13.0

configure() {
    # Patch applied by builder PATCH phase (package.yml) with SHA256 validation.

    mkdir gimp-build
    cd    gimp-build

    meson setup ..              \
          --prefix=/usr         \
          --libdir=/usr/lib     \
          --buildtype=release   \
          -D headless-tests=disabled
}

build() {
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
    cd gimp-build
    # Three tests (save-and-export, single-window-mode, ui) are known to fail
    ninja test || true
}

do_install() {
    cd gimp-build
    DESTDIR="$DESTDIR" ninja install
}

post_install() {
    gtk-update-icon-cache -qtf /usr/share/icons/hicolor 2>/dev/null || true
    update-desktop-database -q 2>/dev/null || true
}
