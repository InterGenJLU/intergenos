#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# dnsmasq 2.93 — lightweight DNS forwarder and DHCP server
# Not in BLFS — InterGenOS extra tier (virtualization stack)
#
# Provides DNS/DHCP for libvirt's default NAT network (libvirt spawns
# dnsmasq per virtual network itself — no running system service is
# required for that). A systemd unit is shipped for standalone use and
# stays disabled by default via the 99-intergenos-default-disable preset
# catch-all (unconditional network-facing daemon — the user enables it
# deliberately). Plain hand-written upstream Makefile; vanilla feature
# set (no dbus/idn compile-time options).

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    set -e
    # No configure step: dnsmasq uses a plain Makefile with PREFIX.
    :
}

build() {
    set -e
    make -j"$(nproc)" PREFIX=/usr
}

do_install() {
    set -e
    make DESTDIR="${DESTDIR}" PREFIX=/usr install
    install -Dm644 "$BUILD_DIR/dnsmasq.service" \
        "$DESTDIR/usr/lib/systemd/system/dnsmasq.service"
    install -Dm644 dnsmasq.conf.example \
        "$DESTDIR/usr/share/doc/dnsmasq/dnsmasq.conf.example"
}
