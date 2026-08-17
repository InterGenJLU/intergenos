#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# zram-generator 1.1.2 — systemd unit generator for zram compressed swap.
# https://github.com/systemd/zram-generator
#
# Provides RAM-backed compressed swap on installed systems. Forge installs
# previously created NO swap, leaving systemd-oomd degraded under memory
# pressure. zram (not a disk swapfile) is the security-correct choice: paged
# anonymous memory stays in RAM, never written in plaintext to persistent
# storage (a confidentiality regression a swapfile would introduce on
# non-encrypted installs). The kernel ships CONFIG_ZRAM=m + zstd.
#
# Build approach mirrors the in-tree Rust pattern (ripgrep/fd/cargo-c):
# the primary source auto-extracts, configure() overlays the host-vendored
# crates + the pinned Cargo.lock, build() compiles offline+frozen, and
# do_install() lays down the generator binary, its instance service unit
# (rendered from the upstream .in template), the upstream config example,
# and our default /etc/systemd/zram-generator.conf swap policy.

PKG_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

# systemd locations on InterGenOS (UsrMerge; /usr/lib, not /lib). These match
# what `pkg-config --variable=systemd*dir systemd` reports in the chroot and
# what the upstream Makefile would substitute.
SYSTEMD_UTIL_DIR="/usr/lib/systemd"
SYSTEMD_SYSTEM_GENERATOR_DIR="/usr/lib/systemd/system-generators"
SYSTEMD_SYSTEM_UNIT_DIR="/usr/lib/systemd/system"

configure() {
    set -e
    # Upstream ships no Cargo.lock — copy the pinned one (generated host-side
    # by scripts/cargo-vendor-gen.sh and committed to this package dir).
    cp -v "${IGOS_SOURCES}/zram-generator-${PKG_VERSION}-Cargo.lock" Cargo.lock

    # Overlay the vendored crate sources (vendor/ + .cargo/config.toml).
    tar xf "${IGOS_SOURCES}/zram-generator-${PKG_VERSION}-vendor.tar.xz" --strip-components=1

    # Render the instance service unit from the upstream template, baking in
    # the generator path (upstream's Makefile does this sed substitution).
    sed -e "s,@SYSTEMD_SYSTEM_GENERATOR_DIR@,${SYSTEMD_SYSTEM_GENERATOR_DIR}," \
        < units/systemd-zram-setup@.service.in \
        > units/systemd-zram-setup@.service
}

build() {
    set -e
    # src/setup.rs bakes SYSTEMD_UTIL_DIR via env!() at compile time (used to
    # locate systemd-makefs for fs-type devices). Required even for swap-only.
    export SYSTEMD_UTIL_DIR
    cargo build --release --frozen --offline
}

do_install() {
    set -e

    # Generator binary — systemd invokes this in early boot.
    install -Dm755 target/release/zram-generator \
        "${DESTDIR}${SYSTEMD_SYSTEM_GENERATOR_DIR}/zram-generator"

    # Instance service unit (systemd-zram-setup@zram0.service is pulled in by
    # the generator at boot; no preset/enable is needed).
    install -Dm644 units/systemd-zram-setup@.service \
        "${DESTDIR}${SYSTEMD_SYSTEM_UNIT_DIR}/systemd-zram-setup@.service"

    # Upstream config reference (self-documenting; we ship no man pages, same
    # as the other in-tree Rust packages).
    install -Dm644 zram-generator.conf.example \
        "${DESTDIR}/usr/share/doc/zram-generator/zram-generator.conf.example"

    # InterGenOS default swap policy.
    install -Dm644 "${PKG_DIR}/files/etc/systemd/zram-generator.conf" \
        "${DESTDIR}/etc/systemd/zram-generator.conf"
}
