#!/bin/bash
# cbindgen 0.29.2 — C bindings generator for Rust
# BLFS 13.0

configure() {
    # Extract vendored crate dependencies (built offline on host)
    tar xf "${IGOS_SOURCES}/cbindgen-${PKG_VERSION}-vendor.tar.xz" --strip-components=1
}

build() {
    cargo build --release
}

check() {
    cargo test --release || true
}

do_install() {
    install -Dm755 target/release/cbindgen "${DESTDIR}/usr/bin/cbindgen"
}
