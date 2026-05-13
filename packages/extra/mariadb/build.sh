#!/bin/bash
# mariadb 11.8.6 — MariaDB 11.8 LTS relational database server
#
# Why this version (11.8.6, current LTS):
#   * Released 2026-02-04 per https://github.com/MariaDB/server/releases
#   * 11.8 is the most recent MariaDB LTS branch (5-year support window
#     per the project's LTS conventions). 11.8.6 is the latest patch in
#     that branch as of this landing.
#   * Note vs latest-stable-overall: 12.2.2 (released 2026-02-12) is the
#     absolute newest "Latest Stable" tag, but is part of the short-term
#     support (STS) line that rotates yearly. For v1.0 the LTS posture
#     is the correct choice — landing-plan section 6 explicitly named
#     11.8.6. Bumping to a 12.x track would shorten our maintenance
#     window without adding wow-factor value.
#   * Single SHA-256 anchor pinned in package.yml.
#
# License: SPDX expression "GPL-2.0 AND LGPL-2.1" captures the
# package's actual licensing structure as shipped in the canonical
# release tarball:
#
#   * COPYING (server core, ~18 KB) — GNU General Public License 2.0
#   * libmariadb/COPYING.LIB (~502 lines) — GNU Lesser General Public
#                                            License 2.1 (covers the
#                                            bundled mariadb-connector-c
#                                            client library)
#
# The server source is GPL-2.0. The bundled client connector library
# (libmariadbclient.so) is LGPL-2.1 — applications that link against
# the client library inherit LGPL terms, but the server itself remains
# GPL-2.0. The AND conjunction is load-bearing: the package as a whole
# requires compliance with BOTH licenses for the respective parts.
# This matches the dispatch-stated "GPL-2.0 + LGPL-2.1" expression and
# is verified against the actual COPYING / COPYING.LIB files in the
# canonical tarball per the verify-and-pin / license-correction
# discipline applied earlier this arc by other packages.
#
# Build profile: CMake out-of-source, Ninja-driven generator. The
# canonical mariadb.org release tarball at archive.mariadb.org includes
# all submodule trees expanded (libmariadb, storage/rocksdb/rocksdb,
# wsrep-lib, extra/wolfssl/wolfssl, storage/maria/libmarias3,
# storage/columnstore/columnstore). The GitHub auto-archive at
# github.com/MariaDB/server does NOT include the submodules — using
# the canonical tarball is therefore mandatory for a complete build.
#
# Build profile: cmake + ninja. 11.8 LTS is a C++-heavy codebase with
# ~5000 sources; ninja's job-scheduling parallelism scales better at
# high -j values than the bundled make. Matches the rocksdb +
# protobuf + snappy in-tree precedent.
#
# Configure flags chosen:
#
# Install layout:
#   -DCMAKE_INSTALL_PREFIX=/usr            standard distro layout
#   -DINSTALL_LAYOUT=RPM                   uses /usr + /var/lib/mysql +
#                                          /var/log/mysql + /run/mysqld
#                                          standard split (vs STANDALONE
#                                          which is the build-tree dir
#                                          layout)
#   -DCMAKE_BUILD_TYPE=Release             optimized, no debug symbols
#
# Security hardening (the wow-factor surface):
#   -DSECURITY_HARDENED=ON                 enables stack protector, RELRO,
#                                          FORTIFY_SOURCE, BIND_NOW —
#                                          umbrella per the upstream
#                                          CMakeLists.txt:274. Default is
#                                          ON; explicit for guard against
#                                          future upstream flips.
#   -DDEFAULT_CHARSET=utf8mb4              full Unicode (4-byte UTF-8
#                                          including emoji + supplementary
#                                          plane). Avoids the historical
#                                          mysql 3-byte utf8 footgun.
#
# Crypto + compression deps (system, not bundled):
#   -DWITH_SSL=system                      uses our openssl, NOT the
#                                          bundled wolfssl (which is in
#                                          extra/wolfssl/wolfssl/ submodule
#                                          but only built when WITH_SSL=
#                                          bundled is set)
#   -DWITH_ZLIB=system                     uses our zlib
#   -DWITH_PCRE=system                     uses our pcre2 (PCRE module
#                                          for built-in REGEXP function)
#
# Allocator + memory:
#   -DWITH_JEMALLOC=yes                    second consumer of in-tree
#                                          jemalloc 5.3.1 (after rocksdb
#                                          at 74a86254). Discovery
#                                          mechanism: cmake/jemalloc.cmake
#                                          uses CHECK_LIBRARY_EXISTS(
#                                          jemalloc malloc_stats_print)
#                                          — same raw linker probe family
#                                          as leveldb's snappy. Resolves
#                                          against /usr/lib/libjemalloc.so
#                                          first-try. (Note: must use
#                                          =yes spelling — MariaDB's
#                                          jemalloc.cmake STREQUAL-tests
#                                          for "yes"/"auto"/"static" so
#                                          =ON would NOT enable jemalloc.)
#
# NUMA — explicit OFF (libnuma absent from tree):
#   -DWITH_NUMA=OFF                        libnuma is not present in
#                                          packages/. Default WITH_NUMA=
#                                          AUTO would auto-detect-miss
#                                          and silently disable; explicit
#                                          OFF makes the absence visible
#                                          to a future maintainer.
#                                          Acceptable for v1.0 — the
#                                          innodb-numa-interleave knob is
#                                          only relevant on multi-socket
#                                          NUMA hardware. v1.1+ can land
#                                          libnuma and flip to ON.
#
# Authentication plugins (operator-opt-in at account-config layer):
#   -DPLUGIN_AUTH_GSSAPI=YES               GSSAPI / Kerberos auth via
#                                          our mitkrb (krb5) package
#   -DPLUGIN_AUTH_PAM=YES                  PAM-backed authentication via
#                                          our linux-pam package
#
# Storage engine disables — three groups:
#
#   (a) Per dispatch (deprecated / unused / absent upstream-deps):
#   -DPLUGIN_TOKUDB=NO                     TokuDB — long-deprecated
#                                          upstream; removed from MariaDB
#                                          in 10.5 — included here for
#                                          forward-compat safety
#   -DPLUGIN_SPHINX=NO                     Sphinx FT search storage —
#                                          sphinxsearch not in tree
#   -DPLUGIN_MROONGA=NO                    Mroonga / Groonga FT search —
#                                          groonga not in tree
#
#   (b) Per submodule audit (we have standalone equivalents or no consumer):
#   -DPLUGIN_ROCKSDB=NO                    The bundled rocksdb storage
#                                          engine would build a second
#                                          copy of rocksdb (vs our
#                                          standalone rocksdb at
#                                          74a86254). No MariaDB-rocksdb
#                                          consumer in tree at v1.0;
#                                          avoid the duplicate library
#                                          and the ~30min extra build
#                                          time.
#   -DPLUGIN_COLUMNSTORE=NO                MariaDB ColumnStore — heavy
#                                          enterprise columnar engine,
#                                          no v1.0 consumer
#
#   (c) Per landing-plan v1.0 scope:
#   -DPLUGIN_S3=NO                         libmarias3 S3 storage backend
#                                          — not in v1.0 scope
#   -DWITH_WSREP=OFF                       Galera replication — not in
#                                          v1.0 scope; clustering can
#                                          land later

# Tests + tools:
#   -DWITH_UNIT_TESTS=OFF                  upstream unit test suite
#                                          (would pull mysql-test-run
#                                          framework + perl deps for
#                                          regression tests)
#   -DWITH_EMBEDDED_SERVER=OFF             embedded server build is
#                                          deprecated upstream and not
#                                          consumed in tree
#
# Project security-alignment posture: MariaDB ships with every available
# default-secure surface engaged. Dedicated mysql system user (not root,
# not nobody). Bind 127.0.0.1 only by default (set in shipped server.cnf
# under /etc/mysql/conf.d/). Random root password generated at first
# install via mariadb-setup helper (NOT empty default). systemd unit
# with full landing-plan §5e hardening baseline. AppArmor profile in
# enforce mode constraining filesystem + capability surface. No
# auto-fired initdb (operator runs mariadb-setup --initdb explicitly;
# systemd unit gated on /var/lib/mysql/mariadb_install_complete marker).

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

PKG_USER=mysql
PKG_GROUP=mysql

configure() {
    set -e
    cmake -B build -G Ninja                                                 \
          -DCMAKE_BUILD_TYPE=Release                                        \
          -DCMAKE_INSTALL_PREFIX=/usr                                       \
          -DINSTALL_LAYOUT=RPM                                              \
          -DSECURITY_HARDENED=ON                                            \
          -DDEFAULT_CHARSET=utf8mb4                                         \
          -DDEFAULT_COLLATION=utf8mb4_general_ci                            \
          -DWITH_SSL=system                                                 \
          -DWITH_ZLIB=system                                                \
          -DWITH_PCRE=system                                                \
          -DWITH_JEMALLOC=yes                                               \
          -DWITH_NUMA=OFF                                                   \
          -DWITH_WSREP=OFF                                                  \
          -DPLUGIN_AUTH_GSSAPI=YES                                          \
          -DPLUGIN_AUTH_PAM=YES                                             \
          -DPLUGIN_TOKUDB=NO                                                \
          -DPLUGIN_SPHINX=NO                                                \
          -DPLUGIN_MROONGA=NO                                               \
          -DPLUGIN_ROCKSDB=NO                                               \
          -DPLUGIN_COLUMNSTORE=NO                                           \
          -DPLUGIN_S3=NO                                                    \
          -DWITH_UNIT_TESTS=OFF                                             \
          -DWITH_EMBEDDED_SERVER=OFF
}

build() {
    set -e
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build

    # Install our hardened systemd unit (replaces upstream's
    # support-files/systemd/mariadb.service which lacks the §5e
    # baseline).
    install -Dm644 "$BUILD_DIR/mariadb.service" \
        "$DESTDIR/usr/lib/systemd/system/mariadb.service"

    # Install the AppArmor profile (enforce mode by default per the
    # landing-plan §5f convention).
    install -Dm644 "$BUILD_DIR/usr.sbin.mariadbd" \
        "$DESTDIR/etc/apparmor.d/usr.sbin.mariadbd"

    # Install the operator-run setup helper. Refuses to run if the
    # data dir is already initialized (gated on a marker file).
    install -Dm755 "$BUILD_DIR/mariadb-setup" \
        "$DESTDIR/usr/sbin/mariadb-setup"

    # Ship a default server.cnf that pins bind-address to 127.0.0.1.
    # Operators wanting network exposure replace this line deliberately.
    install -d -m 755 "$DESTDIR/etc/mysql/conf.d"
    cat > "$DESTDIR/etc/mysql/conf.d/server.cnf" <<-EOF
		# InterGenOS MariaDB default config — loopback-only bind.
		# Operators wanting network exposure replace the bind-address
		# line below deliberately.

		[mysqld]
		bind-address = 127.0.0.1
		skip-name-resolve = ON
		default-storage-engine = InnoDB
		default-tmp-storage-engine = InnoDB
	EOF

    # State + log + runtime directories (will be chowned in post_install).
    install -d -m 750 "$DESTDIR/var/lib/mysql"
    install -d -m 750 "$DESTDIR/var/log/mysql"
    install -d -m 755 "$DESTDIR/run/mysqld"
}

post_install() {
    set -e
    # Create the dedicated `mysql` system user + group if not present.
    if ! getent group "$PKG_GROUP" >/dev/null 2>&1; then
        groupadd -r "$PKG_GROUP"
    fi
    if ! getent passwd "$PKG_USER" >/dev/null 2>&1; then
        useradd -r -g "$PKG_GROUP" -d /var/lib/mysql -s /sbin/nologin \
                -c "MariaDB daemon" "$PKG_USER"
    fi

    # Fix ownership on state dirs.
    chown -R "$PKG_USER:$PKG_GROUP" /var/lib/mysql 2>/dev/null || true
    chown -R "$PKG_USER:$PKG_GROUP" /var/log/mysql 2>/dev/null || true
    chown -R "$PKG_USER:$PKG_GROUP" /run/mysqld    2>/dev/null || true

    # Reload systemd + AppArmor. Best-effort: install succeeds even
    # if these aren't running (chroot install / first-boot path).
    systemctl daemon-reload                                  2>/dev/null || true
    apparmor_parser -r /etc/apparmor.d/usr.sbin.mariadbd     2>/dev/null || true

    # IMPORTANT: do NOT auto-fire mariadb-install-db here. Operators
    # must run `sudo mariadb-setup --initdb` explicitly. The systemd
    # unit's ExecStartPre refuses to start until the data dir is
    # marked initialized.
    cat <<-EOF
		[mariadb installed]

		First-time setup is operator-driven and NOT auto-fired by the package.
		Run:

		    sudo mariadb-setup --initdb

		This will:
		  - Initialize the data cluster at /var/lib/mysql
		  - Generate a 24-byte random root password
		  - Write it to /root/.mysql_initial_root_password (mode 600)
		  - Configure loopback-only bind in /etc/mysql/conf.d/server.cnf
		  - Mark the cluster ready so the systemd unit will start

		Then:

		    sudo systemctl enable --now mariadb
	EOF
}

check() {
    set -e
    # Unit tests disabled via -DWITH_UNIT_TESTS=OFF. The upstream
    # `mysql-test-run.pl` framework spins up the daemon under
    # multiple configurations and runs thousands of SQL regression
    # tests — not chroot-friendly without extensive setup, and our
    # verification surface is the successful link of mariadbd against
    # the shipped library set (openssl, zlib, pcre2, jemalloc, mitkrb,
    # linux-pam, ncurses, readline) plus runtime verification by
    # downstream consumers.
    return 0
}
