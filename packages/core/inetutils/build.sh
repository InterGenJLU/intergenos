#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Inetutils 2.7
# LFS 13.0 Section 8.42

configure() {
    set -e
    # Fix building with gcc-14.1 or later
    sed -i 's/def HAVE_TERMCAP_TGETENT/ 1/' telnet/telnet.c

    # --disable-traceroute: the dedicated traceroute package (base tier) ships the
    # full-featured traceroute and owns /usr/bin/traceroute; inetutils' minimal one
    # would collide on that path.
    ./configure --prefix=/usr        \
        --bindir=/usr/bin            \
        --localstatedir=/var         \
        --disable-logger             \
        --disable-whois              \
        --disable-rcp                \
        --disable-rexec              \
        --disable-rlogin             \
        --disable-rsh                \
        --disable-servers            \
        --disable-traceroute
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    make check
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
    mkdir -pv "${DESTDIR}/usr/sbin"
    mv -v "${DESTDIR}/usr/bin/ifconfig" "${DESTDIR}/usr/sbin/ifconfig"

    # ping runs UNPRIVILEGED: with net.ipv4.ping_group_range open (the
    # sysctl declaration shipped below), inetutils ping uses an ICMP
    # datagram socket — measured live 2026-07-28: a mode-755 copy pings
    # cleanly as a normal user. Dropping setuid removes a root-boundary
    # binary the posture never needed.
    chmod 755 "${DESTDIR}/usr/bin/ping"

    # ping6 KEEPS setuid — measured the same day: inetutils ping6 opens
    # a raw socket only ("raw socket: Operation not permitted" at mode
    # 755, no datagram fallback), so dropping the bit would break IPv6
    # ping for every non-root user. Asserted explicitly rather than
    # inherited from make install; capability over posture.
    # (File capabilities remain not adopted: the pipeline preserves
    # setuid via tarball metadata but not xattr-based caps end-to-end;
    # pkm restores modes post-extract, see pkm/installer.py:475-490.)
    chmod 4755 "${DESTDIR}/usr/bin/ping6"

    # Declare the unprivileged-ping posture as OURS. systemd's shipped
    # default already opens the full gid range, which made the posture
    # right by accident; this file states it by declaration, and it is
    # what lets /usr/bin/ping ship without setuid.
    install -dm755 "${DESTDIR}/usr/lib/sysctl.d"
    cat > "${DESTDIR}/usr/lib/sysctl.d/50-ping-group-range.conf" << 'SYSCTL'
# Unprivileged ICMP Echo (ping) via datagram sockets, both address
# families, for every group. This is why /usr/bin/ping carries no
# setuid bit. ping6 remains setuid: inetutils ping6 supports raw
# sockets only (no datagram fallback).
net.ipv4.ping_group_range = 0 2147483647
SYSCTL
}
