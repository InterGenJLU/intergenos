#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# pydantic-core 2.46.4 — maturin-built Rust extension, offline via the
# cargo-vendor pattern. Version slaved to pydantic 2.13.4's exact pin
# (package.yml alignment note).

configure() {
    set -e
    tar xf "${IGOS_SOURCES}/pydantic-core-${PKG_VERSION}-vendor.tar.xz" --strip-components=1
}

build() {
    set -e
    export CARGO_NET_OFFLINE=true
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir "$PWD"
}

do_install() {
    set -e
    pip3 install --ignore-installed --no-deps --no-index --find-links dist --no-user \
         --root="$DESTDIR" pydantic-core
}
