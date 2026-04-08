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
    # Use copy-firmware.sh directly (avoids parallel/rdfind deps)
    # Plain install — firmware blobs uncompressed (~2GB)
    # Future: add xz compression when parallel is available
    ./copy-firmware.sh "${DESTDIR}/usr/lib/firmware"
}
