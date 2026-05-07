#!/bin/bash
# netavark 1.17.2 — Container network plugin written in Rust
# Not in BLFS — InterGenOS extra tier
#
# Podman's Rust-based network backend. Uses pre-vendored tarball
# (netavark-v${version}-vendor.tar.gz) with all crate dependencies
# included for offline chroot builds.
# Built with cargo --release --frozen.

configure() {
    set -e
    :
}

build() {
    set -e
    cargo build --release --frozen
}

check() {
    set -e
    ./target/release/netavark --version
}

do_install() {
    set -e
    install -D -m 0755 target/release/netavark \
        "$DESTDIR/usr/libexec/podman/netavark"
}
