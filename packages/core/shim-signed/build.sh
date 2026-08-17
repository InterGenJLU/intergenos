#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# shim-signed — extract Microsoft-signed shim from Fedora RPM
# Per D1-7 decision: piggyback on Fedora's shim until our own MS-signed
# shim ships post-Monday.

configure() {
    set -e
    # No configure step — we're extracting prebuilt binaries
    return 0
}

build() {
    set -e
    # Orchestrator's extract_source() (scripts/pkg-functions.sh) handles
    # rpm2cpio | cpio extraction for .rpm sources; PWD then contains the
    # RPM payload tree. Fedora's payload layout is
    # usr/lib/efi/shim/<nvr>/EFI/fedora/ as of 16.1-8 (was
    # boot/efi/EFI/fedora/ pre-16.1-8), so locate the binaries by path-glob
    # rather than a hardcoded prefix. Just verify they are present.
    SHIM_SRC=$(find . -type f -path '*/EFI/fedora/shimx64.efi' | head -1)
    MM_SRC=$(find . -type f -path '*/EFI/fedora/mmx64.efi' | head -1)
    test -n "$SHIM_SRC" || {
        echo "ERROR: shimx64.efi not found in extracted RPM" >&2
        exit 1
    }
    test -n "$MM_SRC" || {
        echo "ERROR: mmx64.efi not found in extracted RPM" >&2
        exit 1
    }
}

do_install() {
    set -e
    # Stage shim binaries under /usr/share/shim-signed/ where Forge expects.
    # Locate by path-glob (Fedora's RPM payload layout varies by release).
    SHIM_SRC=$(find . -type f -path '*/EFI/fedora/shimx64.efi' | head -1)
    MM_SRC=$(find . -type f -path '*/EFI/fedora/mmx64.efi' | head -1)
    CER_SRC=$(find . -type f -path '*/EFI/fedora/shimx64-fedora.cer' | head -1)
    install -d "$DESTDIR/usr/share/shim-signed"
    install -m 0644 "$SHIM_SRC" "$DESTDIR/usr/share/shim-signed/shimx64.efi"
    install -m 0644 "$MM_SRC"   "$DESTDIR/usr/share/shim-signed/mmx64.efi"
    if [ -n "$CER_SRC" ]; then
        install -m 0644 "$CER_SRC" "$DESTDIR/usr/share/shim-signed/shimx64-fedora.cer"
    fi
}
