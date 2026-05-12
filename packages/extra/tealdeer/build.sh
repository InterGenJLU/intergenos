#!/bin/bash
# tealdeer 1.6.1

configure() {
    set -e
    tar xf "$IGOS_SOURCES/tealdeer-$PKG_VERSION-vendor.tar.xz" --strip-components=1
}

build() {
    set -e
    cargo build --release --frozen --offline
}

do_install() {
    set -e
    install -Dm755 target/release/tldr "$DESTDIR/usr/bin/tldr"
}
