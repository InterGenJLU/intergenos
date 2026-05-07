#!/bin/bash
# linux-firmware — Firmware files for Linux kernel drivers
# BLFS 13.0
#
# Provides firmware blobs for WiFi (Intel, Realtek, Broadcom, Atheros,
# MediaTek), GPU (AMD, Intel), audio (Intel SOF), Bluetooth, and more.
# Essential for bare metal hardware support.

configure() {
    set -e
    : # No configure step
}

build() {
    set -e
    : # No build step — pre-compiled firmware binaries
}

do_install() {
    set -e
    # Compressed install saves ~1.3GB (requires parallel + xz)
    # Kernel must have CONFIG_FW_LOADER_COMPRESS_XZ=y (verified in baseline)
    make DESTDIR="$DESTDIR" FIRMWAREDIR=/usr/lib/firmware install-xz

    # De-duplicate identical files with hardlinks (requires rdfind)
    make DESTDIR="$DESTDIR" FIRMWAREDIR=/usr/lib/firmware dedup
}
