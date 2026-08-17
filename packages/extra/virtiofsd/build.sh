#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# virtiofsd 1.14.0 — vhost-user virtio-fs device backend
# Not in BLFS — InterGenOS extra tier (virtualization stack)
#
# Standalone Rust virtio-fs daemon: OUR build-share mechanism for
# guests (the virtiofs role the Ubuntu host's virtiofsd performs for
# the build VM today). Links libcap-ng (privilege drop) and libseccomp
# (sandbox profile). Source is the upstream-published crates.io
# artifact; crate dependencies come from the cargo-vendor tarball
# (each crate is integrity-pinned by the checksums in the crate's own
# Cargo.lock, which rides inside the witnessed source artifact).

configure() {
    set -e
    tar xf "$IGOS_SOURCES/virtiofsd-$PKG_VERSION-vendor.tar.xz" --strip-components=1
}

build() {
    set -e
    cargo build --release --frozen --offline
}

do_install() {
    set -e
    install -Dm755 target/release/virtiofsd "$DESTDIR/usr/libexec/virtiofsd"
    # qemu vhost-user device descriptor (libvirt discovers the backend
    # through this file; shipped in the crate).
    install -Dm644 50-virtiofsd.json \
        "$DESTDIR/usr/share/qemu/vhost-user/50-virtiofsd.json"
}
