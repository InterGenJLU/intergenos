#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# packages/desktop/intergen-toggle/build.sh
#
# intergen-toggle 1.0 -- single-screen GTK4 + libadwaita app that
# toggles the opt-in InterGen AI service on/off. Decided
# 2026-05-22 (theming-arc Walk #24): users who hate the terminal can
# enable InterGen via Applications menu instead of `intergen setup`.
# Mirrors the Forge installer's opt-in row for post-install
# reconfiguration; honors D-010 (no auto-enable; explicit user opt-in).

configure() { :; }
build() { :; }

do_install() {
    set -e
    local assets="${ASSETS}"
    if [ -z "$assets" ] || [ ! -d "$assets" ]; then
        # build.sh is sourced; use ${BASH_SOURCE[0]} (not $0 which is the
        # calling chroot-build script).
        assets="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/assets"
    fi

    # Python entry point at /usr/libexec/intergen-toggle/intergen-toggle.py
    install -dm755 "${DESTDIR}/usr/libexec/intergen-toggle"
    install -m755 "${assets}/intergen-toggle.py" \
        "${DESTDIR}/usr/libexec/intergen-toggle/intergen-toggle.py"

    # /usr/bin/intergen-toggle wrapper that exec's the python entry point.
    install -dm755 "${DESTDIR}/usr/bin"
    cat > "${DESTDIR}/usr/bin/intergen-toggle" << 'WRAPPER_EOF'
#!/bin/sh
exec python3 /usr/libexec/intergen-toggle/intergen-toggle.py "$@"
WRAPPER_EOF
    chmod 755 "${DESTDIR}/usr/bin/intergen-toggle"

    # Applications menu entry.
    install -dm755 "${DESTDIR}/usr/share/applications"
    install -m644 "${assets}/intergen-toggle.desktop" \
        "${DESTDIR}/usr/share/applications/intergen-toggle.desktop"

    # App icon (Icon=intergen-toggle). The InterGen pulse mark framed by a
    # power-on ring — the "enable" glyph. Previously Icon=intergen-toggle
    # resolved to nothing → a generic grey gear in the overview/ArcMenu
    # (operator branding pass 2026-06-11 §D).
    install -dm755 "${DESTDIR}/usr/share/icons/hicolor/scalable/apps"
    install -m644 "${assets}/intergen-toggle.svg" \
        "${DESTDIR}/usr/share/icons/hicolor/scalable/apps/intergen-toggle.svg"
}

post_install() {
    set -e
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache --quiet --force /usr/share/icons/hicolor 2>/dev/null || true
    fi
    update-desktop-database -q /usr/share/applications 2>/dev/null || true
}
