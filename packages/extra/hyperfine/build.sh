#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# hyperfine 1.18.0

configure() {
    set -e
    tar xf "$IGOS_SOURCES/hyperfine-$PKG_VERSION-vendor.tar.xz" --strip-components=1
}

build() {
    set -e
    cargo build --release --frozen --offline
}

do_install() {
    set -e
    install -Dm755 target/release/hyperfine "$DESTDIR/usr/bin/hyperfine"
}
