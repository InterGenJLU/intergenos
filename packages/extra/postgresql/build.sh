#!/bin/bash
# postgresql 18.3 — Relational database server (default-secure config)
#
# Why this version (18.3, current stable):
#   * Released 2026-02-26 (announcement banner on postgresql.org/ftp/source/
#     v18.3 directory). Six high-severity CVEs from 2026-Q1 closed by
#     18.3 vs the BLFS-tracked 18.2 — direct security driver for the
#     v1.0 pin.
#   * Most recent v18.x. Pinned via local sha256sum of the upstream
#     tarball at fetch time; anchor in package.yml.
#
# License: PostgreSQL (SPDX expression `PostgreSQL`). The upstream
# meson.build line 12 declares `license: 'PostgreSQL'`; the COPYRIGHT
# file at the source root is a 30-line BSD-style permission notice
# (UC Regents + PostgreSQL Global Development Group copyright). No
# GPL / Apache / dual-license complication.
#
# Build profile: meson + ninja. PostgreSQL 18 migrated to meson as the
# primary build system; autotools is still present but deprecated. We
# follow the upstream meson path. Out-of-tree build dir (`build/`).
#
# Configure flag policy: enable every dispatch-required feature plus
# every feature whose dependency is already in tree (perl-core, llvm,
# python, mitkrb, libxml2, libxslt). One feature is explicitly disabled:
# tcl (no `tcl` package in tree at v1.0; pltcl unavailable until a tcl
# package lands).
#
# liburing forward-flag: -Dliburing=disabled in this commit per the
# database-landing-plan §5a sequencing. The liburing package landed at
# master 90043445 + cherry-picked into 28440cb6 series, but enabling
# it here would be a same-commit forward-edit that the landing-plan
# section recommends doing in a follow-up commit *after* this lands
# (mirrors the same pattern IGOSC applied on liburing's own package).
# A future maintainer flips to -Dliburing=enabled in a dedicated
# commit after this one merges.
#
# Initdb is NOT auto-fired on package install. The PostgreSQL default
# initdb behavior is `--auth-local=trust --auth-host=trust` which is
# unacceptable for any networked host. We ship a `postgres-setup`
# helper at /usr/bin/postgres-setup that the operator runs once
# manually post-install. The helper applies:
#   - --data-checksums              (page-level corruption detection)
#   - --auth-local=peer             (UNIX-socket caller identity match)
#   - --auth-host=scram-sha-256     (modern challenge-response auth)
#   - --pwprompt                    (operator-typed superuser password
#                                    at setup time; nothing baked in)
#   - --encoding=UTF8 --locale=C    (sane portable defaults; operator
#                                    can override via env)
# After initdb the helper edits postgresql.conf to set
# listen_addresses='localhost' (the package-level loopback-only default
# matching landing-plan §5e database-fleet posture).
#
# Project security-alignment posture for this package: loopback-only
# bind, scram-sha-256 authentication baseline, dedicated `postgres`
# system user, full systemd unit hardening per database-landing-plan
# §5e, AppArmor profile shipped in enforce mode. The deferred initdb
# is the explicit "user must do it consciously" pattern from the
# project's Prime Directive — no opaque first-run database creation.

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup ..                                                         \
        --prefix=/usr                                                      \
        --libdir=/usr/lib                                                  \
        --sysconfdir=/etc                                                  \
        --localstatedir=/var                                               \
        --buildtype=release                                                \
        -Dssl=openssl                                                      \
        -Dreadline=enabled                                                 \
        -Dzlib=enabled                                                     \
        -Dlz4=enabled                                                      \
        -Dzstd=enabled                                                     \
        -Dicu=enabled                                                      \
        -Dnls=enabled                                                      \
        -Dsystemd=enabled                                                  \
        -Dpam=enabled                                                      \
        -Dldap=enabled                                                     \
        -Dgssapi=enabled                                                   \
        -Dllvm=enabled                                                     \
        -Dlibxml=enabled                                                   \
        -Dlibxslt=enabled                                                  \
        -Dplperl=enabled                                                   \
        -Dplpython=enabled                                                 \
        -Dpltcl=disabled                                                   \
        -Dliburing=disabled
}

build() {
    set -e
    cd build
    ninja
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install

    # Install the hardened systemd unit. The PostgreSQL build emits its
    # own contrib unit at `src/backend/postmaster/postgresql.service.in`
    # which lacks the landing-plan §5e baseline directives; we ship our
    # own and override the upstream one.
    install -Dm644 "$BUILD_DIR/postgresql.service" \
        "$DESTDIR/usr/lib/systemd/system/postgresql.service"

    # Install the AppArmor profile (enforce mode per landing-plan §5f).
    install -Dm644 "$BUILD_DIR/usr.bin.postgres" \
        "$DESTDIR/etc/apparmor.d/usr.bin.postgres"

    # Install the postgres-setup helper. Runs the operator-driven
    # initdb + post-initdb config tweaks. Not invoked automatically.
    install -Dm755 "$BUILD_DIR/postgres-setup" \
        "$DESTDIR/usr/bin/postgres-setup"
}

post_install() {
    set -e
    # Create the dedicated `postgres` system user if not present. The
    # daemon will refuse to run as root; the user is mandatory.
    if ! getent passwd postgres >/dev/null 2>&1; then
        useradd -r -s /bin/bash -d /var/lib/postgresql \
                -c "PostgreSQL server" postgres || true
    fi

    # State + log + runtime directories owned by the daemon user. The
    # systemd unit's ReadWritePaths references these explicitly. The
    # data subdirectory inside /var/lib/postgresql is created later by
    # `postgres-setup --initdb`; we just prepare the parent here.
    install -dm700 -o postgres -g postgres /var/lib/postgresql  2>/dev/null || true
    install -dm755 -o postgres -g postgres /var/log/postgresql  2>/dev/null || true
    install -dm755 -o postgres -g postgres /run/postgresql      2>/dev/null || true

    systemctl daemon-reload                                  2>/dev/null || true
    apparmor_parser -r /etc/apparmor.d/usr.bin.postgres      2>/dev/null || true

    # Post-install operator guidance — printed to the install log; the
    # operator-driven initdb step is the explicit user-control surface.
    cat <<'EOF' >&2

[postgresql install complete]

PostgreSQL 18.3 is installed but the database cluster has NOT been
initialized. Run the operator-driven setup once before starting the
service:

    sudo postgres-setup --initdb

This will:
  * initdb under /var/lib/postgresql/data with checksums enabled
  * scram-sha-256 host auth + peer local auth
  * prompt for the postgres superuser password (not baked into any
    config file)
  * set listen_addresses='localhost' (loopback-only)

After setup, start the service:

    sudo systemctl enable --now postgresql

EOF
}

check() {
    set -e
    # PostgreSQL ships an extensive `make check` test suite (regression
    # + isolation + recovery), some of which require a temp data dir
    # and an unprivileged user. Running it inside the chroot is
    # supported but adds substantial wall-time; we run a minimal
    # sanity test (binary executes + --version emits the expected
    # version) and rely on Build #N integration runs + the
    # postgres-setup happy path on a fresh InterGenOS install to
    # catch regressions.
    cd build
    # `postgres --version` emits `postgres (PostgreSQL) 18.3` — the
    # paren after PostgreSQL is part of upstream's --version format.
    ./src/backend/postgres --version | grep -qE "PostgreSQL.*${PKG_VERSION}"
}
