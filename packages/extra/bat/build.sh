#!/bin/bash
# bat 0.24.0

configure() {
    set -e
    tar xf "$IGOS_SOURCES/bat-$PKG_VERSION-vendor.tar.xz" --strip-components=1
}

build() {
    set -e
    # onig_sys (transitive Rust crate; bundled Oniguruma C source) has K&R-style
    # function pointer declarations that GCC 14/15+ rejects under default
    # -Werror=incompatible-pointer-types. Relax for the C-source compile pass
    # only; bat's Rust code is unaffected.
    export CFLAGS="${CFLAGS:-} -Wno-incompatible-pointer-types"
    cargo build --release --frozen --offline
}

do_install() {
    set -e
    install -Dm755 target/release/bat "$DESTDIR/usr/bin/bat"
}
