#!/bin/bash
# xh 0.21.0

configure() {
    set -e
    tar xf "$IGOS_SOURCES/xh-$PKG_VERSION-vendor.tar.xz" --strip-components=1
}

build() {
    set -e
    cargo build --release
}

do_install() {
    set -e
    install -Dm755 target/release/xh "$DESTDIR/usr/bin/xh"
}
