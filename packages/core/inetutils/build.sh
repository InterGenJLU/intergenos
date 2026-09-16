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

    # ping6 opens a raw socket and has no datagram fallback. A mode-755 copy
    # carrying cap_net_raw+ep was measured working as an ordinary user on
    # 2026-09-16; the same copy without the capability failed at socket open.
    # Stage it without setuid and restore the narrower capability after package
    # extraction. The pipeline retains modes (pkm/installer.py:1386), but not
    # xattr-based file capabilities end to end.
    chmod 755 "${DESTDIR}/usr/bin/ping6"

    # Declare the unprivileged-ping posture as OURS. systemd's shipped
    # default already opens the full gid range, which made the posture
    # right by accident; this file states it by declaration, and it is
    # what lets /usr/bin/ping ship without setuid.
    install -dm755 "${DESTDIR}/usr/lib/sysctl.d"
    cat > "${DESTDIR}/usr/lib/sysctl.d/50-ping-group-range.conf" << 'SYSCTL'
# Unprivileged ICMP Echo (ping) via datagram sockets, both address
# families, for every group. /usr/bin/ping therefore needs no special
# privilege; ping6 uses a raw socket and receives only cap_net_raw+ep
# from its package hook. Both binaries remain mode 0755.
net.ipv4.ping_group_range = 0 2147483647
SYSCTL
}

post_install() {
    set -e
    # Restore the capability on the deployed payload, never in the build root:
    # the package archive does not carry this extended attribute end to end.
    for _cap_tool in /usr/sbin/setcap /usr/sbin/getcap; do
        if [ ! -x "$_cap_tool" ]; then
            echo "ERROR: $_cap_tool is absent or not executable; cannot restore the ping6 capability" >&2
            exit 1
        fi
    done
    _cap_root="${PKM_PACKAGE_ROOT:-/}"
    _cap_target="${_cap_root%/}/usr/bin/ping6"
    if ! /usr/sbin/setcap cap_net_raw+ep "$_cap_target"; then
        echo "ERROR: failed to set cap_net_raw+ep on $_cap_target" >&2
        exit 1
    fi
    _installed_cap=$(/usr/sbin/getcap "$_cap_target")
    if [ "$_installed_cap" != "$_cap_target cap_net_raw=ep" ]; then
        echo "ERROR: $_cap_target capability read-back differs: ${_installed_cap:-<empty>}" >&2
        exit 1
    fi
}
