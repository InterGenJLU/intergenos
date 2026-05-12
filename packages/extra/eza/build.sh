#!/bin/bash
# eza 0.18.11

configure() {
    set -e
    tar xf "$IGOS_SOURCES/eza-$PKG_VERSION-vendor.tar.xz" --strip-components=1
}

build() {
    set -e
    cargo build --release --frozen --offline
}

do_install() {
    set -e
    install -Dm755 target/release/eza "$DESTDIR/usr/bin/eza"
}
