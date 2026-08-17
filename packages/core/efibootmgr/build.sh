#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# efibootmgr 18 — UEFI boot entry manager
# BLFS 13.0

configure() {
    set -e
    # Remove -Werror from Make.defaults (hardcoded, not variable-based)
    # GCC 15 triggers warnings that -Werror promotes to fatal
    sed -i 's/-Werror //' Make.defaults
}

build() {
    set -e
    make EFIDIR=InterGenOS EFI_LOADER=grubx64.efi -j${IGOS_JOBS}
}

do_install() {
    set -e
    make EFIDIR=InterGenOS DESTDIR="$DESTDIR" install
}
