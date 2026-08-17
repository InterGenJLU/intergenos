#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# valkey 9.0.4 — BSD-3-Clause in-memory KV store (Redis-wire-compatible)
# Upstream: https://github.com/valkey-io/valkey
# LF-stewarded. Default-recommended Redis-wire package per
# database-landing-plan §6. Three 2025 CVEs closed in 8.x line.
#
# Security posture (default-secure, no-tradeoffs):
# - Bind 127.0.0.1 only (deliberate opt-in to network exposure)
# - NO shipped auth: a build-time-generated requirepass is one secret
#   stamped into the archive and shared by every install (shipped that
#   way 2026-05-12..2026-07-28) -- worse than none. Mainstream posture
#   for a loopback-bound service: no password, network isolation is the
#   boundary (bind 127.0.0.1 + protected-mode). Operators who expose it
#   set their own requirepass at that moment.
# - Full systemd hardening baseline (§5e)
# - AppArmor profile in enforce mode (§5f)
# - No telemetry, no analytics, no auto-update

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

PKG_USER=valkey
PKG_GROUP=valkey
STATE_DIR=/var/lib/valkey
LOG_DIR=/var/log/valkey
RUNTIME_DIR=/run/valkey
CONF_DIR=/etc/valkey

configure() {
    set -e
    true
}

build() {
    set -e
    make -j${IGOS_JOBS:-1} -C src all \
        PREFIX=/usr \
        BUILD_TLS=yes
}

check() {
    set -e
    true
}

do_install() {
    set -e
    # Install binaries
    make -C src install \
        PREFIX="$DESTDIR"/usr \
        INSTALL_BIN="$DESTDIR"/usr/bin \
        INSTALL=install

    # Install config directory and default config
    install -d -m 750 "$DESTDIR"/etc/valkey
    install -m 640 valkey.conf "$DESTDIR"/etc/valkey/valkey.conf

    # No requirepass line: see the security-posture header. protected-mode
    # refuses non-loopback clients when no auth is configured, which is
    # exactly the shipped topology.

    # Bind 127.0.0.1 only — operators edit to expose
    echo "bind 127.0.0.1 -::1" >> "$DESTDIR"/etc/valkey/valkey.conf
    echo "protected-mode yes" >> "$DESTDIR"/etc/valkey/valkey.conf

    # State + log + runtime directories
    install -d -m 750 "$DESTDIR"/var/lib/valkey
    install -d -m 750 "$DESTDIR"/var/log/valkey
    install -d -m 755 "$DESTDIR"/run/valkey

    # Install systemd unit
    install -d -m 755 "$DESTDIR"/usr/lib/systemd/system
    install -m 644 "$BUILD_DIR/valkey.service" "$DESTDIR"/usr/lib/systemd/system/

    # Install AppArmor profile
    install -d -m 755 "$DESTDIR"/etc/apparmor.d
    install -m 644 "$BUILD_DIR/usr.bin.valkey-server" "$DESTDIR"/etc/apparmor.d/
}

post_install() {
    set -e
    # Process this package's /usr/lib/sysusers.d/valkey.conf entry now
    # so the valkey user/group exist before the chown below resolves.
    systemd-sysusers /usr/lib/sysusers.d/valkey.conf

    # Fix ownership
    chown -R "$PKG_USER":"$PKG_GROUP" /var/lib/valkey
    chown -R "$PKG_USER":"$PKG_GROUP" /var/log/valkey
    chown -R "$PKG_USER":"$PKG_GROUP" /run/valkey

    # Reload systemd and AppArmor
    systemctl daemon-reload 2>/dev/null || true
    apparmor_parser -r /etc/apparmor.d/usr.bin.valkey-server 2>/dev/null || true
}
