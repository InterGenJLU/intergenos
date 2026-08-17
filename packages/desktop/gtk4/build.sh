#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# gtk4 4.20.3 — GTK 4 widget toolkit
# BLFS 13.0

configure() {
    set -e
    # BLFS required fixes
    sed "s@'doc'@& / 'gtk-${PKG_VERSION}'@" -i docs/reference/meson.build

    # InterGenOS patches — applied from packages/desktop/gtk4/patches/.
    # The build environment sets IGOS_PACKAGE_DIR to the package recipe
    # directory; fall back to the canonical workspace path if unset (some
    # surgical-rebuild invocations don't propagate it).
    local patches_dir="${IGOS_PACKAGE_DIR:-/mnt/intergenos/packages/desktop/gtk4}/patches"
    if [ -d "$patches_dir" ]; then
        for p in "$patches_dir"/*.patch; do
            [ -f "$p" ] || continue
            echo "Applying patch: $(basename "$p")"
            patch -p1 -i "$p"
        done
    fi

    mkdir -p build
    cd    build

    # Explicit feature flags. Build #5 audit found tracker/colord/
    # cloudproviders/print-cpdb were silently disabled because we relied
    # on meson's "auto" detection — when build order misordered the deps,
    # auto fell back to disabled. =enabled makes meson HALT if a dep is
    # missing, which is what we want (tests-as-truth principle applied
    # to feature detection).
    #
    # print-cpdb=disabled: libcpdb is not yet in our tree (v1.0+1 backlog).
    # The CUPS backend is still enabled and covers printing.
    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          --wrap-mode=nofallback \
          -Dbroadway-backend=true   \
          -Dx11-backend=true        \
          -Dwayland-backend=true    \
          -Dintrospection=enabled   \
          -Dvulkan=enabled          \
          -Dcolord=enabled          \
          -Dcloudproviders=enabled  \
          -Dtracker=enabled         \
          -Dprint-cups=enabled      \
          -Dprint-cpdb=disabled
}

build() {
    set -e
    cd build
    ninja
}

check() {
    set -e
    cd build
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        ninja test
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install

    # Decided 2026-07-17: the toolkit demo/sample launchers ship for
    # developer use, not as end-user apps — hide them from the app menu
    # (NoDisplay=true). test -f fails loudly if upstream moves/renames one.
    for demo in org.gtk.Demo4 org.gtk.PrintEditor4 org.gtk.WidgetFactory4 \
                org.gtk.gtk4.NodeEditor; do
        f="$DESTDIR/usr/share/applications/$demo.desktop"
        test -f "$f"
        sed -i '/^NoDisplay=/d' "$f"
        sed -i '/^\[Desktop Entry\]/a NoDisplay=true' "$f"
    done
}

post_install() {
    set -e
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
}
