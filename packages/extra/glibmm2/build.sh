#!/bin/bash
# glibmm2 2.66.7 — C++ bindings for GLib 2.4 (GTK3-era, coexisting with newer glibmm)

configure() {
    meson setup build               \
          --prefix=/usr             \
          --libdir=/usr/lib         \
          --buildtype=release       \
          --wrap-mode=nodownload
}

build() {
    cd build
    ninja -j${IGOS_JOBS}
}

check() {
    cd build
    # Skip giomm_tls_client_test: it does DNS resolution to www.gnome.org. The
    # chroot has no network by security design. Upstream's own test
    # source acknowledges the offline-incompatibility with an unfinished TODO:
    #   tests/giomm_tls_client/main.cc:54-58
    #     "This happens if it could not resolve the name, for instance if we
    #      are not connected to the internet. TODO: Change this test so it can
    #      do something useful and succeed even if the testing computer is not
    #      connected to the internet."
    # All other 33 tests run normally. Drop this filter when upstream fixes the TODO.
    mapfile -t tests < <(meson test --list 2>/dev/null | grep -v 'giomm_tls_client_test')
    if [ ${#tests[@]} -eq 0 ]; then
        echo "ERROR: meson test --list returned no tests after filter" >&2
        return 1
    fi
    meson test --no-rebuild --print-errorlogs "${tests[@]}"
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
