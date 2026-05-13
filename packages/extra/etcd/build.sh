#!/bin/bash
# etcd 3.6.11 — Apache-2.0 distributed KV / coordination store
# Upstream: https://github.com/etcd-io/etcd
# License: Apache-2.0 (verified: LICENSE file in upstream tarball)
# First Wave 2 Go-based database per database-landing-plan §6.
#
# Security posture (default-secure, no-tradeoffs):
# - TLS required + auth on by default per shipped config skeleton
# - Bind 127.0.0.1 only (deliberate opt-in to network exposure)
# - Full systemd hardening baseline (§5e)
# - AppArmor profile in enforce mode (§5f)

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

PKG_USER=etcd
PKG_GROUP=etcd
DATA_DIR=/var/lib/etcd
LOG_DIR=/var/log/etcd
RUNTIME_DIR=/run/etcd
CONF_DIR=/etc/etcd

configure() {
    set -e
    # Extract pre-vendored Go module deps (multi-module workspace:
    # server/vendor + etcdctl/vendor + etcdutl/vendor). The tarball is
    # produced by go-vendor-gen on SPOC's host, byte-reproducible against
    # SOURCE_DATE_EPOCH=master tip + tar reproducibility flags.
    if [ -f "${IGOS_SOURCES}/etcd-3.6.11-vendor.tar.xz" ]; then
        tar -xf "${IGOS_SOURCES}/etcd-3.6.11-vendor.tar.xz" --strip-components=1
    else
        echo "ERROR: etcd-3.6.11-vendor.tar.xz not found in IGOS_SOURCES"
        echo "Run: scripts/go-vendor-gen for etcd-3.6.11 prior to build"
        exit 1
    fi
}

build() {
    set -e
    export GOTOOLCHAIN=local
    export CGO_ENABLED=0
    # Bypass Makefile (which hardcodes -mod=readonly that would defeat
    # the vendor pattern); call build.sh directly with -mod=vendor.
    GO_BUILD_FLAGS="-v -mod=vendor" ./scripts/build.sh
}

check() {
    set -e
    true
}

do_install() {
    set -e
    # Install binaries
    install -d -m 755 "$DESTDIR"/usr/bin
    install -m 755 bin/etcd "$DESTDIR"/usr/bin/etcd
    install -m 755 bin/etcdctl "$DESTDIR"/usr/bin/etcdctl
    install -m 755 bin/etcdutl "$DESTDIR"/usr/bin/etcdutl

    # Install config skeleton
    install -d -m 750 "$DESTDIR"/etc/etcd
    install -m 640 "$BUILD_DIR/etcd.conf.yml.sample" "$DESTDIR"/etc/etcd/etcd.conf.yaml

    # State + log + runtime directories
    install -d -m 750 "$DESTDIR"/var/lib/etcd
    install -d -m 750 "$DESTDIR"/var/log/etcd
    install -d -m 755 "$DESTDIR"/run/etcd

    # Install systemd unit
    install -d -m 755 "$DESTDIR"/usr/lib/systemd/system
    install -m 644 "$BUILD_DIR/etcd.service" "$DESTDIR"/usr/lib/systemd/system/

    # Install AppArmor profile
    install -d -m 755 "$DESTDIR"/etc/apparmor.d
    install -m 644 "$BUILD_DIR/usr.bin.etcd" "$DESTDIR"/etc/apparmor.d/
}

post_install() {
    set -e
    if ! getent group "$PKG_GROUP" >/dev/null; then
        groupadd -r "$PKG_GROUP"
    fi
    if ! getent passwd "$PKG_USER" >/dev/null; then
        useradd -r -g "$PKG_GROUP" -d /var/lib/etcd -s /sbin/nologin "$PKG_USER"
    fi
    chown -R "$PKG_USER":"$PKG_GROUP" /var/lib/etcd /var/log/etcd /run/etcd /etc/etcd
    systemctl daemon-reload 2>/dev/null || true
    apparmor_parser -r /etc/apparmor.d/usr.bin.etcd 2>/dev/null || true
}
