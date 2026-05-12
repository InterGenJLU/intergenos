#!/bin/bash
# just 1.26.0

configure() {
    set -e
    tar xf "$IGOS_SOURCES/just-$PKG_VERSION-vendor.tar.xz" --strip-components=1
}

build() {
    set -e
    cargo build --release --frozen --offline
}

do_install() {
    set -e
    install -Dm755 target/release/just "$DESTDIR/usr/bin/just"
}
