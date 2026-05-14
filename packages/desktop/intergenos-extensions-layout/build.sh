#!/bin/bash
# intergenos-extensions-layout 1.0 — Layout-category GNOME Shell extensions super-package
#
# Welcomer category: Layout (4 extensions; none are enabled by default
# in our gschema override — these are user-selectable opt-ins for
# users who want a different shell layout than the GNOME default).
#
# Bundled extensions:
#   * dash-to-dock@micxgx.gmail.com         — GPL-2.0; persistent dock on any screen edge
#   * dash-to-panel@jderose9.github.com     — GPL-2.0; Windows/KDE-style taskbar
#   * arcmenu@arcmenu.com                   — GPL-2.0; full app menu with search + layouts
#   * show-desktop-button@amivaleo          — GPL-3.0; one-click minimize-all-windows

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
    install -dm755 "${DESTDIR}/usr/share/gnome-shell/extensions"
    for uuid in \
        dash-to-dock@micxgx.gmail.com \
        dash-to-panel@jderose9.github.com \
        arcmenu@arcmenu.com \
        show-desktop-button@amivaleo ; do
        if [ ! -d "${uuid}" ] ; then
            echo "intergenos-extensions-layout: bundle missing expected extension '${uuid}'" >&2
            exit 1
        fi
        cp -a "${uuid}" "${DESTDIR}/usr/share/gnome-shell/extensions/"
    done
}

post_install() {
    set -e
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
}
