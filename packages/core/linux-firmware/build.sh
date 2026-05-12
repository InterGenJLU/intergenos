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
    # Use-if-have semantics for install-time optimizations (parallel + rdfind
    # are at tier:base, which is built after tier:core; so during the first
    # build they aren't available, but a later rebuild or dev environment
    # may have them). Compressed install (install-xz) saves ~1.3GB but
    # requires GNU parallel via copy-firmware.sh -jN; the dedup target
    # requires rdfind. Both degrade to plain install / no-dedup when absent.
    # Kernel CONFIG_FW_LOADER_COMPRESS_XZ=y is set unconditionally so the
    # kernel can load either compressed or uncompressed blobs (verified in
    # baseline).
    if command -v parallel >/dev/null 2>&1; then
        make DESTDIR="$DESTDIR" FIRMWAREDIR=/usr/lib/firmware install-xz
    else
        make DESTDIR="$DESTDIR" FIRMWAREDIR=/usr/lib/firmware install
    fi

    if command -v rdfind >/dev/null 2>&1; then
        make DESTDIR="$DESTDIR" FIRMWAREDIR=/usr/lib/firmware dedup
    fi
}
