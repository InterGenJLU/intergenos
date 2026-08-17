#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# font-dejavu 2.37 — DejaVu TrueType fonts
# Pre-built TTF fonts — no compilation needed

do_install() {
    set -e
    install -v -d -m755 "${DESTDIR}/usr/share/fonts/dejavu"
    install -v -m644 ttf/*.ttf "${DESTDIR}/usr/share/fonts/dejavu/"
}

post_install() {
    set -e
    fc-cache -f 2>/dev/null || true
}
