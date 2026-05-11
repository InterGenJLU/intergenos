#!/bin/bash
# networkmanager-pass1 1.56.0 — NetworkManager (bootstrap, no desktop integration)
# First pass of 2-pass build. Disables iptables, polkit, bluez, introspection,
# vala, nmtui, modem_manager — anything that pulls tier:desktop deps.
# System networking only: bring up wired + WPA-supplicant WiFi at boot.

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
          -Dnmtui=false \
          -Dovs=false \
          -Dppp=false \
          -Dselinux=false \
          -Dqt=false \
          -Dbluez5=false \
          -Diptables=/usr/bin/false \
          -Dpolkit=false \
          -Dintrospection=false \
          -Dvapi=false \
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
}

post_install() {
    set -e
    # Enable NetworkManager for system networking at boot. (Full NM with
    # desktop integration supersedes this pass1 at install time; the
    # systemctl enable persists across the supersede.)
    systemctl enable NetworkManager.service 2>/dev/null || true

    # Disable systemd-networkd if enabled (conflicts with NM)
    systemctl disable systemd-networkd.service 2>/dev/null || true
    systemctl disable systemd-networkd-wait-online.service 2>/dev/null || true

    # Disable NetworkManager-wait-online — blocks boot indefinitely when no
    # network interface is immediately available.
    systemctl disable NetworkManager-wait-online.service 2>/dev/null || true
}
