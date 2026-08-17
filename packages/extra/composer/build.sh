#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# composer 2.10.2 — PHP dependency manager.
#
# Vendor exception (see package.yml): Composer ships only as a sha256-pinned
# PHP archive; there is no from-source shipping path (building the phar needs
# Composer + Box, a circular self-bootstrap). The pinned phar IS the artifact.
# Mirror-first + pin means no runtime `curl | php` installer — the phar is a
# staged, checksum-verified source, installed as a plain executable.

configure() {
    set -e
    :
}

build() {
    set -e
    :
}

check() {
    set -e
    # Verify the pinned phar runs under the just-built PHP.
    php "${IGOS_SOURCES}/composer.phar" --version --no-interaction
}

do_install() {
    set -e
    install -vDm755 "${IGOS_SOURCES}/composer.phar" "${DESTDIR}/usr/bin/composer"
}
