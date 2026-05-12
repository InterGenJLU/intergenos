#!/bin/bash
# dust 0.9.0

configure() {
    set -e
    tar xf "$IGOS_SOURCES/dust-$PKG_VERSION-vendor.tar.xz" --strip-components=1
}

build() {
    set -e
    cargo build --release --frozen --offline
}

do_install() {
    set -e
    install -Dm755 target/release/dust "$DESTDIR/usr/bin/dust"
}
