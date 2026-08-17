#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# ripgrep 14.1.0

configure() {
    set -e
    tar xf "$IGOS_SOURCES/ripgrep-$PKG_VERSION-vendor.tar.xz" --strip-components=1
}

build() {
    set -e
    cargo build --release --frozen --offline
}

do_install() {
    set -e
    install -Dm755 target/release/rg "$DESTDIR/usr/bin/rg"
}
