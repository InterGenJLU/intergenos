#!/bin/bash
# rust-bindgen 0.72.1 — Rust FFI bindings generator
# BLFS 13.0

configure() {
    set -e
    # Extract vendored crate dependencies (built offline on host)
    tar xf "${IGOS_SOURCES}/rust-bindgen-${PKG_VERSION}-vendor.tar.xz" --strip-components=1
}

build() {
    set -e
    cargo build --release
}

check() {
    set -e
    cargo test --release || true
}

do_install() {
    set -e
    mkdir -pv "${DESTDIR}/usr/bin"
    install -v -m755 target/release/bindgen "${DESTDIR}/usr/bin/bindgen"
}

post_install() {
    set -e
    # Install shell completions
    bindgen --generate-shell-completions bash \
        > /usr/share/bash-completion/completions/bindgen
    bindgen --generate-shell-completions zsh \
        > /usr/share/zsh/site-functions/_bindgen
}
