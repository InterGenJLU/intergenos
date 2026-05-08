#!/bin/bash
# aardvark-dns 1.17.1 — Authoritative DNS server for container records
# Not in BLFS — InterGenOS extra tier
#
# DNS backend for netavark/Podman. Uses pre-vendored tarball
# (aardvark-dns-v${version}-vendor.tar.gz) with all crate dependencies
# included for offline chroot builds.
# Built with cargo --release --frozen.

configure() {
    set -e
    # Both tarballs extract into the same source root (loupe pattern):
    # project tarball → Cargo.toml + src/ at the top level
    # vendor tarball → vendor/ at the top level
    # Configure cargo to use the vendor dir (no network access in chroot).
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
}

check() {
    set -e
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        cargo test --release --frozen --offline
}

do_install() {
    set -e
    install -Dm755 target/release/aardvark-dns "$DESTDIR/usr/libexec/podman/aardvark-dns"
}
