#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# sane-backends — the scanner driver collection and the libsane library every
# scanning application links against. Without it a scanner is a USB device
# nothing on the machine knows how to read.
#
# Build system verified against the PINNED source (1.4.0 release tarball,
# extracted and read, not assumed):
#   - autotools, with a generated ./configure in the release asset (the plain
#     repository archive has configure.ac only, which is why the recipe pins
#     the release asset — see package.yml);
#   - the scanner access rule is GENERATED during the build by tools/sane-desc
#     into tools/udev/libsane.rules and is NOT installed by `make install`, so
#     this recipe installs it at the path udev reads and asserts it is there;
#   - the network scanner-sharing daemon's units (saned.socket, saned@.service)
#     are BUILT but not installed — both are EXTRA_DIST in frontend/Makefile.am,
#     so `make install` alone ships saned with nothing able to start it. This
#     recipe stages both (measured here 2026-09-19: the install target left
#     /usr/lib/systemd/system empty).
#
# WHAT SHIPS OFF, and why it is worth saying plainly: saned lets OTHER
# machines use this machine's scanner. Its socket listens on tcp/6566 and its
# unit carries an [Install] section, so it is a thing the install-time preset
# pass decides — and the tree's enable-list preset file disables it, beside
# the print scheduler and the mDNS responder. Local scanning does not use it:
# an application talks to libsane in its own process, and libsane talks to the
# device. The daemon is here for the person who deliberately wants to share a
# scanner, and for nobody else.

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    set -e
    # BACKENDS is pinned to an explicit list, and the optional libraries are
    # answered explicitly, so what this package contains is decided HERE and
    # not by what happens to be installed on the machine that builds it.
    # Measured on this machine 2026-09-19: an unpinned configure found
    # libgphoto2, libcurl, poppler-glib, libxml-2.0 and libv4l1 on the host and
    # silently built three more backends against them.
    #
    # The list is upstream's ALL_BACKENDS with three removed:
    #   escl    — driverless network and USB scanning is sane-airscan's job in
    #             this tree, and it is the better implementation of the same
    #             protocol; two eSCL backends in one dll.conf means the one
    #             that answers first wins, which is not a thing to decide by
    #             accident. Removing it also removes the libcurl, poppler-glib
    #             and libxml-2.0 dependencies this package would otherwise take.
    #   gphoto2 — treats a camera as a scanner. Cameras are handled by the
    #             desktop's own camera support, and the backend DOES NOT
    #             COMPILE with this toolchain: gphoto2.c:589 assigns through a
    #             pointer strchr() returns as const, which is an error rather
    #             than a warning here (measured 2026-09-19).
    #   v4l     — treats a video capture device as a scanner; a webcam is not a
    #             scanner, and it pulls libv4l in for a feature nothing asks for.
    BACKENDS="abaton agfafocus apple artec artec_eplus48u as6e \
avision bh canon canon630u canon_dr canon_lide70 cardscan \
coolscan coolscan2 coolscan3 dc25 dc210 dc240 \
dell1600n_net dmc epjitsu epson epson2 epsonds fujitsu \
genesys gt68xx hp hp3500 hp3900 hp4200 hp5400 \
hp5590 hpljm1005 hs2p ibm kodak kodakaio kvs1025 kvs20xx \
kvs40xx leo lexmark lexmark_x2600 ma1509 magicolor \
matsushita microtek microtek2 mustek \
mustek_usb mustek_usb2 nec net niash pie pieusb \
pixma plustek plustek_pp ricoh ricoh2 rts8891 s9036 \
sceptre sharp sm3600 sm3840 snapscan sp15c st400 \
stv680 tamarack teco1 teco2 teco3 test u12 umax \
umax_pp umax1220u xerox_mfp p5" \
    ./configure                                       \
        --prefix=/usr                                 \
        --sbindir=/usr/sbin                           \
        --sysconfdir=/etc                             \
        --localstatedir=/var                          \
        --disable-static                              \
        --with-systemd                                \
        --with-usb                                    \
        --with-avahi                                  \
        --without-snmp                                \
        --without-libcurl                             \
        --without-poppler-glib                        \
        --without-v4l

    # A pinned list that silently lost an entry would ship fewer drivers than
    # this recipe says it does, so the three removals are read back out of the
    # configure output rather than assumed.
    for gone in escl gphoto2 v4l canon_pp hpsj5s mustek_pp pint qcam; do
        if grep -qE "^BACKENDS *=.*\\b$gone\\b" Makefile 2>/dev/null; then
            echo "ERROR: the $gone backend is still selected after configure." >&2
            grep -nE "^BACKENDS *=" Makefile >&2
            exit 1
        fi
    done
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    # The scanner access rule. udev applies it when a scanner is plugged in,
    # which is what lets the logged-in user read the device without being
    # given blanket USB access. It is generated during the build and is not
    # part of `make install`.
    if [ ! -f tools/udev/libsane.rules ]; then
        echo "ERROR: the build produced no tools/udev/libsane.rules, so a" >&2
        echo "       scanner plugged into an installed machine would be" >&2
        echo "       readable only by root." >&2
        exit 1
    fi
    install -Dm644 tools/udev/libsane.rules \
                   "$DESTDIR/usr/lib/udev/rules.d/60-libsane.rules"

    # The sharing daemon's units. Upstream BUILDS them (saned@.service from
    # saned@.service.in, and saned.socket ships as-is) but its install target
    # does not stage them: both are EXTRA_DIST only, so a package that just
    # runs `make install` ships the daemon with no way to start it and no way
    # for the preset file to decide it. They are staged here, and asserted
    # below.
    install -Dm644 frontend/saned.socket \
                   "$DESTDIR/usr/lib/systemd/system/saned.socket"
    install -Dm644 frontend/saned@.service \
                   "$DESTDIR/usr/lib/systemd/system/saned@.service"

    # The hardening drop-in for the sharing daemon's per-connection unit.
    install -Dm644 "$BUILD_DIR/files/usr/lib/systemd/system/saned@.service.d/20-hardening.conf" \
                   "$DESTDIR/usr/lib/systemd/system/saned@.service.d/20-hardening.conf"

    # What this package must be true about, asserted against what was staged.
    #
    # 1. The library every scanning application links against.
    if [ ! -e "$DESTDIR/usr/lib/libsane.so.1" ]; then
        echo "ERROR: libsane.so.1 was not staged; nothing that scans can link." >&2
        exit 1
    fi

    # 2. The sharing daemon's units must be present AND must carry the
    #    [Install] section, because the preset file decides them off. A unit
    #    without one could not be decided, and the tree would state a default
    #    it does not enforce.
    for unit in saned.socket; do
        path="$DESTDIR/usr/lib/systemd/system/$unit"
        if [ ! -f "$path" ]; then
            echo "ERROR: $unit was not staged, so the preset file's decision" >&2
            echo "       about network scanner sharing would refer to nothing." >&2
            exit 1
        fi
        if ! grep -q '^\[Install\]' "$path"; then
            echo "ERROR: $unit carries no [Install] section; the preset file" >&2
            echo "       cannot decide it." >&2
            exit 1
        fi
    done

    # 3. The sharing daemon must not be listening on anything but the port its
    #    own socket names, and that port must be the one the preset decision
    #    and the firewall posture were written about.
    if ! grep -q '^ListenStream=6566$' "$DESTDIR/usr/lib/systemd/system/saned.socket"; then
        echo "ERROR: the staged saned.socket does not listen on 6566:" >&2
        grep -n 'ListenStream' "$DESTDIR/usr/lib/systemd/system/saned.socket" >&2
        echo "       The preset comment and this system's firewall posture were" >&2
        echo "       written about that port; re-decide both before changing it." >&2
        exit 1
    fi

    # The staged frontend links the staged library, and neither is installed
    # on the machine running this build, so the loader is pointed at the
    # staging root for this one call. Measured 2026-09-19 without it:
    # "error while loading shared libraries: libsane.so.1".
    _reported="$(LD_LIBRARY_PATH="$DESTDIR/usr/lib" \
                 "$DESTDIR/usr/bin/scanimage" --version 2>&1 || true)"
    case "$_reported" in
        *"${PKG_VERSION}"*) ;;
        *)
            echo "ERROR: the staged scanimage reports '${_reported}', which does" >&2
            echo "       not name the pinned version ${PKG_VERSION}." >&2
            exit 1
            ;;
    esac
    echo "[version] staged scanimage reports: ${_reported}"
    echo "[posture] saned.socket staged with [Install] and ListenStream=6566; the preset file ships it disabled"
}
