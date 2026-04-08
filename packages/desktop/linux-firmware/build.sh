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
    make DESTDIR="$DESTDIR" FIRMWAREDIR=/usr/lib/firmware install
}
