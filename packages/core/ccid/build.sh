#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# ccid 1.8.2 — generic USB CCID (Chip/Smart Card Interface Device) driver
#
# The reader-driver half of the InterGenOS smartcard/PIV signing stack. ccid
# builds libccid.so into pcscd's USB driver-bundle directory
# (/usr/lib/pcsc/drivers/ifd-ccid.bundle); pcscd loads it on hotplug to talk to
# CCID-class readers, including the Nitrokey NK1 used for the PIV signing
# ceremonies. Depends on pcsc-lite (for libpcsclite + usbdropdir/serialconfdir
# pkg-config variables) and libusb (USB I/O).
#
# Build system: meson (upstream 1.8.2 ships meson.build, no autotools).
# Verified against the real pinned tarball's meson.build + meson.options:
#   - bundle dir derives from `pkg-config --variable=usbdropdir libpcsclite`,
#     which pcsc-lite sets to /usr/lib/pcsc/drivers (matched in pcsc-lite's
#     configure), so the bundle lands at
#     /usr/lib/pcsc/drivers/ifd-ccid.bundle/Contents/<uname>/libccid.so.
#   - <uname> on Linux is the literal "Linux" (meson runs `uname` with no
#     args -> kernel name), hence .../Contents/Linux/libccid.so.
#   - udev-rules=true installs 92_pcscd_ccid.rules into udevdir/rules.d.

configure() {
    set -e
    # Defaults are correct for our case (usb readers on, serial off,
    # udev-rules on). No driver-path override needed: it is inherited from
    # pcsc-lite's pkg-config usbdropdir.
    meson setup build         \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release
}

build() {
    set -e
    ninja -C build
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" ninja -C build install
}
