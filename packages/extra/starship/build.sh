#!/bin/bash
# starship 1.18.2

configure() {
    set -e
    tar xf "$IGOS_SOURCES/starship-$PKG_VERSION-vendor.tar.xz" --strip-components=1
}

build() {
    set -e
    cargo build --release
}

do_install() {
    set -e
    install -Dm755 target/release/starship "$DESTDIR/usr/bin/starship"
}
