#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Iana-Etc 20260202
# LFS 13.0 Section 8.4
#
# DESTDIR exception: No build system. Just copies files.

configure() {
    set -e
    : # Nothing to configure
}

build() {
    set -e
    : # Nothing to build
}

do_install() {
    set -e
    mkdir -pv "${DESTDIR}/etc"
    cp -v services protocols "${DESTDIR}/etc"
}
