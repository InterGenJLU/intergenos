#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# noto-color-emoji 2.051 — Google Noto Color Emoji font
# Pre-built CBDT/CBLC color-bitmap TTF — no compilation needed.
# Lives in /usr/share/fonts/noto/ alongside the other Noto fonts so
# fontconfig picks it up automatically without an extra .conf file.

do_install() {
    set -e
    install -v -d -m755 "${DESTDIR}/usr/share/fonts/noto"
    install -v -m644 fonts/NotoColorEmoji.ttf \
        "${DESTDIR}/usr/share/fonts/noto/NotoColorEmoji.ttf"
}

post_install() {
    set -e
    fc-cache -f 2>/dev/null || true
}
