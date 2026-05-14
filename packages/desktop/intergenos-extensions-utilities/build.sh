#!/bin/bash
# intergenos-extensions-utilities 1.0 — Utilities-category GNOME Shell extensions super-package
#
# Welcomer category: Utilities (8 extensions, two of which —
# appindicatorsupport and bluetooth-quick-connect — are enabled by
# default per the gschema enabled-extensions list).
#
# Bundled extensions:
#   * appindicatorsupport@rgcjonas.gmail.com  — GPL-2.0; default-enabled; system tray icons
#   * bluetooth-quick-connect@bjarosze.gmail.com — GPL-2.0; default-enabled; BT connect-from-panel
#   * caffeine@patapon.info                   — GPL-2.0; disable auto-suspend toggle
#   * Vitals@CoreCoding.com                   — GPL-2.0; CPU/RAM/temp in panel
#   * mediacontrols@cliffniff.github.com      — GPL-3.0; now-playing info in panel
#   * gsconnect@andyholmes.github.io          — GPL-2.0; phone integration (KDEConnect protocol)
#   * just-perfection-desktop@just-perfection — GPL-3.0; tweak every aspect of the GNOME Shell
#   * ding@rastersoft.com                     — GPL-3.0; desktop icons NG (drag-and-drop desktop)

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
        appindicatorsupport@rgcjonas.gmail.com \
        bluetooth-quick-connect@bjarosze.gmail.com \
        caffeine@patapon.info \
        Vitals@CoreCoding.com \
        mediacontrols@cliffniff.github.com \
        gsconnect@andyholmes.github.io \
        just-perfection-desktop@just-perfection \
        ding@rastersoft.com ; do
        if [ ! -d "${uuid}" ] ; then
            echo "intergenos-extensions-utilities: bundle missing expected extension '${uuid}'" >&2
            exit 1
        fi
        cp -a "${uuid}" "${DESTDIR}/usr/share/gnome-shell/extensions/"
    done
}

post_install() {
    set -e
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
}
