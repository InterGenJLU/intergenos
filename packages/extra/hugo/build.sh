#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# hugo 0.125.4

configure() {
    set -e
    tar xf "$IGOS_SOURCES/hugo-$PKG_VERSION-vendor.tar.xz" --strip-components=1
}

build() {
    set -e
    cd .
    go build -mod=vendor -o hugo
}

do_install() {
    set -e
    cd .
    install -Dm755 hugo "$DESTDIR/usr/bin/hugo"
}
