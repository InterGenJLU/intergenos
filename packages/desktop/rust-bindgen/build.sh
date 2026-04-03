#!/bin/bash
# rust-bindgen 0.72.1 — Rust FFI bindings generator
# BLFS 13.0

configure() {
    # Extract vendored crate dependencies (built offline on host)
    tar xf "${IGOS_SOURCES}/rust-bindgen-${PKG_VERSION}-vendor.tar.xz" --strip-components=1
}

build() {
    cargo build --release
}

check() {
    cargo test --release || true
}

do_install() {
    install -v -m755 target/release/bindgen "${DESTDIR}/usr/bin/bindgen"
}

post_install() {
    # Install shell completions
    bindgen --generate-shell-completions bash \
        > /usr/share/bash-completion/completions/bindgen
    bindgen --generate-shell-completions zsh \
        > /usr/share/zsh/site-functions/_bindgen
}
