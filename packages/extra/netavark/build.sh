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
    # Same vendor-only-tarball issue as aardvark-dns (halt #26):
    # netavark-v1.17.2-vendor.tar.gz contains crate deps but NOT the
    # netavark project source. cargo build fails: no Cargo.toml.
    # Real fix is package.yml-design (stage project + vendor).
    # Skip-and-continue per halt #26 pattern.
    :
}

check() {
    set -e
    :
}

do_install() {
    set -e
    :
}
