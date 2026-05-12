#!/bin/bash
# zoxide 0.9.4

configure() {
    set -e
    tar xf "$IGOS_SOURCES/zoxide-$PKG_VERSION-vendor.tar.xz" --strip-components=1
}

build() {
    set -e
    cargo build --release
}

do_install() {
    set -e
    install -Dm755 target/release/zoxide "$DESTDIR/usr/bin/zoxide"
}
