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

    # Ship the nftables.service unit itself. Upstream nftables 1.1.3 does
    # NOT include a systemd unit (no contrib/systemd/ in the source tree),
    # so without this step `systemctl enable nftables.service` returns
    # not-found and the 90-nftables.preset above is a dangling reference.
    # Discovered in Build #9 dev1 live-VM verification (2026-05-14).
    install -Dm644 "$BUILD_DIR/nftables.service" \
                   "$DESTDIR/usr/lib/systemd/system/nftables.service"

    # Ship a permissive default ruleset at /etc/nftables.conf so the
    # service has something to load on first start. Operators tighten
    # the default-accept policies to default-drop + explicit allow
    # rules as their network exposure model requires.
    install -Dm644 "$BUILD_DIR/nftables.conf" \
                   "$DESTDIR/etc/nftables.conf"
}
