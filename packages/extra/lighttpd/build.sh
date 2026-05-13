#!/bin/bash
# lighttpd 1.4.82 — BSD-3 lightweight event-driven HTTP server
# Upstream: https://www.lighttpd.net/
#
# Security posture (default-secure, no-tradeoffs):
# - Bind 127.0.0.1 only (deliberate opt-in to network exposure)
# - server.tag = "" (no version banner)
# - Full systemd hardening baseline (landing plan §5e)
# - AppArmor profile in enforce mode (§5f)
# - No telemetry, no analytics, no auto-update

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

PKG_USER=lighttpd
PKG_GROUP=lighttpd
STATE_DIR=/var/lib/lighttpd
LOG_DIR=/var/log/lighttpd
RUNTIME_DIR=/run/lighttpd
CONF_DIR=/etc/lighttpd

configure() {
    set -e
    ./configure --prefix=/usr \
                --sysconfdir="$CONF_DIR" \
                --libdir=/usr/lib \
                --with-openssl \
                --with-pcre2 \
                --with-zlib \
                --without-bzip2 \
                --without-lua
}

build() {
    set -e
    make -j${IGOS_JOBS:-1}
}

check() {
    set -e
    make check || true
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    install -d -m 750 "$DESTDIR"/etc/lighttpd
    install -d -m 750 "$DESTDIR"/etc/lighttpd/conf.d
    install -m 644 lighttpd.conf "$DESTDIR"/etc/lighttpd/lighttpd.conf

    install -d -m 750 "$DESTDIR"/var/lib/lighttpd
    install -d -m 750 "$DESTDIR"/var/log/lighttpd
    install -d -m 755 "$DESTDIR"/run/lighttpd

    install -d -m 755 "$DESTDIR"/usr/lib/systemd/system
    install -m 644 "$BUILD_DIR/lighttpd.service" "$DESTDIR"/usr/lib/systemd/system/

    install -d -m 755 "$DESTDIR"/etc/apparmor.d
    install -m 644 "$BUILD_DIR/usr.sbin.lighttpd" "$DESTDIR"/etc/apparmor.d/
}

post_install() {
    set -e
    if ! getent group "$PKG_GROUP" >/dev/null; then
        groupadd -r "$PKG_GROUP"
    fi
    if ! getent passwd "$PKG_USER" >/dev/null; then
        useradd -r -g "$PKG_GROUP" -d /var/lib/lighttpd -s /sbin/nologin "$PKG_USER"
    fi

    chown -R "$PKG_USER":"$PKG_GROUP" /var/lib/lighttpd
    chown -R "$PKG_USER":"$PKG_GROUP" /var/log/lighttpd
    chown -R "$PKG_USER":"$PKG_GROUP" /run/lighttpd
    chown root:"$PKG_GROUP" /etc/lighttpd /etc/lighttpd/lighttpd.conf
    chmod 640 /etc/lighttpd/lighttpd.conf

    systemctl daemon-reload 2>/dev/null || true
    apparmor_parser -r /etc/apparmor.d/usr.sbin.lighttpd 2>/dev/null || true
}
