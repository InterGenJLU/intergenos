#!/bin/bash
# efibootmgr 18 — UEFI boot entry manager
# BLFS 13.0

configure() {
    : # No configure step — uses GNU Make directly
}

build() {
    make EFIDIR=InterGenOS EFI_LOADER=grubx64.efi -j${IGOS_JOBS}
}

do_install() {
    make EFIDIR=InterGenOS DESTDIR="$DESTDIR" install
}
