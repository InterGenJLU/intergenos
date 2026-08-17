#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# font-noto 2025.12.01 — Google Noto fonts
# Pre-built TTF fonts — no compilation needed
# Installs core Noto Sans/Serif/Mono (not the full 1GB+ collection)

do_install() {
    set -e
    install -v -d -m755 "${DESTDIR}/usr/share/fonts/noto"

    # Core fonts — Sans, Serif, Mono (Latin + common scripts)
    install -v -m644 fonts/NotoSans/hinted/ttf/*.ttf       "${DESTDIR}/usr/share/fonts/noto/" 2>/dev/null || true
    install -v -m644 fonts/NotoSerif/hinted/ttf/*.ttf      "${DESTDIR}/usr/share/fonts/noto/" 2>/dev/null || true
    install -v -m644 fonts/NotoSansMono/hinted/ttf/*.ttf   "${DESTDIR}/usr/share/fonts/noto/" 2>/dev/null || true

    # Emoji handled by sibling noto-color-emoji package (sourced from the
    # standalone googlefonts/noto-emoji repo — the aggregator tarball
    # doesn't ship fonts/NotoColorEmoji/*.ttf so a silent fallback here
    # would be a stub per Rule 21. Keep that concern out of this recipe.)
}

post_install() {
    set -e
    fc-cache -f 2>/dev/null || true
}
