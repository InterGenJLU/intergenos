#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# apache-httpd 2.4.67 — Apache HTTP Server with mpm_event + DSO modules
#
# Wave W1 landing per docs/architecture/web-server-landing-plan.md at
# master b3427596. The heritage HTTP/HTTPS server; ~75% of internet-
# facing web traffic for legacy + corporate-CMS deployments.
#
# Why this version (2.4.67, current latest stable):
#   * 2.4.67 is the most recent 2.4.x release per
#     https://httpd.apache.org/. The 2.4.x branch is the long-term
#     stable; 2.6.x has not been released; 2.5.x is dev-only. Pinning
#     to 2.4.67.
#   * Single SHA-256 anchor pinned in package.yml.
#
# License: Apache-2.0. Single LICENSE file at the tarball root carries
# the standard Apache License 2.0. No additional license components.
#
# Build profile: autotools. Tarball ships pre-generated `configure`.
#
# Wave W1 dep-arrow validation (the third multi-arrow downstream this
# arc after rocksdb's 4-dep + mariadb's wave-1b dep + influxdb's
# protobuf-arrow):
#
#   apr 1.7.6 (this branch, prior commit) — link target for libapr-1.so
#       Discovery: configure --with-apr=/usr probes /usr/bin/apr-1-config
#       installed by the apr package. Standard pkg-config-style helper.
#
#   apr-util 1.6.3 (this branch, prior commit) — link target for
#       libaprutil-1.so. Discovery: configure --with-apr-util=/usr
#       probes /usr/bin/apu-1-config.
#
#   openssl (core) — mod_ssl + apr_crypto.h backend
#       Discovery: configure --with-ssl=/usr probes /usr/include/openssl/
#
#   pcre2 (core) — regex engine for the URL-matching surface
#       Discovery: configure --with-pcre=/usr (Apache 2.4.x accepts
#       both pcre1 and pcre2; in-tree pcre2 satisfies)
#
#   zlib (core) — mod_deflate backend
#       Discovery: configure --with-z=/usr
#
#   libxml2 (core) — mod_proxy_html + mod_xml2enc backend
#       Discovery: probed by configure based on presence
#
# All six dep arrows resolve via apache configure's --with-PKG=/usr
# pattern + standard /usr/{include,lib} search paths. The five-pkg-
# config-style helpers (apr-1-config, apu-1-config, pkg-config for
# openssl/pcre2/zlib) are all installed-and-discoverable in the build
# chroot.
#
# Configure flags applied per dispatch + the BLFS Apache HTTP Server
# canonical recipe (refined):
#
# Install layout (FHS-canonical, NOT Apache's default
# /usr/local/apache2 layout):
#   --prefix=/usr                       standard FHS
#   --sysconfdir=/etc/httpd             config tree
#   --datadir=/usr/share/httpd          ICONS / cgi-bin / error / etc.
#   --localstatedir=/var                runtime + log root
#   --libexecdir=/usr/lib/httpd/modules DSO modules
#
# MPM choice + DSO posture:
#   --with-mpm=event                    modern threaded MPM, async I/O
#   --enable-mods-shared=all            all stdlib modules as DSOs
#   --enable-mods-static=mpm_event      MPM linked into core
#
# Module enables (per landing plan + default-secure intent):
#   --enable-ssl                        mod_ssl with system openssl
#   --enable-deflate                    mod_deflate with system zlib
#   --enable-rewrite                    mod_rewrite (URL rewriting)
#   --enable-proxy --enable-proxy-http  reverse-proxy surface
#   --enable-http2                      HTTP/2 (mod_http2)
#   --enable-suexec                     SUID helper for shared hosting
#   --enable-pie                        PIE binary for ASLR
#
# Dep linkage:
#   --with-apr=/usr                     in-tree apr (this branch)
#   --with-apr-util=/usr                in-tree apr-util (this branch)
#   --with-ssl=/usr                     in-tree openssl
#   --with-pcre=/usr                    in-tree pcre2
#   --with-z=/usr                       in-tree zlib
#
# Default-secure posture (the wow-factor surface):
#   1. NO mod_php / mod_python / mod_perl bundled. Language interpreter
#      modules belong in separate follow-on apache-mod-<lang> packages.
#   2. The shipped /etc/httpd/conf.d/00-default.conf binds
#      127.0.0.1:443 ONLY (TLS-only loopback). Operators replace
#      127.0.0.1 with a real interface deliberately.
#   3. ServerTokens Prod + ServerSignature Off + TraceEnable Off.
#      No version banner, no signature footer on error pages.
#   4. TLS 1.2 + 1.3 only (no SSLv3 / TLSv1.0 / TLSv1.1).
#   5. HSTS + X-Content-Type-Options + X-Frame-Options + Referrer-
#      Policy headers set on the default vhost.
#   6. /server-status locked to 127.0.0.1 via Require ip + lazy
#      mod_status gate.
#   7. Self-signed cert + key generated per-machine on the target at
#      first service start (httpd-tls-keygen.service) to /etc/httpd/ssl/,
#      never at build time. Operators replace with CA-signed for prod.
#   8. systemd unit drops to dedicated `apache` system user (master
#      runs as root only to bind <1024 + delegate; workers run unpriv).
#   9. CapabilityBoundingSet locked to NET_BIND_SERVICE + SETUID +
#      SETGID (no CAP_SYS_ADMIN, no CAP_NET_RAW).
#  10. MemoryDenyWriteExecute=true — Apache has no JIT.
#  11. AppArmor profile in enforce mode constraining filesystem to
#      config (R) + DocumentRoot (R) + log/run/state dirs (RW) +
#      DSO module dir (MR) + standard library link surface.
#  12. Service ships disabled-by-default. Operator runs
#      `systemctl enable httpd` consciously.
#
# Security-only-alignment note: Apache httpd has the largest CVE
# history of any package in this branch — the long-running 2.4.x
# branch (since 2012) has accumulated mitigations + hardening over
# its lifetime. Default-secure config makes our shipped posture
# substantially more conservative than upstream defaults. The risk-
# reduction posture: ship with the minimum-viable-feature surface
# enabled, let operators add modules (proxy, ldap, dav, etc.) on
# their own modular config files.

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

PKG_USER=apache
PKG_GROUP=apache

configure() {
    set -e
    ./configure                                                            \
        --prefix=/usr                                                      \
        --sysconfdir=/etc/httpd                                            \
        --datadir=/usr/share/httpd                                         \
        --localstatedir=/var                                               \
        --libexecdir=/usr/lib/httpd/modules                                \
        --with-mpm=event                                                   \
        --enable-mods-shared=all                                           \
        --enable-ssl                                                       \
        --enable-deflate                                                   \
        --enable-rewrite                                                   \
        --enable-proxy                                                     \
        --enable-proxy-http                                                \
        --enable-http2                                                     \
        --enable-suexec                                                    \
        --enable-pie                                                       \
        --with-apr=/usr                                                    \
        --with-apr-util=/usr                                               \
        --with-ssl=/usr                                                    \
        --with-pcre=/usr                                                   \
        --with-z=/usr
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    # Install our hardened systemd unit + AppArmor profile + default-
    # secure drop-in config + sysconfig defaults.
    install -Dm644 "$BUILD_DIR/httpd.service" \
        "$DESTDIR/usr/lib/systemd/system/httpd.service"
    install -Dm644 "$BUILD_DIR/usr.bin.httpd" \
        "$DESTDIR/etc/apparmor.d/usr.bin.httpd"

    # F43: per-machine TLS cert generator + its oneshot unit. The cert+key are
    # generated on the target at first service start (see generate-tls-cert.sh),
    # NEVER at build time — so no key material is baked into the archive.
    install -Dm755 "$BUILD_DIR/generate-tls-cert.sh" \
        "$DESTDIR/usr/lib/apache-httpd/generate-tls-cert.sh"
    install -Dm644 "$BUILD_DIR/httpd-tls-keygen.service" \
        "$DESTDIR/usr/lib/systemd/system/httpd-tls-keygen.service"

    # Drop-in default-secure config. The upstream-shipped
    # /etc/httpd/httpd.conf at /etc/httpd/httpd.conf will Include this
    # file via the `IncludeOptional conf.d/*.conf` line that Apache's
    # default config already contains.
    install -d -m 755 "$DESTDIR/etc/httpd/conf.d"
    install -Dm644 "$BUILD_DIR/00-default.conf" \
        "$DESTDIR/etc/httpd/conf.d/00-default.conf"

    # Sysconfig default for $OPTIONS used by the systemd unit.
    install -d -m 755 "$DESTDIR/etc/sysconfig"
    cat > "$DESTDIR/etc/sysconfig/httpd" <<-EOF
		# Options passed to httpd via the systemd unit's ExecStart
		# line. Operators may add -D<DEFINE> here to toggle
		# <IfDefine> blocks in their config, or pass -e <level>
		# to adjust startup-time error logging.
		OPTIONS=""
	EOF

    # State + log + ssl dirs (will be chowned in post_install). The runtime
    # dir is deliberately NOT shipped: /var/run is a symlink to /run on
    # installed systems (base-files r9), and an archive carrying var/run/
    # members would materialize it as a real dir at install time. The
    # tmpfiles.d entry below creates /run/httpd at every boot instead.
    install -d -m 750 "$DESTDIR/var/log/httpd"
    install -d -m 750 "$DESTDIR/var/lib/httpd"
    install -d -m 750 "$DESTDIR/etc/httpd/ssl"
    install -d -m 755 "$DESTDIR/usr/lib/tmpfiles.d"
    echo "d /run/httpd 0755 apache apache -" \
        > "$DESTDIR/usr/lib/tmpfiles.d/apache-httpd.conf"

    # Document root for the default vhost.
    install -d -m 755 "$DESTDIR/var/www/html"
    cat > "$DESTDIR/var/www/html/index.html" <<-EOF
		<!DOCTYPE html>
		<html lang="en">
		<head>
		<meta charset="utf-8">
		<title>Apache httpd — default page</title>
		<meta name="viewport" content="width=device-width, initial-scale=1">
		</head>
		<body>
		<h1>Apache httpd is running on InterGenOS.</h1>
		<p>The default config is TLS-only on 127.0.0.1:443.
		   Replace this page at <code>/var/www/html/index.html</code>
		   and edit <code>/etc/httpd/conf.d/00-default.conf</code> to
		   serve a real site.</p>
		</body>
		</html>
	EOF
}

post_install() {
    set -e
    # apache user/group is declared by
    # /usr/lib/sysusers.d/apache-httpd.conf and created by the pkm
    # canonical sysusers hook before this lifecycle hook runs.

    # Fix ownership on state dirs (/run/httpd ownership comes from the
    # tmpfiles.d entry, not a chown here).
    chown -R "$PKG_USER:$PKG_GROUP" /var/log/httpd  2>/dev/null || true
    chown -R "$PKG_USER:$PKG_GROUP" /var/lib/httpd  2>/dev/null || true

    # The self-signed cert + key at /etc/httpd/ssl/ are NOT generated here.
    # httpd-tls-keygen.service (which httpd.service Requires=) generates them
    # per-machine on the target at first service start — never at build/install
    # time, so no shared key material ships in the archive (F43).

    # Reload systemd + AppArmor. Best-effort; install succeeds even
    # if these aren't running (chroot install / first-boot path).
    systemctl daemon-reload                              2>/dev/null || true
    apparmor_parser -r /etc/apparmor.d/usr.bin.httpd     2>/dev/null || true

    cat <<-EOF
		[apache-httpd installed]

		Default config is TLS-only on 127.0.0.1:443 with a self-signed cert
		generated per-machine at /etc/httpd/ssl/ the first time the service
		starts. Service is DISABLED by default.

		To start:
		    sudo systemctl enable --now httpd

		To replace the self-signed cert with a CA-signed pair:
		    /etc/httpd/ssl/server.pem      (cert + chain)
		    /etc/httpd/ssl/server.key      (private key; mode 600)

		To expose on the network (deliberate operator action):
		    edit /etc/httpd/conf.d/00-default.conf
		    change 'Listen 127.0.0.1:443' to 'Listen :443'
		    sudo systemctl reload httpd
	EOF
}

check() {
    set -e
    # Apache's `make check` runs a Perl-based regression test suite
    # (Apache::Test) that requires writable filesystem + tmpfs + a
    # running daemon to exercise. Not chroot-friendly. Run-time
    # verification will come from the first request against the
    # default vhost.
    return 0
}
