#!/bin/bash
# aardvark-dns 1.17.1 — Authoritative DNS server for container records
# Not in BLFS — InterGenOS extra tier
#
# DNS backend for netavark/Podman. Uses pre-vendored tarball
# (aardvark-dns-v${version}-vendor.tar.gz) with all crate dependencies
# included for offline chroot builds.
# Built with cargo --release --frozen.

configure() {
    :
}

build() {
    cargo build --release --frozen
}

check() {
    ./target/release/aardvark-dns --version
}

do_install() {
    install -D -m 0755 target/release/aardvark-dns \
        "$DESTDIR/usr/libexec/podman/aardvark-dns"
}
