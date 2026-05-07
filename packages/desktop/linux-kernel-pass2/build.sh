#!/bin/bash
# Linux Kernel 6.18.10 — Pass 2 rebuild with merged config fragments
#
# Pass 1 (core) builds with whatever intergenos.config existed at the time.
# This pass rebuilds with all fragments merged, ensuring USB_STORAGE=y,
# RTW88 bus parents, RTW89, and all overrides are applied.
#
# Note on the previously-removed SKIP-LOGIC: an earlier optimization
# checked a checksum of the config fragments and short-circuited the
# build if unchanged. That collided with the framework's filesystem-
# snapshot-diff manifest tracking — a no-op build means zero new files,
# which the tracker correctly rejects ("No new files detected"). The
# skip-logic also produced installs without manifests, breaking the
# audit trail required by the project's security-only alignment. Removed in favor of always-rebuild
# semantics (16-min cost per build cycle, predictable manifest output).

FRAG_DIR="/mnt/intergenos/config/kernel/fragments"

configure() {
    set -e
    make mrproper

    # Merge ALL config fragments (baseline + overrides)
    cat "$FRAG_DIR"/*.config > .config
    make olddefconfig

    # Verify critical options
    echo "  Verify: USB_STORAGE=$(grep CONFIG_USB_STORAGE= .config | head -1)"
    echo "  Verify: RTW88_PCI=$(grep CONFIG_RTW88_PCI .config | head -1)"
    echo "  Verify: RTW88_8821CE=$(grep CONFIG_RTW88_8821CE .config | head -1)"
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    # Overwrite pass 1 kernel and modules. Direct-install package
    # (per package.yml direct_install: true) — files land in /boot
    # and /lib/modules; framework's FS-snapshot-diff generates the
    # manifest from observed new files.
    make modules_install

    install -vm755 -d /boot
    cp -v arch/x86/boot/bzImage /boot/vmlinuz-6.18.10-igos
    cp -v System.map /boot/System.map-6.18.10
    cp -v .config /boot/config-6.18.10

    depmod 6.18.10
}
