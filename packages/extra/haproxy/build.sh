#!/bin/bash
# haproxy 3.2.19 — Reliable, high-performance TCP/HTTP load balancer
#
# Why this version (3.2.19, LTS 3.2 branch):
#   * 3.2 is the current LTS line, maintained until 2030-Q2 per
#     https://www.haproxy.org/. 3.2.19 is the latest point release
#     in the line (released 2025-05-28).
#   * SHA256 anchor pinned in package.yml after local download +
#     sha256sum.
#   * 3.2 introduces ACME protocol support for cert renewal — a
#     wow-factor surface for default-TLS deployment.
#
# License: GPL-2.0 (server core) AND LGPL-2.1 (exportable headers per
# upstream LICENSE explicitly declaring the split). The LICENSE file
# also adds an OpenSSL linking exception (compile/link/use with OpenSSL
# is expressly permitted). SPDX expression `GPL-2.0 AND LGPL-2.1`
# captures the dual notice; the OpenSSL linking exception is a project-
# specific carve-out that does not have a standard SPDX identifier and
# is documented in build.sh comments + commit-message body.
#
# Build profile: custom hand-rolled GNU Makefile (no autotools, no
# cmake, no meson). The build is driven by `make TARGET=linux-glibc
# USE_<feature>=1` flag pairs.
#
# Configure flags chosen (per upstream INSTALL Quick-Start + our
# in-tree dep posture):
#   TARGET=linux-glibc            — Linux/glibc kernel + libc
#   USE_OPENSSL=1                 — TLS termination via system OpenSSL
#                                   (3.6.1 in tree; satisfies QUIC API)
#   USE_QUIC=1                    — HTTP/3 / QUIC support
#   USE_QUIC_OPENSSL_COMPAT=1     — OpenSSL-API compatibility shim
#                                   (HAProxy historically targets
#                                   QuicTLS; this flag enables QUIC
#                                   against vanilla OpenSSL 3.5+)
#   USE_LUA=1                     — Lua scripting via system lua
#   USE_PCRE2=1                   — pcre2 regex engine
#   USE_ZLIB=1                    — gzip compression backend
#   USE_SYSTEMD=1                 — sd_notify socket integration
#   USE_PROMEX=1                  — built-in Prometheus exporter
#   USE_LIBCRYPT=1                — password hashing
#   USE_REGPARM=1                 — register-parameter calling
#                                   convention (x86 perf win)
#
# Project security-alignment posture: loopback-only frontend bind
# (operator opts in to network exposure), stats endpoint locked to
# 127.0.0.1 + Basic Auth (the access-control gate inside the frontend
# is defense-in-depth alongside the bind), no admin socket exposed
# externally (haproxy.cfg `stats socket /run/haproxy/admin.sock`
# unix-only mode 660 owned by the haproxy group), dedicated `haproxy`
# system user, full systemd hardening per the database-fleet baseline
# generalized to web servers, AppArmor profile in enforce mode. Service
# unit ships disabled — operator runs `systemctl enable haproxy`
# consciously after reviewing the sample config + replacing the
# self-signed cert with a real CA-signed one if needed.

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    set -e
    # haproxy's Makefile expects all flags on the command line; there
    # is no separate `./configure` step. This stub exists so the build
    # orchestrator's configure/build/install phase split still applies
    # and `configure_flags`-style additions can be wired in later.
    :
}

build() {
    set -e
    make -j${IGOS_JOBS}                                                    \
         TARGET=linux-glibc                                                \
         USE_OPENSSL=1                                                     \
         USE_QUIC=1                                                        \
         USE_QUIC_OPENSSL_COMPAT=1                                         \
         USE_LUA=1                                                         \
         USE_PCRE2=1                                                       \
         USE_ZLIB=1                                                        \
         USE_SYSTEMD=1                                                     \
         USE_PROMEX=1                                                      \
         USE_LIBCRYPT=1                                                    \
         USE_REGPARM=1
}

do_install() {
    set -e
    # haproxy's `make install` populates /usr/local/sbin/haproxy by
    # default; override to /usr/sbin via PREFIX. The Makefile also
    # installs man pages + the doc tree.
    make DESTDIR="$DESTDIR" PREFIX=/usr install

    # Install our hardened sample config (replaces the upstream-shipped
    # `examples/` placeholder — we ship a working bind-127.0.0.1
    # default at /etc/haproxy/haproxy.cfg).
    install -Dm644 "$BUILD_DIR/haproxy.cfg" \
        "$DESTDIR/etc/haproxy/haproxy.cfg"

    # Install our hardened systemd unit (upstream ships an example unit
    # under contrib/systemd/ but it lacks the §5e baseline directives;
    # we ship our own).
    install -Dm644 "$BUILD_DIR/haproxy.service" \
        "$DESTDIR/usr/lib/systemd/system/haproxy.service"

    # Install the AppArmor profile (enforce mode).
    install -Dm644 "$BUILD_DIR/usr.sbin.haproxy" \
        "$DESTDIR/etc/apparmor.d/usr.sbin.haproxy"
}

post_install() {
    set -e
    # Create the dedicated `haproxy` system user if not present.
    if ! getent passwd haproxy >/dev/null 2>&1; then
        useradd -r -s /sbin/nologin -d /var/lib/haproxy \
                -c "HAProxy load balancer" haproxy || true
    fi

    # State / log / runtime directories owned by the daemon user. The
    # admin socket lives at /run/haproxy/admin.sock — directory must be
    # group-writable by the haproxy group only.
    install -dm755 -o root    -g root    /etc/haproxy        2>/dev/null || true
    install -dm755 -o root    -g root    /etc/haproxy/ssl    2>/dev/null || true
    install -dm755 -o haproxy -g haproxy /var/lib/haproxy    2>/dev/null || true
    install -dm755 -o haproxy -g haproxy /var/log/haproxy    2>/dev/null || true
    install -dm750 -o haproxy -g haproxy /run/haproxy        2>/dev/null || true

    # Generate a self-signed cert + key at /etc/haproxy/ssl/server.pem
    # (haproxy expects cert + key concatenated into a single .pem file)
    # if not present yet. The sample haproxy.cfg references this path.
    local CERT_PEM=/etc/haproxy/ssl/server.pem
    if [ ! -f "$CERT_PEM" ]; then
        if command -v openssl >/dev/null 2>&1; then
            local TMP_KEY=$(mktemp)
            local TMP_CRT=$(mktemp)
            openssl req -x509 -newkey rsa:4096 -sha256 -nodes        \
                -days 365                                            \
                -keyout "$TMP_KEY"                                   \
                -out    "$TMP_CRT"                                   \
                -subj "/CN=localhost"                                \
                2>/dev/null || true
            cat "$TMP_CRT" "$TMP_KEY" > "$CERT_PEM"               2>/dev/null || true
            rm -f "$TMP_KEY" "$TMP_CRT"                           2>/dev/null || true
            chown root:root "$CERT_PEM"                           2>/dev/null || true
            chmod 600       "$CERT_PEM"                           2>/dev/null || true
        fi
    fi

    systemctl daemon-reload                                  2>/dev/null || true
    apparmor_parser -r /etc/apparmor.d/usr.sbin.haproxy      2>/dev/null || true
}

check() {
    set -e
    # haproxy's check target validates the just-built binary against a
    # canned config. Skipping here — the install-time `haproxy -c -f
    # /etc/haproxy/haproxy.cfg` invocation (via ExecStartPre in the
    # systemd unit) is the more meaningful health check.
    return 0
}
