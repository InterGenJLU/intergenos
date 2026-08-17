#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# php 8.4.11 — PHP scripting language + runtime.
# Recipe reference: BLFS 12.x "PHP" (general/php.html). Configure flags follow
# the book verbatim (the researched, known-good set); the release tarball ships
# pre-generated parsers so bison/re2c are not build deps.

configure() {
    set -e
    ./configure --prefix=/usr                \
                --sysconfdir=/etc            \
                --localstatedir=/var         \
                --datadir=/usr/share/php     \
                --mandir=/usr/share/man      \
                --without-pear               \
                --enable-fpm                 \
                --with-fpm-user=apache       \
                --with-fpm-group=apache      \
                --with-config-file-path=/etc \
                --with-zlib                  \
                --enable-bcmath              \
                --with-bz2                   \
                --enable-calendar            \
                --enable-dba=shared          \
                --with-gdbm                  \
                --with-gmp                   \
                --enable-ftp                 \
                --with-gettext               \
                --enable-mbstring            \
                --disable-mbregex            \
                --with-readline
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    # Smoke-verify the freshly-built CLI. The full upstream `make test` suite is
    # thousands of cases with known-environment-sensitive failures and is not run
    # in-chroot; this proves the interpreter parses and executes.
    sapi/cli/php -v
    sapi/cli/php -r 'echo "hello, InterGenOS\n";'
}

do_install() {
    set -e
    make INSTALL_ROOT="$DESTDIR" install

    # Production ini + FPM default configs (BLFS post-install).
    install -v -m644 php.ini-production "${DESTDIR}/etc/php.ini"

    install -v -m755 -d "${DESTDIR}/usr/share/doc/php-${PKG_VERSION}"
    install -v -m644 CODING_STANDARDS* EXTENSIONS NEWS README* UPGRADING* \
        "${DESTDIR}/usr/share/doc/php-${PKG_VERSION}" 2>/dev/null || true

    # First-install FPM config names (the build stages *.default).
    if [ -f "${DESTDIR}/etc/php-fpm.conf.default" ]; then
        mv -v "${DESTDIR}/etc/php-fpm.conf.default" "${DESTDIR}/etc/php-fpm.conf"
    fi
    if [ -f "${DESTDIR}/etc/php-fpm.d/www.conf.default" ]; then
        mv -v "${DESTDIR}/etc/php-fpm.d/www.conf.default" "${DESTDIR}/etc/php-fpm.d/www.conf"
    fi

    # Runtime dirs never ship in the archive (runtime-dir gate catch, first
    # build-verify): --localstatedir stages var/run for the fpm pid. Strip it
    # and declare the runtime dir via tmpfiles.d so systemd creates it at boot.
    rm -rf "${DESTDIR}/var/run"
    install -v -m755 -d "${DESTDIR}/usr/lib/tmpfiles.d"
    echo 'd /run/php-fpm 0755 root root -' > "${DESTDIR}/usr/lib/tmpfiles.d/php-fpm.conf"
}
