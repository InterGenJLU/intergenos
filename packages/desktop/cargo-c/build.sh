#!/bin/bash
# cargo-c 0.10.20 — Cargo C-ABI helpers
# BLFS 13.0

configure() {
    set -e
    # Copy Cargo.lock (cargo-c doesn't ship one in its GitHub archive)
    cp -v "${IGOS_SOURCES}/cargo-c-${PKG_VERSION}-Cargo.lock" Cargo.lock

    # Extract vendored crate dependencies (built offline on host)
    tar xf "${IGOS_SOURCES}/cargo-c-${PKG_VERSION}-vendor.tar.xz" --strip-components=1
}

build() {
    set -e
    export LIBSSH2_SYS_USE_PKG_CONFIG=1
    export LIBSQLITE3_SYS_USE_PKG_CONFIG=1

    cargo build --release
}

do_install() {
    set -e
    mkdir -pv "${DESTDIR}/usr/bin"
    install -vm755 target/release/cargo-{capi,cbuild,cinstall,ctest} \
        "${DESTDIR}/usr/bin/"
}
