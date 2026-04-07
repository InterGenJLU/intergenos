#!/bin/bash
# font-noto 2025.12.01 — Google Noto fonts
# Pre-built TTF fonts — no compilation needed
# Installs core Noto Sans/Serif/Mono (not the full 1GB+ collection)

do_install() {
    install -v -d -m755 "${DESTDIR}/usr/share/fonts/noto"

    # Core fonts — Sans, Serif, Mono (Latin + common scripts)
    install -v -m644 fonts/NotoSans/hinted/ttf/*.ttf       "${DESTDIR}/usr/share/fonts/noto/" 2>/dev/null || true
    install -v -m644 fonts/NotoSerif/hinted/ttf/*.ttf      "${DESTDIR}/usr/share/fonts/noto/" 2>/dev/null || true
    install -v -m644 fonts/NotoSansMono/hinted/ttf/*.ttf   "${DESTDIR}/usr/share/fonts/noto/" 2>/dev/null || true

    # Emoji
    install -v -m644 fonts/NotoColorEmoji/*.ttf            "${DESTDIR}/usr/share/fonts/noto/" 2>/dev/null || true
}

post_install() {
    fc-cache -f 2>/dev/null || true
}
