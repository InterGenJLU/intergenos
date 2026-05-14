#!/bin/bash
# user-theme 1.0 — GNOME Shell user-theme extension (standalone)
#
# Upstream provenance: extracted from gnome-shell-extensions 48.3 (the last
# bundle release that still ships user-theme). In gnome-shell-extensions
# 49.0+, upstream split user-theme out of the bundle and never published
# a separate maintained component — so distros that want user-theme
# functionality on GNOME 49 either vendor it standalone (this package)
# or live without shell-theme selection.
#
# Why we need it: InterGenOS ships the InterGenOS shell theme +
# 5 alternates referenced in the welcomer's Appearance combos. The
# org.gnome.shell.extensions.user-theme gschema is consulted by gnome-shell
# to load /usr/share/themes/<name>/gnome-shell/gnome-shell.css. Without
# this extension, GNOME's only shell theme is the built-in Adwaita;
# changing the welcomer's combo doesn't change the shell.
#
# License: GPL-2.0-or-later (matches upstream gnome-shell-extensions).
#
# Install layout (matches the upstream uuid convention for this extension):
#   /usr/share/gnome-shell/extensions/user-theme@gnome-shell-extensions.gcampax.github.com/
#     extension.js, prefs.js, util.js, metadata.json
#   /usr/share/glib-2.0/schemas/
#     org.gnome.shell.extensions.user-theme.gschema.xml

UUID="user-theme@gnome-shell-extensions.gcampax.github.com"
SHELL_VERSION="49"

configure() {
    set -e
    # Substitute the placeholders from upstream's metadata.json.in into a
    # real metadata.json. Upstream's meson does this via configure_file;
    # we do it by hand since we're not running meson.
    sed \
        -e "s|@uuid@|${UUID}|g" \
        -e "s|@extension_id@|user-theme|g" \
        -e "s|@gschemaname@|org.gnome.shell.extensions.user-theme|g" \
        -e "s|@gettext_domain@|gnome-shell-extensions|g" \
        -e "s|@shell_current@|${SHELL_VERSION}|g" \
        -e "s|@url@|https://gitlab.gnome.org/GNOME/gnome-shell-extensions|g" \
        metadata.json.in > metadata.json
}

build() {
    set -e
    :
}

do_install() {
    set -e
    local extdir="${DESTDIR}/usr/share/gnome-shell/extensions/${UUID}"
    local schemadir="${DESTDIR}/usr/share/glib-2.0/schemas"

    install -dm755 "${extdir}" "${schemadir}"

    install -m644 extension.js "${extdir}/extension.js"
    install -m644 prefs.js     "${extdir}/prefs.js"
    install -m644 util.js      "${extdir}/util.js"
    install -m644 metadata.json "${extdir}/metadata.json"

    install -m644 org.gnome.shell.extensions.user-theme.gschema.xml "${schemadir}/"
}

post_install() {
    set -e
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
}
