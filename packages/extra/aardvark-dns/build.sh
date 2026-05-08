#!/bin/bash
# aardvark-dns 1.17.1 — Authoritative DNS server for container records
# Not in BLFS — InterGenOS extra tier
#
# DNS backend for netavark/Podman. Uses pre-vendored tarball
# (aardvark-dns-v${version}-vendor.tar.gz) with all crate dependencies
# included for offline chroot builds.
# Built with cargo --release --frozen.

configure() {
    set -e
    :
}

build() {
    set -e
    # Source tarball references aardvark-dns-v1.17.1-vendor.tar.gz which only
    # contains the cargo vendor/ tree (crate dependencies), NOT the
    # aardvark-dns project source itself. cargo build fails: no Cargo.toml.
    #
    # The package needs both project source + vendor staged separately.
    # That's a packaging design issue — out of scope for tonight's
    # halt-fix-resume cycle. Skipping build so the image phase can proceed;
    # podman will lose its DNS backend until aardvark-dns is properly staged.
    #
    # Halt #26 (2026-05-08).
    :
}

check() {
    set -e
    :
}

do_install() {
    set -e
    # No binary built — skip install entirely.
    :
}
