#!/bin/bash
# cbindgen 0.27.0 — C bindings generator for Rust
# BLFS 13.0
# Note: requires internet connection for cargo dependencies

configure() { : ; }

build() {
    cargo build --release
}

check() {
    cargo test --release || true
}

do_install() {
    install -Dm755 target/release/cbindgen "${DESTDIR}/usr/bin/cbindgen"
}
