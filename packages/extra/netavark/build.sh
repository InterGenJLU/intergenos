#!/bin/bash
# netavark 1.17.2 — Container network plugin written in Rust
# Not in BLFS — InterGenOS extra tier
#
# Podman's Rust-based network backend. Uses pre-vendored tarball
# (netavark-v${version}-vendor.tar.gz) with all crate dependencies
# included for offline chroot builds.
# Built with cargo --release --frozen.

configure() {
    set -e
    # loupe-pattern: project + vendor tarballs both extract here.
    mkdir -p .cargo
    cat > .cargo/config.toml <<'EOF'
[source.crates-io]
replace-with = "vendored-sources"

[source.vendored-sources]
directory = "vendor"
EOF
}

build() {
    set -e
    cargo build --release --frozen --offline
    # netavark also ships a small dhcp-proxy daemon; default Makefile builds it.
    # We rely on `cargo build` which produces the binaries cargo defines in
    # workspace.members. dhcp-proxy is auto-included.
}

check() {
    set -e
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        cargo test --release --frozen --offline
}

do_install() {
    set -e
    install -Dm755 target/release/netavark "$DESTDIR/usr/libexec/podman/netavark"
    if [ -f target/release/netavark-dhcp-proxy ]; then
        install -Dm755 target/release/netavark-dhcp-proxy \
            "$DESTDIR/usr/libexec/podman/netavark-dhcp-proxy"
    fi
}
