#!/bin/bash
# intergenos-extensions-appearance 1.0 — Appearance-category GNOME Shell extensions super-package
#
# Welcomer category: Appearance (5 extensions, one of which is enabled by
# default per the gschema enabled-extensions list).
#
# Bundled extensions (with welcomer-grouping rationale + license):
#   * blur-my-shell@aunetx           — GPL-3.0; default-enabled; blur effects on panel/overview/lockscreen
#   * burn-my-windows@schneegans...  — GPL-3.0; default-enabled; stylish window open/close animations
#   * rounded-window-corners@fxgn    — GPL-3.0; rounded corners on all windows (fxgn fork active maint)
#   * desktop-cube@schneegans...     — GPL-3.0; 3D cube workspace switching
#   * nightthemeswitcher@romain...   — GPL-3.0; auto light/dark by time of day
#
# Each extension is a self-contained directory under
# /usr/share/gnome-shell/extensions/<uuid>/ containing extension.js
# (or compiled TypeScript output), metadata.json, optional schemas/,
# and an optional locale/ tree. The source tarball preserves the
# upstream layout; do_install copies each UUID dir verbatim.

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
        blur-my-shell@aunetx \
        burn-my-windows@schneegans.github.com \
        rounded-window-corners@fxgn \
        desktop-cube@schneegans.github.com \
        nightthemeswitcher@romainvigier.fr ; do
        if [ ! -d "${uuid}" ] ; then
            echo "intergenos-extensions-appearance: bundle missing expected extension '${uuid}'" >&2
            exit 1
        fi
        cp -a "${uuid}" "${DESTDIR}/usr/share/gnome-shell/extensions/"
    done
}

post_install() {
    set -e
    # Each extension's schemas/ subdir, if present, holds compiled
    # GSettings schemas that need installation to the system schema
    # path. The simplest universal approach is to compile the global
    # /usr/share/glib-2.0/schemas/ tree, which will pick up any
    # extension-shipped overrides that were placed there.
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
}
