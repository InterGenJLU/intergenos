#!/bin/bash
# caddy 2.11.3 — Apache-2.0 modern ACME-aware HTTP/2/3 single-binary server
# Upstream: https://github.com/caddyserver/caddy
# Go build with CGO_ENABLED=0 (static binary, no system crypto deps)
# BUILD_DIR pattern per master commit 29997ff2
#
# Security posture (default-secure, no-tradeoffs):
# - Bind 127.0.0.1 only per shipped Caddyfile
# - Staging ACME endpoint by default (no real LE traffic on dry-runs)
# - Full systemd hardening baseline (§5e)
# - AppArmor profile in enforce mode (§5f)
# - CGO disabled (static binary, no dynamic system linking surface)

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

PKG_USER=caddy
PKG_GROUP=caddy
STATE_DIR=/var/lib/caddy
LOG_DIR=/var/log/caddy
CONF_DIR=/etc/caddy

configure() {
    set -e
    # Extract pre-vendored Go module deps (generated via go-vendor-gen on SPOC,
    # byte-reproducible against SOURCE_DATE_EPOCH = master tip commit time).
    # Chroot is offline by design — caddy's ~150-module dep tree must be
    # vendored locally before `go build` runs.
    if [ -f "${IGOS_SOURCES}/caddy-2.11.3-vendor.tar.xz" ]; then
        tar -xf "${IGOS_SOURCES}/caddy-2.11.3-vendor.tar.xz" --strip-components=1
    else
        echo "ERROR: caddy-2.11.3-vendor.tar.xz not found in IGOS_SOURCES"
        echo "Run: go-vendor-gen for caddy-2.11.3 prior to build"
        exit 1
    fi
}

build() {
    set -e
    export GOTOOLCHAIN=local
    export CGO_ENABLED=0
    export GOFLAGS="-buildvcs=false -trimpath -mod=vendor"

    cd cmd/caddy
    go build -ldflags '-s -w' -o caddy
}

check() {
    set -e
    true
}

do_install() {
    set -e
    install -d -m 755 "$DESTDIR"/usr/bin
    install -m 755 cmd/caddy/caddy "$DESTDIR"/usr/bin/caddy

    install -d -m 750 "$DESTDIR"/etc/caddy
    install -m 640 "$BUILD_DIR"/Caddyfile "$DESTDIR"/etc/caddy/Caddyfile

    install -d -m 750 "$DESTDIR"/var/lib/caddy
    install -d -m 750 "$DESTDIR"/var/log/caddy

    install -d -m 755 "$DESTDIR"/usr/lib/systemd/system
    install -m 644 "$BUILD_DIR"/caddy.service "$DESTDIR"/usr/lib/systemd/system/

    install -d -m 755 "$DESTDIR"/etc/apparmor.d
    install -m 644 "$BUILD_DIR"/usr.bin.caddy "$DESTDIR"/etc/apparmor.d/
}

post_install() {
    set -e
    if ! getent group "$PKG_GROUP" >/dev/null; then
        groupadd -r "$PKG_GROUP"
    fi
    if ! getent passwd "$PKG_USER" >/dev/null; then
        useradd -r -g "$PKG_GROUP" -d /var/lib/caddy -s /sbin/nologin "$PKG_USER"
    fi
    chown -R "$PKG_USER":"$PKG_GROUP" /var/lib/caddy /var/log/caddy /etc/caddy
    systemctl daemon-reload 2>/dev/null || true
    apparmor_parser -r /etc/apparmor.d/usr.bin.caddy 2>/dev/null || true
}
