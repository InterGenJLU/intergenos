#!/bin/bash
# tokei 12.1.2

configure() {
    set -e
    tar xf "$IGOS_SOURCES/tokei-$PKG_VERSION-vendor.tar.xz" --strip-components=1
}

build() {
    set -e
    cargo build --release --frozen --offline
}

do_install() {
    set -e
    install -Dm755 target/release/tokei "$DESTDIR/usr/bin/tokei"
}
