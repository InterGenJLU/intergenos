#!/bin/bash
# lazygit 0.41.0

configure() {
    set -e
    tar xf "$IGOS_SOURCES/lazygit-$PKG_VERSION-vendor.tar.xz" --strip-components=1
}

build() {
    set -e
    cd .
    go build -mod=vendor -o lazygit
}

do_install() {
    set -e
    cd .
    install -Dm755 lazygit "$DESTDIR/usr/bin/lazygit"
}
