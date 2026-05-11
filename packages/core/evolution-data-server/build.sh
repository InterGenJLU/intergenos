#!/bin/bash
# evolution-data-server 3.58.3 — Calendar and contacts data server
# BLFS 13.0

configure() {
    set -e
    # WITH_OPENLDAP=ON: LDAP address book backend. openldap is in tree at
    # packages/desktop/openldap; declared as build dep.
    # WITH_KRB5=ON: Kerberos authentication for IMAP/SMTP/cloud accounts.
    # mitkrb is now tier:core (reclassified 2026-05-10); declared as
    # build dep.
    # WITH_LIBDB=OFF: Berkeley DB is deprecated upstream (Oracle license
    # issues); kept off intentionally per BLFS recommendation.
    cmake -B build -G Ninja                    \
          -DCMAKE_INSTALL_PREFIX=/usr          \
          -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5           \
          -DSYSCONF_INSTALL_DIR=/etc           \
          -DENABLE_GTK_DOC=OFF                 \
          -DENABLE_INSTALLED_TESTS=OFF         \
          -DENABLE_VALA_BINDINGS=ON            \
          -DENABLE_INTROSPECTION=ON            \
          -DWITH_LIBDB=OFF                     \
          -W no-dev
}

build() {
    set -e
    ninja -C build -j${IGOS_JOBS}
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" ninja -C build install
}
