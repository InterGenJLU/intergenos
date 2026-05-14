#!/bin/bash
# dracula-gtk-theme 4.0.0 — Dracula GTK + gnome-shell theme
#
# Upstream: github.com/dracula/gtk v4.0.0
# License: GPL-3.0-or-later
#
# Installs one theme directory at /usr/share/themes/Dracula/ providing
# both GTK styles (gtk-2.0, gtk-3.0, gtk-3.20, gtk-4.0) AND a
# gnome-shell theme. The welcomer's Dracula combo uses Dracula as the
# gtk_theme but Orchis-Dark for the shell_theme (which is supplied by
# orchis-theme); Dracula's own shell theme is unused in our combos but
# is included since it ships in the upstream bundle.
#
# Source provenance: upstream ships pre-built CSS, no SCSS compilation.
# Top-level dir of the tarball IS the theme bundle.

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
    install -dm755 "${DESTDIR}/usr/share/themes/Dracula"
    cp -a \
        gtk-2.0 \
        gtk-3.0 \
        gtk-3.20 \
        gtk-4.0 \
        gnome-shell \
        cinnamon \
        assets \
        index.theme \
        "${DESTDIR}/usr/share/themes/Dracula/" 2>/dev/null || cp -a . "${DESTDIR}/usr/share/themes/Dracula/"
    rm -f "${DESTDIR}/usr/share/themes/Dracula/.gitignore" 2>/dev/null || true
    rm -rf "${DESTDIR}/usr/share/themes/Dracula/.github" 2>/dev/null || true
}

post_install() {
    set -e
    :
}
