#!/bin/bash
# nordic-theme 2.2.0 — Nordic GTK + gnome-shell theme (Nord palette)
#
# Upstream: github.com/EliverLara/Nordic v2.2.0
# License: GPL-3.0-or-later
#
# Installs one theme directory at /usr/share/themes/Nordic/ providing
# both GTK styles (gtk-2.0, gtk-3.0, gtk-4.0) AND a gnome-shell theme
# in a single bundle — exactly what the welcomer's Nordic combo expects
# (gtk_theme=Nordic + shell_theme=Nordic, same name for both).
#
# Source provenance: upstream ships pre-built CSS — no SCSS compilation
# needed. The release tarball's top-level dir is the theme bundle ready
# for drop-in install.

configure() {
    set -e
    :
}

build() {
    set -e
    :
}

do_install() {
    set -e
    install -dm755 "${DESTDIR}/usr/share/themes/Nordic"
    # Copy all theme content (gtk-2.0, gtk-3.0, gtk-4.0, gnome-shell,
    # cinnamon, xfce, etc.) verbatim. The cinnamon + xfce dirs are
    # harmless dead bytes for GNOME-only InterGenOS but it is
    # simpler to copy the whole tree than to selectively cherry-pick
    # GNOME-relevant subdirs.
    cp -a \
        gtk-2.0 \
        gtk-3.0 \
        gtk-4.0 \
        gnome-shell \
        cinnamon \
        xfce-notify-4.0 \
        assets \
        index.theme \
        "${DESTDIR}/usr/share/themes/Nordic/" 2>/dev/null || cp -a . "${DESTDIR}/usr/share/themes/Nordic/"
    rm -f "${DESTDIR}/usr/share/themes/Nordic/.gitignore" \
          "${DESTDIR}/usr/share/themes/Nordic/.editorconfig" 2>/dev/null || true
}

post_install() {
    set -e
    :
}
