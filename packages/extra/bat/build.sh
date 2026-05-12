#!/bin/bash
# bat 0.24.0

configure() {
    set -e
    tar xf "$IGOS_SOURCES/bat-$PKG_VERSION-vendor.tar.xz" --strip-components=1
}

build() {
    set -e
    cargo build --release
}

do_install() {
    set -e
    install -Dm755 target/release/bat "$DESTDIR/usr/bin/bat"
}
