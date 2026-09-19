#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# sane-airscan — the SANE backend that speaks the two protocols a modern
# scanner actually offers: eSCL (Apple AirScan) and WSD. It is a shared
# library the SANE loader opens, not a daemon: nothing runs until an
# application asks to scan.
#
# Build system verified against the PINNED source (0.99.38, extracted and
# read):
#   - a hand-written Makefile, no configure. It asks pkg-config for
#     avahi-client, libxml-2.0, gnutls, libjpeg, libpng and libtiff-4, and it
#     asks pkg-config for sane-backends' libdir so the backend lands beside
#     the other SANE backends;
#   - `make install` stages the backend, the airscan-discover tool, the two
#     man pages and two configuration files — /etc/sane.d/airscan.conf and
#     /etc/sane.d/dll.d/airscan, the second of which is what makes the SANE
#     loader open this backend at all. Both are copied only if absent, which
#     is upstream being careful with an existing installation; on a clean
#     DESTDIR both are written;
#   - CFLAGS carry -Werror upstream. That is kept: a warning in a network-
#     facing parser is worth stopping for.
#
# WHAT IT DOES NOT DO: it opens no port and starts no daemon. It looks for
# scanners over DNS-SD when an application asks it to, which on this system
# means it finds network scanners only while the machine's mDNS responder is
# on — the Welcomer's Network Discovery choice. Over USB it needs no responder
# at all when the scanner is presented locally by ipp-usb.

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    # What this package must be true about, asserted against what was staged.
    #
    # 1. The backend library, under the directory the SANE loader searches.
    if [ ! -f "$DESTDIR/usr/lib/sane/libsane-airscan.so.1" ]; then
        echo "ERROR: the backend was not staged under /usr/lib/sane; the SANE" >&2
        echo "       loader would never open it." >&2
        find "$DESTDIR" -name 'libsane-airscan*' >&2
        exit 1
    fi

    # 2. The registration file. Without it the library is on disk and the
    #    loader does not know it exists — the exact silent half-install this
    #    assertion is here to prevent.
    if ! grep -q '^airscan$' "$DESTDIR/etc/sane.d/dll.d/airscan"; then
        echo "ERROR: /etc/sane.d/dll.d/airscan does not register the backend:" >&2
        cat "$DESTDIR/etc/sane.d/dll.d/airscan" >&2
        exit 1
    fi

    # 3. The shipped configuration must not turn the discovery behaviour into
    #    something this system did not decide. Upstream's default is to
    #    discover over both protocols and to trust nothing implicitly; the
    #    assertion is that the file was staged at all, and that it names no
    #    scanner address of its own.
    conf="$DESTDIR/etc/sane.d/airscan.conf"
    if [ ! -f "$conf" ]; then
        echo "ERROR: $conf was not staged." >&2
        exit 1
    fi
    if grep -qE '^\s*[^#[:space:]]+\s*=\s*https?://' "$conf"; then
        echo "ERROR: the shipped configuration names a scanner by address:" >&2
        grep -nE '^\s*[^#[:space:]]+\s*=\s*https?://' "$conf" >&2
        echo "       A shipped file must not point this machine at a host" >&2
        echo "       nobody on it chose." >&2
        exit 1
    fi
    echo "[registration] backend staged under /usr/lib/sane and registered in /etc/sane.d/dll.d/airscan"
}
