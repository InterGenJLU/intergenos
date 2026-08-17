#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# pcsc-lite 2.5.1 — PC/SC smart card daemon (pcscd) + libpcsclite middleware
#
# Foundational layer of the InterGenOS smartcard/PIV signing stack: pcscd is
# the daemon every PKCS#11 module (OpenSC) talks to to reach a reader, and the
# CCID driver bundle (packages/core/ccid) drops into pcscd's usbdropdir
# (/usr/lib/pcsc/drivers). This stack exists so InterGenOS can drive the NK1
# Nitrokey PIV signing ceremonies natively.
#
# Build system: meson (upstream 2.5.1 ships meson.build, no autotools).
# Verified against the real pinned tarball's meson.options + meson.build.

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    set -e
    # Option surface verified against meson.options in pcsc-lite-2.5.1:
    #   libsystemd  -> socket activation + auto-exit (the upstream-intended,
    #                  no-idle-daemon model: pcscd starts on first client
    #                  connect to /run/pcscd/pcscd.comm and exits when idle).
    #   libudev     -> USB hotplug via libudev (preferred over libusb hotplug;
    #                  systemd package provides udev). libusb is still linked
    #                  for the actual USB I/O via the CCID driver.
    #   polkit      -> enforce reader access control (polkit is in-tree).
    #   systemdunit=system -> install units to the system unit dir, not user.
    # With libsystemd=true the units + sysusers.conf install to the dirs
    # exposed by `pkg-config systemd` (systemdsystemunitdir=/usr/lib/systemd/
    # system, sysusersdir=/usr/lib/sysusers.d) — see meson.build:146-159.
    meson setup build           \
          --prefix=/usr         \
          --libdir=/usr/lib     \
          --buildtype=release   \
          -D libsystemd=true    \
          -D libudev=true       \
          -D libusb=false       \
          -D polkit=true        \
          -D systemdunit=system \
          -D usbdropdir=/usr/lib/pcsc/drivers
}

build() {
    set -e
    ninja -C build
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" ninja -C build install

    # pcsc-lite ships pcscd-sysusers.conf (creates the locked `pcscd` system
    # user the hardened unit runs as) into /usr/lib/sysusers.d automatically;
    # the units (pcscd.socket + pcscd.service) install to /usr/lib/systemd/
    # system. We add ONLY the preset below.
    #
    # Enable pcscd.SOCKET, not the service: socket activation means no daemon
    # runs until a client actually opens the PC/SC communication socket, and
    # --auto-exit lets it shut down when idle. That is the default-deny posture
    # for a USB-facing daemon (security-only alignment): zero attack surface at
    # rest, brought up on demand for a signing ceremony, torn down after.
    install -Dm644 "$BUILD_DIR/90-pcsc-lite.preset" \
                   "$DESTDIR/usr/lib/systemd/system-preset/90-pcsc-lite.preset"
}
