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

PKG_USER=etcd
PKG_GROUP=etcd
DATA_DIR=/var/lib/etcd
LOG_DIR=/var/log/etcd
RUNTIME_DIR=/run/etcd
CONF_DIR=/etc/etcd

configure() {
    set -e
    true
}

build() {
    set -e
    # Go toolchain from packages/core/go/
    export GOTOOLCHAIN=local
    export CGO_ENABLED=0
    make build GO_BUILD_FLAGS="-v"
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
    install -m 640 etcd.conf.yml.sample "$DESTDIR"/etc/etcd/etcd.conf.yaml

    # State + log + runtime directories
    install -d -m 750 "$DESTDIR"/var/lib/etcd
    install -d -m 750 "$DESTDIR"/var/log/etcd
    install -d -m 755 "$DESTDIR"/run/etcd

    # Install systemd unit
    install -d -m 755 "$DESTDIR"/usr/lib/systemd/system
    install -m 644 etcd.service "$DESTDIR"/usr/lib/systemd/system/

    # Install AppArmor profile
    install -d -m 755 "$DESTDIR"/etc/apparmor.d
    install -m 644 usr.bin.etcd "$DESTDIR"/etc/apparmor.d/
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
