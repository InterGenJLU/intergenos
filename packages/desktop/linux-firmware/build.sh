#!/bin/bash
# linux-firmware — Firmware files for Linux kernel drivers
# BLFS 13.0
#
# Provides firmware blobs for WiFi (Intel, Realtek, Broadcom, Atheros,
# MediaTek), GPU (AMD, Intel), audio (Intel SOF), Bluetooth, and more.
# Essential for bare metal hardware support.

configure() {
    : # No configure step
}

build() {
    : # No build step — pre-compiled firmware binaries
}

do_install() {
    # install-xz compresses firmware blobs (~900MB vs ~2.2GB uncompressed)
    # Requires CONFIG_FW_LOADER_COMPRESS_XZ=y in the kernel
    make DESTDIR="$DESTDIR" FIRMWAREDIR=/usr/lib/firmware install-xz

    # De-duplicate identical files with hardlinks
    make DESTDIR="$DESTDIR" FIRMWAREDIR=/usr/lib/firmware dedup
}
