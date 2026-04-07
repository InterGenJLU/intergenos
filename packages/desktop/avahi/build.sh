#!/bin/bash
# avahi 0.8 — Service Discovery for Linux using mDNS/DNS-SD
# BLFS 13.0

configure() {
    # Apply IPv6 race condition fix (BLFS required patch)
    patch -Np1 -i ../avahi-0.8-ipv6_race_condition_fix-1.patch

    # Fix security vulnerability in avahi-daemon (BLFS)
    sed -i '426a if (events & AVAHI_WATCH_HUP) { \
client_free(c); \
return; \
}' avahi-daemon/simple-protocol.c

    ./configure --prefix=/usr        \
                --sysconfdir=/etc    \
                --localstatedir=/var \
                --disable-static     \
                --disable-libevent   \
                --disable-mono       \
                --disable-monodoc    \
                --disable-python     \
                --disable-qt3        \
                --disable-qt4        \
                --disable-qt5        \
                --enable-core-docs   \
                --with-distro=none   \
                --with-dbus-system-address='unix:path=/run/dbus/system_bus_socket'
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}

post_install() {
    # Create avahi system user and group (BLFS: uid/gid 84)
    groupadd -fg 84 avahi 2>/dev/null || true
    useradd -c "Avahi Daemon Owner" -d /run/avahi-daemon -u 84 \
            -g avahi -s /bin/false avahi 2>/dev/null || true

    # Create privileged access group for Avahi clients
    groupadd -fg 86 netdev 2>/dev/null || true

    systemctl enable avahi-daemon.service 2>/dev/null || true
}
