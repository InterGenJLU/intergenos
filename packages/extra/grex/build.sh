#!/bin/bash
# grex 1.4.5

configure() {
    set -e
    tar xf "$IGOS_SOURCES/grex-$PKG_VERSION-vendor.tar.xz" --strip-components=1
}

build() {
    set -e
    cargo build --release --frozen --offline
}

do_install() {
    set -e
    install -Dm755 target/release/grex "$DESTDIR/usr/bin/grex"
}
