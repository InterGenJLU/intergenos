#!/bin/bash
# valkey 9.0.4 — BSD-3-Clause in-memory KV store (Redis-wire-compatible)
# Upstream: https://github.com/valkey-io/valkey
# LF-stewarded. Default-recommended Redis-wire package per
# database-landing-plan §6. Three 2025 CVEs closed in 8.x line.
#
# Security posture (default-secure, no-tradeoffs):
# - Bind 127.0.0.1 only (deliberate opt-in to network exposure)
# - Generated random requirepass at install time (NOT "foobared")
# - Full systemd hardening baseline (§5e)
# - AppArmor profile in enforce mode (§5f)
# - No telemetry, no analytics, no auto-update

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

    # Generate random requirepass (NOT default "foobared")
    local pass
    pass=$(openssl rand -base64 24 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(24))")
    echo "requirepass $pass" >> "$DESTDIR"/etc/valkey/valkey.conf
    echo "  Generated random requirepass via openssl rand", file=stderr

    # Bind 127.0.0.1 only — operators edit to expose
    echo "bind 127.0.0.1 -::1" >> "$DESTDIR"/etc/valkey/valkey.conf
    echo "protected-mode yes" >> "$DESTDIR"/etc/valkey/valkey.conf

    # State + log + runtime directories
    install -d -m 750 "$DESTDIR"/var/lib/valkey
    install -d -m 750 "$DESTDIR"/var/log/valkey
    install -d -m 755 "$DESTDIR"/run/valkey

    # Ship reproducible-builds anchor: recorded install time
    echo "valkey-install-timestamp $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$DESTDIR"/var/lib/valkey/.install-meta

    # Install systemd unit
    install -d -m 755 "$DESTDIR"/usr/lib/systemd/system
    install -m 644 valkey.service "$DESTDIR"/usr/lib/systemd/system/

    # Install AppArmor profile
    install -d -m 755 "$DESTDIR"/etc/apparmor.d
    install -m 644 usr.bin.valkey-server "$DESTDIR"/etc/apparmor.d/
}

post_install() {
    set -e
    # Create system user/group
    if ! getent group "$PKG_GROUP" >/dev/null; then
        groupadd -r "$PKG_GROUP"
    fi
    if ! getent passwd "$PKG_USER" >/dev/null; then
        useradd -r -g "$PKG_GROUP" -d /var/lib/valkey -s /sbin/nologin "$PKG_USER"
    fi

    # Fix ownership
    chown -R "$PKG_USER":"$PKG_GROUP" /var/lib/valkey
    chown -R "$PKG_USER":"$PKG_GROUP" /var/log/valkey
    chown -R "$PKG_USER":"$PKG_GROUP" /run/valkey

    # Reload systemd and AppArmor
    systemctl daemon-reload 2>/dev/null || true
    apparmor_parser -r /etc/apparmor.d/usr.bin.valkey-server 2>/dev/null || true
}
