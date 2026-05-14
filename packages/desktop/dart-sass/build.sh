#!/bin/bash
# dart-sass 1.99.0 — Sass compiler (Dart Native AOT-compiled binary distribution)
#
# Upstream: github.com/sass/dart-sass
# License: MIT
#
# We ship the upstream pre-built linux-x64 binary release rather than
# bootstrapping Dart from source. Arch, Fedora, and Debian all follow
# this pattern (dart-sass-bin / sass / node-sass-embedded) — bootstrapping
# the Dart SDK from C++ would balloon the toolchain budget by hours for
# a tool that's only consumed at theme-package build time.
#
# Tarball layout (after builder's tar --strip-components=1):
#   sass        - bash wrapper that invokes `dart src/sass.snapshot`
#   src/        - dart-runtime + sass.snapshot (Dart Native AOT snapshot)
#
# Install layout:
#   /usr/lib/dart-sass/{sass,src/}    - shipped intact
#   /usr/bin/sass                     - symlink to /usr/lib/dart-sass/sass
#
# Consumers:
#   adw-gtk3-theme    - meson find_program('sass')
#   (any future package needing modern Sass with @use/@forward syntax)

configure() {
    set -e
    :
}

build() {
    set -e
    :
}

do_install() {
    set -e
    install -dm755 "${DESTDIR}/usr/lib/dart-sass"
    install -dm755 "${DESTDIR}/usr/bin"
    cp -a ./. "${DESTDIR}/usr/lib/dart-sass/"
    # Symlink stays valid post-pkm-install because the target path is
    # absolute against the on-target rootfs, not DESTDIR.
    ln -sf /usr/lib/dart-sass/sass "${DESTDIR}/usr/bin/sass"
}

post_install() {
    set -e
    :
}
