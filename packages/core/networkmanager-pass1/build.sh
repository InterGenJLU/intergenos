#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
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

# No post_install hook. Default enablement of every unit this package ships is
# decided in one place — intergenos-base-files'
# /usr/lib/systemd/system-preset/80-intergenos-enable.preset — and applied by the
# `systemctl preset-all` pass the image build and the installer both run. A
# `systemctl enable` here was a second voice for the same decision and the preset
# pass reverted it, so the tree stated one default and shipped another. Decided
# 2026-08-19: the preset files own this; recipes do not enable their own units.
