#!/bin/bash
# networkmanager 1.56.0 — Network connection manager
# BLFS 13.0

configure() {
    # Fix Python scripts that reference python2
    grep -rl '^#!.*python$' | xargs sed -i '1s/python/&3/' 2>/dev/null || true

    mkdir build
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
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}

post_install() {
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
