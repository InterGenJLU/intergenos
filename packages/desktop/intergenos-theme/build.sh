#!/bin/bash
# intergenos-theme 1.0.0 — InterGenOS native metatheme
#
# The InterGenOS metatheme bundles GTK 3, GTK 4, and GNOME Shell stylesheets
# into a single named theme ("InterGenOS"). The visual identity is ECG blue
# on deep navy — the system's signature aesthetic.
#
# The index.theme metatheme references companion assets that are not
# bundled in this package (separation of concerns — those have their own
# upstream maintainers):
#
#   * IconTheme=Papirus-Dark      from packages/desktop/papirus-icon-theme
#   * CursorTheme=Bibata-Modern-Classic
#                                 from packages/desktop/bibata-cursor-theme
#
# License: GPL-3.0-or-later (matches the broader InterGenOS in-house
# component licensing posture). The theme is original work authored on
# IGOSC; no upstream attribution.
#
# Build profile: custom drop-in. The source tarball contains the four
# theme files at the canonical layout — no configure, no compile. The
# do_install function copies the entire extracted tree to
# /usr/share/themes/InterGenOS/.

configure() {
    set -e
    # No-op — drop-in static assets.
    :
}

build() {
    set -e
    # No-op — no compilation needed for CSS-only theme.
    :
}

do_install() {
    set -e
    install -dm755 "${DESTDIR}/usr/share/themes/InterGenOS"
    cp -a ./* "${DESTDIR}/usr/share/themes/InterGenOS/"

    # Sanity check — the metatheme is incomplete without all four files.
    for f in index.theme gtk-3.0/gtk.css gtk-4.0/gtk.css gnome-shell/gnome-shell.css ; do
        if [ ! -f "${DESTDIR}/usr/share/themes/InterGenOS/${f}" ] ; then
            echo "intergenos-theme: missing required file ${f}" >&2
            exit 1
        fi
    done

    # libadwaita bridge — every libadwaita app ignores the system gtk-theme
    # gsetting by design (the Adwaita stylesheet is compiled into the
    # library). The single CSS hook libadwaita honors is the per-user
    # ~/.config/gtk-4.0/gtk.css, which it prepends to its compiled
    # stylesheet at app startup. Our InterGenOS theme uses @define-color
    # overrides for the libadwaita semantic palette — these only take
    # effect through this bridge file. Without it, Settings / Files /
    # Text Editor / Calculator / Weather / Calendar / Clocks / Contacts
    # / Music render with stock Adwaita.
    #
    # Ship the bridge via /etc/skel so every new user (installed-system
    # useradd OR live-mode init.sh `cp -a /etc/skel/. /home/liveuser/`)
    # gets the symlink in their freshly-created home. cp -a preserves
    # symlinks (does NOT dereference) so the symlink-to-theme-CSS pattern
    # transfers correctly. gtk-3 gets the same treatment for the legacy-
    # GTK3 app surface that does not go through libadwaita.
    install -dm755 "${DESTDIR}/etc/skel/.config/gtk-4.0"
    install -dm755 "${DESTDIR}/etc/skel/.config/gtk-3.0"
    ln -snf /usr/share/themes/InterGenOS/gtk-4.0/gtk.css \
        "${DESTDIR}/etc/skel/.config/gtk-4.0/gtk.css"
    ln -snf /usr/share/themes/InterGenOS/gtk-3.0/gtk.css \
        "${DESTDIR}/etc/skel/.config/gtk-3.0/gtk.css"
}

post_install() {
    set -e
    # GTK themes have no compiled cache — they are read at app/shell startup.
    # No post_install action is needed; this hook is present for convention.
    :
}
