#!/bin/bash
# bottom 0.9.6

configure() {
    set -e
    tar xf "$IGOS_SOURCES/bottom-$PKG_VERSION-vendor.tar.xz" --strip-components=1
}

build() {
    set -e
    cargo build --release --frozen --offline
}

do_install() {
    set -e
    install -Dm755 target/release/btm "$DESTDIR/usr/bin/btm"
}
