#!/bin/bash
# gtk4 4.20.3 — GTK 4 widget toolkit
# BLFS 13.0

configure() {
    set -e
    # BLFS required fixes
    sed "s@'doc'@& / 'gtk-${PKG_VERSION}'@" -i docs/reference/meson.build
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
}

post_install() {
    set -e
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
}
