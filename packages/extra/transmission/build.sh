#!/bin/bash
# transmission 4.1.1 — Fast, easy, free BitTorrent client
# Not in BLFS — built from upstream GitHub release.
#
# Components built (matches maintainer-approved 2026-04-08 strong-apps scope):
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
# System libraries (USE_SYSTEM_*=ON):
#   miniupnpc, libnatpmp, libdeflate — packaged as standalone tier:extra
#     libraries 2026-05-09 (commits authoring libdeflate/miniupnpc/libnatpmp).
#   libdht — packaged 2026-05-13 (Build #9 r#53 transmission halt: "No rule
#     to make target third-party/dht.bld/pfx/lib/libdht.a"). transmission
#     4.1.1's tr_add_external_auto_library DOES include BUILD_BYPRODUCTS,
#     but the gtk target's make-level dependency graph still doesn't see
#     the ExternalProject byproduct. Same Prime-Directive-aligned answer
#     as 2026-05-09: unbundle to a first-class system library (jech/dht
#     pinned to master HEAD 0bbb8f4a). transmission's existing FindDHT.cmake
#     handles the system path.
#
# Bundled libraries kept (no upstream packaging required for the build to
# succeed; transmission's CMake handles these without ExternalProject pain):
#   libb64, libutp, fmt, fast_float, rapidjson, small, utfcpp,
#   wide-integer, googletest (tests only).
# These build cleanly because they're either header-only or compiled directly
# into libtransmission as in-tree sources rather than via ExternalProject.

configure() {
    set -e
    # Build #5 audit: Halt #31 — GCC 15.2 -Wmaybe-uninitialized false
    # positives deep inside std::variant<...> template instantiations
    # (libtransmission/announce-list.cc + variant.h). Known GCC limitation
    # in template-heavy variant code. Narrow -Wno-error= keeps the
    # diagnostic visible without failing the build.
    #
    # Build #5 audit: Halt #32 — bundled third-party/dht include path not
    # wired by transmission's CMake. tr-dht.h does `#include <dht/dht.h>`
    # and third-party/dht/dht.h exists, so adding `-Ithird-party` to
    # CXXFLAGS restores the include path resolution. dht is a small in-tree
    # source (not an ExternalProject) so this fix remains valid under the
    # 2026-05-09 system-libs migration.
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
        -DUSE_SYSTEM_MINIUPNPC=ON                       \
        -DUSE_SYSTEM_NATPMP=ON                          \
        -DUSE_SYSTEM_DEFLATE=ON                         \
        -DUSE_SYSTEM_DHT=ON                             \
        -DUSE_SYSTEM_UTP=OFF                            \
        -DUSE_SYSTEM_B64=OFF                            \
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
