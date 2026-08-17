#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# wireless-regdb — Wireless regulatory database
# Data-only package — installs regulatory.db for WiFi compliance

configure() {
    set -e
    : # No configure step
}

build() {
    set -e
    : # No build step — pre-compiled database
}

do_install() {
    set -e
    install -vDm644 regulatory.db "${DESTDIR}/usr/lib/firmware/regulatory.db"
    install -vDm644 regulatory.db.p7s "${DESTDIR}/usr/lib/firmware/regulatory.db.p7s"
}
