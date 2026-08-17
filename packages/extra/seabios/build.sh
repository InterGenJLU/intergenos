#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# seabios 1.17.0 — legacy BIOS firmware for QEMU guests
# Not in BLFS — InterGenOS extra tier (virtualization stack)
#
# Legacy (non-UEFI) guest firmware. Secure Boot is how InterGenOS runs
# its own ship; the OS PROVIDES the capability and never forces it on
# guests — so the legacy firmware path ships alongside edk2-ovmf
# (decided 2026-07-16). The tracked configs/ fragments are byte-copies
# of the fragments QEMU itself uses to build its bundled blobs
# (roms/config.* in the QEMU source tree), so these roms are
# functionally the set QEMU expects: bios.bin (128k, isapc-class
# machines), bios-256k.bin (machine types 2.0+), and the seavgabios
# variants per emulated display device. Each variant builds in its own
# OUT= directory from its fragment (the upstream multi-config pattern).

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

# variant:config-fragment:built-rom:installed-name
VARIANTS_BIOS="
seabios-128k:config.seabios-128k:bios.bin:bios.bin
seabios-256k:config.seabios-256k:bios.bin:bios-256k.bin
"
VARIANTS_VGA="
vga-isavga:config.vga-isavga:vgabios.bin:vgabios.bin
vga-stdvga:config.vga-stdvga:vgabios.bin:vgabios-stdvga.bin
vga-cirrus:config.vga-cirrus:vgabios.bin:vgabios-cirrus.bin
vga-qxl:config.vga-qxl:vgabios.bin:vgabios-qxl.bin
vga-virtio:config.vga-virtio:vgabios.bin:vgabios-virtio.bin
vga-vmware:config.vga-vmware:vgabios.bin:vgabios-vmware.bin
vga-bochs-display:config.vga-bochs-display:vgabios.bin:vgabios-bochs-display.bin
vga-ramfb:config.vga-ramfb:vgabios.bin:vgabios-ramfb.bin
"

build_variant() {
    local name="$1" fragment="$2"
    mkdir -p "builds/$name"
    cp "${IGOS_PACKAGE_DIR:-$BUILD_DIR}/configs/$fragment" "builds/$name/.config"
    # KCONFIG_CONFIG MUST be overridden on the command line: the top
    # Makefile pins it := $(CURDIR)/.config (source root), so an OUT=
    # build otherwise IGNORES the per-variant fragment and silently
    # produces a default-config rom (caught by the per-variant
    # assertions below on the first build).
    make OUT="builds/$name/" KCONFIG_CONFIG="$PWD/builds/$name/.config" olddefconfig
    make -j"$(nproc)" OUT="builds/$name/" KCONFIG_CONFIG="$PWD/builds/$name/.config" all
}

configure() {
    set -e
    # No configure step: per-variant Kconfig fragments are applied in build().
    :
}

build() {
    set -e
    local entry name fragment rom dest
    for entry in $VARIANTS_BIOS $VARIANTS_VGA; do
        IFS=: read -r name fragment rom dest <<< "$entry"
        build_variant "$name" "$fragment"
        test -f "builds/$name/$rom"   # fail loudly if the variant produced nothing
    done
    # Prove the ROM_SIZE fragments took: the padded rom sizes are exact.
    test "$(stat -c %s builds/seabios-128k/bios.bin)" = 131072
    test "$(stat -c %s builds/seabios-256k/bios.bin)" = 262144
}

do_install() {
    set -e
    local entry name fragment rom dest
    for entry in $VARIANTS_BIOS; do
        IFS=: read -r name fragment rom dest <<< "$entry"
        install -Dm644 "builds/$name/$rom" "$DESTDIR/usr/share/seabios/$dest"
    done
    for entry in $VARIANTS_VGA; do
        IFS=: read -r name fragment rom dest <<< "$entry"
        install -Dm644 "builds/$name/$rom" "$DESTDIR/usr/share/seavgabios/$dest"
    done
}
