#!/bin/bash
# networkmanager 1.56.0 — Network connection manager
# BLFS 13.0

configure() {
    set -e
    # Fix Python scripts that reference python2
    grep -rl '^#!.*python$' | xargs sed -i '1s/python/&3/' 2>/dev/null || true

    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dlibaudit=no \
          -Dmodem_manager=false \
          -Dnm_cloud_setup=false \
          -Dnbft=false \
          -Dnmtui=true \
          -Dovs=false \
          -Dppp=false \
          -Dselinux=false \
          -Dqt=false \
          -Dsession_tracking=systemd \
          -Dtests=no \
          -Ddocs=false
}

build() {
    set -e
    cd build
    ninja
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install

    # Drop initrd-variant unit files. Upstream ships these for dracut-based
    # distros that run NetworkManager inside the initramfs (remote-root via
    # DHCP). InterGenOS does not — our initramfs uses busybox + a custom
    # init.sh; networking is brought up post-pivot by NM proper. Shipping
    # the *-initrd.service units in the final root causes a D-Bus name
    # collision on org.freedesktop.NetworkManager — systemd refuses to load
    # either unit ("Two services allocated for the same bus name"), NM
    # stays dead at boot, GNOME's panel applet shows "Network unavailable"
    # while systemd-networkd silently does the actual work.
    rm -f "${DESTDIR}/usr/lib/systemd/system/NetworkManager-initrd.service"
    rm -f "${DESTDIR}/usr/lib/systemd/system/NetworkManager-config-initrd.service"
    rm -f "${DESTDIR}/usr/lib/systemd/system/NetworkManager-wait-online-initrd.service"
}

post_install() {
    set -e
    # Enable NetworkManager for GNOME desktop integration
    # (replaces systemd-networkd which is server-oriented)
    systemctl enable NetworkManager.service 2>/dev/null || true

    # Disable systemd-networkd if enabled (conflicts with NM)
    systemctl disable systemd-networkd.service 2>/dev/null || true
    systemctl disable systemd-networkd-wait-online.service 2>/dev/null || true

    # Disable NetworkManager-wait-online — blocks boot indefinitely when no
    # network interface is immediately available (USB NIC unplugged, WiFi not
    # configured). NM still manages interfaces asynchronously without it.
    systemctl disable NetworkManager-wait-online.service 2>/dev/null || true
}
