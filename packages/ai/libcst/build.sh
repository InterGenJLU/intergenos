#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# libcst 1.8.6 — setuptools-rust build, offline via the cargo-vendor pattern
# (rust workspace under native/; its Cargo.lock governs the vendor set).

configure() {
    set -e
    tar xf "${IGOS_SOURCES}/libcst-${PKG_VERSION}-vendor.tar.xz" --strip-components=1
}

build() {
    set -e
    export CARGO_NET_OFFLINE=true
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir "$PWD"
}

do_install() {
    set -e
    pip3 install --ignore-installed --no-deps --no-index --find-links dist --no-user \
         --root="$DESTDIR" libcst
}
