#!/bin/bash
# sd 1.0.0

configure() {
    set -e
    tar xf "$IGOS_SOURCES/sd-$PKG_VERSION-vendor.tar.xz" --strip-components=1
}

build() {
    set -e
    cargo build --release --frozen --offline
}

do_install() {
    set -e
    install -Dm755 target/release/sd "$DESTDIR/usr/bin/sd"
}
