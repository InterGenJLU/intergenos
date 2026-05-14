#!/bin/bash
# nftables 1.1.3 — Netfilter nftables packet filtering framework
# BLFS 13.0

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    set -e
    ./configure --prefix=/usr \
                --sysconfdir=/etc \
                --with-json \
                --disable-man-doc \
                --with-cli=readline
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    # Ship a systemd preset that enables nftables.service by default. Firewall
    # up at boot is the secure default; real distros (Fedora, Arch) do the
    # same. Operators wanting to opt out drop a higher-priority preset that
    # `disable nftables.service`.
    install -Dm644 "$BUILD_DIR/90-nftables.preset" \
                   "$DESTDIR/usr/lib/systemd/system-preset/90-nftables.preset"
}
