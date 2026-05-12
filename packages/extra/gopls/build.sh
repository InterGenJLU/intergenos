#!/bin/bash
# gopls 0.15.3

configure() {
    set -e
    tar xf "$IGOS_SOURCES/gopls-$PKG_VERSION-vendor.tar.xz" --strip-components=1
}

build() {
    set -e
    cd gopls
    go build -mod=vendor -o gopls
}

do_install() {
    set -e
    cd gopls
    install -Dm755 gopls "$DESTDIR/usr/bin/gopls"
}
