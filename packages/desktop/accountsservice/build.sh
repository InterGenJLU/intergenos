#!/bin/bash
# accountsservice 23.13.9 — D-Bus interface for user account management
# BLFS 13.0

configure() {
    # BLFS: rename tests/dbusmock so build system doesn't fail without dbusmock
    mv tests/dbusmock{,-tests}

    # BLFS: fix test script for renamed directory and Python 3.12+
    sed -e '/accounts_service\.py/s/dbusmock/dbusmock-tests/' \
        -e 's/assertEquals/assertEqual/'                      \
        -i tests/test-libaccountsservice.py

    # BLFS: fix locale test
    sed -i '/^SIMULATED_SYSTEM_LOCALE/s/en_IE.UTF-8/en_HK.iso88591/' tests/test-daemon.py

    # The generate-version.sh script derives version from directory name
    # (accountsservice-X.Y.Z), but our builder extracts to src/ with
    # --strip-components=1. Replace script with one that echoes the version.
    echo "#!/bin/sh" > generate-version.sh
    echo "echo ${PKG_VERSION}" >> generate-version.sh

    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dadmin_group=adm
}

build() {
    cd build

    # BLFS: fix mocklibc for GCC 14+
    grep 'print_indent'     ../subprojects/mocklibc-1.0/src/netgroup.c \
         | sed 's/ {/;/' >> ../subprojects/mocklibc-1.0/src/netgroup.h
    sed -i '1i#include <stdio.h>'                                      \
        ../subprojects/mocklibc-1.0/src/netgroup.h

    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
