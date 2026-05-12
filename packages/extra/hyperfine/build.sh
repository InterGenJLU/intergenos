#!/bin/bash
# hyperfine 1.18.0

configure() {
    set -e
    tar xf "$IGOS_SOURCES/hyperfine-$PKG_VERSION-vendor.tar.xz" --strip-components=1
}

build() {
    set -e
    cargo build --release
}

do_install() {
    set -e
    install -Dm755 target/release/hyperfine "$DESTDIR/usr/bin/hyperfine"
}
