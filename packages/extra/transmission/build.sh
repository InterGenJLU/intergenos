#!/bin/bash
# transmission 4.1.1 — Fast, easy, free BitTorrent client
# Not in BLFS — built from upstream GitHub release.
#
# Components built (matches owner-approved 2026-04-08 strong-apps scope):
#   * libtransmission   — core library
#   * transmission-daemon  — headless daemon (systemd-notify enabled)
#   * transmission-remote, transmission-show, transmission-create,
#     transmission-edit  — CLI utilities (ENABLE_UTILS=ON, default)
#   * transmission-cli   — legacy single-torrent CLI client (ENABLE_CLI=ON,
#     opt-in; deprecated upstream but kept for older-hardware use as the
#     project README documents)
#   * transmission-gtk   — GTK4 GUI front-end (matches GNOME 49 stack)
#
# Components SKIPPED (deliberate):
#   * Qt front-end (transmission-qt)  — InterGenOS desktop is GNOME/GTK,
#     pulling Qt purely for one optional client violates dependency policy.
#   * macOS native client            — N/A on Linux.
#
# Bundled (third-party/) libraries used as fallback because we have not yet
# packaged stand-alone versions and Transmission ships them in-tree:
#   miniupnpc, libnatpmp, libdeflate, libb64, dht, libutp, fmt, fast_float,
#   rapidjson, small, utfcpp, wide-integer, googletest (tests only).
# CMake AUTO mode picks the system version when available, otherwise
# falls back to the bundled copy. We supply system copies of:
#   openssl, curl, libevent, libpsl, glib2, gtkmm4 (+ glibmm, giomm),
#   gettext, libnotify, systemd, dbus.

configure() {
    set -e
    # Build #5 audit: Halt #31 — GCC 15.2 -Wmaybe-uninitialized false
    # positives deep inside std::variant<...> template instantiations
    # (libtransmission/announce-list.cc + variant.h). Known GCC limitation
    # in template-heavy variant code. Narrow -Wno-error= keeps the
    # diagnostic visible without failing the build.
    #
    # Halt #32 — bundled third-party/dht include path not wired by
    # transmission's CMake. tr-dht.h does `#include <dht/dht.h>` and
    # third-party/dht/dht.h exists, so adding `-Ithird-party` to CXXFLAGS
    # restores the include path resolution.
    export CXXFLAGS="${CXXFLAGS:-} -Wno-error=maybe-uninitialized -I$(pwd)/third-party"

    # Out-of-tree CMake build.
    cmake -S . -B build                                 \
        -DCMAKE_BUILD_TYPE=RelWithDebInfo               \
        -DCMAKE_CXX_FLAGS_RELWITHDEBINFO_INIT="-O2 -g -Wno-error=maybe-uninitialized -I$(pwd)/third-party" \
        -DCMAKE_INSTALL_PREFIX=/usr                     \
        -DCMAKE_INSTALL_LIBDIR=lib                      \
        -DCMAKE_INSTALL_SYSCONFDIR=/etc                 \
        -DCMAKE_INSTALL_LOCALSTATEDIR=/var              \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5              \
        -DENABLE_DAEMON=ON                              \
        -DENABLE_GTK=ON                                 \
        -DUSE_GTK_VERSION=4                             \
        -DENABLE_QT=OFF                                 \
        -DENABLE_MAC=OFF                                \
        -DENABLE_UTILS=ON                               \
        -DENABLE_CLI=ON                                 \
        -DENABLE_NLS=ON                                 \
        -DENABLE_TESTS=ON                               \
        -DENABLE_UTP=ON                                 \
        -DINSTALL_DOC=ON                                \
        -DINSTALL_WEB=ON                                \
        -DWITH_CRYPTO=openssl                           \
        -DWITH_INOTIFY=ON                               \
        -DWITH_SYSTEMD=ON                               \
        -DREBUILD_WEB=OFF                               \
        -W no-dev
}

build() {
    set -e
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}

post_install() {
    set -e
    # CMake installs:
    #   man pages       -> /usr/share/man/man1/transmission-*.1
    #   desktop file    -> /usr/share/applications/transmission-gtk.desktop
    #   icons (hicolor) -> /usr/share/icons/hicolor/{16,22,24,32,48,...}x*/apps/
    #   web client      -> /usr/share/transmission/web/
    #   systemd unit    -> /usr/lib/systemd/system/transmission-daemon.service
    # Refresh icon cache + desktop database (best-effort; non-fatal if missing).
    gtk-update-icon-cache -qtf /usr/share/icons/hicolor 2>/dev/null || true
    update-desktop-database -q 2>/dev/null || true
}

# do_test:
#   Transmission's test suite uses a bundled googletest (third-party/googletest),
#   so it is offline-friendly. Tests cover unit-level logic in libtransmission
#   (bencoding, magnet parsing, peer-message framing, RPC, etc.) and do NOT
#   require live tracker or peer connectivity. We run them via ctest; failures
#   are treated as build failures (tests are truth, per project rule).
#   Some tests open loopback sockets (announcer-udp, dns, lpd, net) — these
#   work inside the chroot since the loopback interface is always present.
check() {
    set -e
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        ctest --test-dir build --output-on-failure -j${IGOS_JOBS}
}
