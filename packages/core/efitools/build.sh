#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# efitools — UEFI variable + key management
# Build dep of: sbsigntool

configure() {
    set -e
    return 0
}

build() {
    set -e
    make
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" PREFIX=/usr install
}
