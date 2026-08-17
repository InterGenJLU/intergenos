#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# tokenizers 0.22.2 — maturin-built Rust extension, offline via the
# cargo-vendor pattern (vendor/ + .cargo/ staged by cargo-vendor-gen.sh).

configure() {
    set -e
    tar xf "${IGOS_SOURCES}/tokenizers-${PKG_VERSION}-vendor.tar.xz" --strip-components=1
    # maturin sdists ship Cargo.lock; the vendor tarball was generated --locked
    # against it. If upstream ever drops the lockfile, stage the generated one:
    [ -f Cargo.lock ] || cp -v "${IGOS_SOURCES}/tokenizers-${PKG_VERSION}-Cargo.lock" Cargo.lock
}

build() {
    set -e
    export CARGO_NET_OFFLINE=true
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir "$PWD"
}

do_install() {
    set -e
    pip3 install --ignore-installed --no-deps --no-index --find-links dist --no-user \
         --root="$DESTDIR" tokenizers
}
