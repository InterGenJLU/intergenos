#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# cups-browsed — the daemon that watches for printers advertised on the
# network and creates a matching local print destination for each one.
#
# WHY IT IS A SEPARATE PACKAGE. It used to be part of cups-filters. Verified
# against the tarball this tree pins (cups-filters 2.0.1): no file in it
# matches "browsed", and the built package on a machine from this tree lists
# 84 files with no such binary. Upstream moved the daemon into its own project
# at the 2.x boundary, so shipping it means shipping this.
#
# WHAT IT SHIPS AS: OFF. The unit carries an [Install] section, so it is a
# thing the install-time preset pass decides, and the tree's enable-list
# preset file disables it beside cups and avahi. It is turned on by the same
# Welcomer choice that turns those two on, and by nothing else — a machine
# whose owner has not asked for printing and network discovery does not run a
# daemon that builds print destinations out of what the network advertises.
#
# Build system verified against the PINNED source (2.1.1, extracted and read):
#   - autotools, with a generated ./configure in the release tarball;
#   - it asks cups-config for the scheduler's paths, and PKG_CHECK_MODULES for
#     libcupsfilters, libppd, avahi-client, avahi-glib, glib-2.0, gio-2.0 and
#     gio-unix-2.0 — the build dependencies this recipe declares;
#   - --with-browseremoteprotocols defaults to "dnssd"; it is passed
#     explicitly below because the alternative ("cups") makes the daemon listen
#     for the legacy CUPS browse protocol on udp/631, which this system does
#     not want on any machine;
#   - `make install` does NOT install the systemd unit: daemon/cups-browsed.service
#     is EXTRA_DIST only, and the install target ships an init script instead
#     when --with-rcdir names a directory. This recipe passes --with-rcdir=no
#     and installs the unit itself, so what lands is one file this tree chose
#     rather than an init script nothing on this system runs.

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    set -e
    ./configure                                       \
        --prefix=/usr                                 \
        --sbindir=/usr/sbin                           \
        --sysconfdir=/etc                             \
        --localstatedir=/var                          \
        --disable-static                              \
        --with-rcdir=no                               \
        --with-browseremoteprotocols=dnssd
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    # The unit upstream distributes but does not install (see the note above).
    install -Dm644 daemon/cups-browsed.service \
                   "$DESTDIR/usr/lib/systemd/system/cups-browsed.service"

    install -Dm644 "$BUILD_DIR/files/usr/lib/systemd/system/cups-browsed.service.d/20-hardening.conf" \
                   "$DESTDIR/usr/lib/systemd/system/cups-browsed.service.d/20-hardening.conf"

    # What this package must be true about, asserted against what was staged.
    #
    # 1. The daemon must be there under the name the unit starts.
    if [ ! -x "$DESTDIR/usr/sbin/cups-browsed" ]; then
        echo "ERROR: /usr/sbin/cups-browsed was not staged, so the unit would" >&2
        echo "       start a path that does not exist." >&2
        exit 1
    fi

    # 2. The unit must carry the [Install] section the preset file decides
    #    against. Without it the unit could not be enabled at all and the
    #    opt-in switch would silently do nothing.
    if ! grep -q '^\[Install\]' "$DESTDIR/usr/lib/systemd/system/cups-browsed.service"; then
        echo "ERROR: the staged unit has no [Install] section, so nothing can" >&2
        echo "       enable it and the opt-in switch would be inert." >&2
        exit 1
    fi

    # 3. The shipped configuration must listen over DNS-SD only. The legacy
    #    CUPS browse protocol is a udp/631 listener, and this system's firewall
    #    drops inbound by default — a daemon waiting for packets that cannot
    #    arrive is surface with no function.
    conf="$DESTDIR/etc/cups/cups-browsed.conf"
    if [ ! -f "$conf" ]; then
        echo "ERROR: $conf was not staged; the scheduler's configuration" >&2
        echo "       directory is not where cups-config said it would be." >&2
        exit 1
    fi
    if ! grep -qE '^BrowseRemoteProtocols +dnssd *$' "$conf"; then
        echo "ERROR: the staged configuration does not listen over DNS-SD only:" >&2
        grep -n 'BrowseRemoteProtocols' "$conf" >&2
        exit 1
    fi
    echo "[posture] staged configuration: BrowseRemoteProtocols dnssd; unit ships disabled by preset"
}
