#!/bin/bash
# intergenos-default-settings 1.0.0 — InterGenOS canonical SSoT for GNOME defaults
# https://github.com/InterGenJLU/intergenos
#
# Per D-006 (2026-05-18 owner directive), this package is THE
# source-of-truth for system-wide GNOME / GTK / desktop defaults on
# InterGenOS installs. It replaces the install-theming.sh dconf write
# block (retired per D-006) with a gschema-override approach at the
# upstream layer:
#
#   - /usr/share/glib-2.0/schemas/90_intergenos.gschema.override
#       — GNOME interface (color-scheme, gtk/icon/cursor theme, fonts,
#         terminal colors, background, login banner, dock favorites,
#         window button layout)
#   - /usr/share/glib-2.0/schemas/91_intergenos-extensions.gschema.override
#       — GNOME shell extensions enable list + user-theme name
#   - /usr/share/glib-2.0/schemas/92_intergenos-desktop.gschema.override
#       — Desktop behavior (clock, touchpad, night light, window manager)
#
# post_install runs glib-compile-schemas to compile the overrides into
# the gschemas.compiled binary GNOME reads at session start.
#
# Also ships /etc/skel/.config/gtk-4.0/gtk.css as a SYMLINK to
# /usr/share/themes/InterGenOS/gtk-4.0/gtk.css (audit row J-005 +
# matrix line 343: install-theming.sh:382-389 wrote a regular-file
# copy; canonical posture is symlink so theme updates propagate to
# user homes via cp -a useradd behavior). Composes with the
# packages/desktop/intergenos-theme/ package that ships the theme
# assets at /usr/share/themes/InterGenOS/.
#
# Consolidates audit rows:
#   - J-002 / J-017 / J-029: the 3 gschema overrides were dead-letter
#     on installed systems (copied only by create-image.sh; no package
#     shipped them). This package IS the canonical shipper.
#   - J-005: /etc/skel libadwaita bridge as symlink (was regular-file
#     copy in install-theming.sh).
#
# Sequenced with T0-7-B (install-theming.sh retirement): this package
# lands first; install-theming.sh's dconf + libadwaita-copy + welcome-
# greeter blocks are removed under T0-7-B once this package is the
# canonical writer.
#
# tier=core: GNOME desktop defaults are core system policy on
# InterGenOS, not optional extras. Users who want to remove the
# InterGenOS defaults can `pkm remove intergenos-default-settings`
# (mirrors the intergenos-firewall-defaults pattern in D-011).

build() {
    set -e
    # No build step — pure-config package, files shipped from the
    # in-tree config/gsettings/ directory.
    return 0
}

do_install() {
    set -e
    local sources_dir="${IGOS_SOURCE_ROOT:-/mnt/intergenos}/config/gsettings"

    # 1. Ship the three gschema overrides to /usr/share/glib-2.0/schemas/.
    #    glib-compile-schemas (in post_install) picks them up + merges
    #    them into gschemas.compiled, which GNOME reads at session start.
    install -dm755 "${DESTDIR}/usr/share/glib-2.0/schemas"
    install -m644 \
        "${sources_dir}/90_intergenos.gschema.override" \
        "${DESTDIR}/usr/share/glib-2.0/schemas/90_intergenos.gschema.override"
    install -m644 \
        "${sources_dir}/91_intergenos-extensions.gschema.override" \
        "${DESTDIR}/usr/share/glib-2.0/schemas/91_intergenos-extensions.gschema.override"
    install -m644 \
        "${sources_dir}/92_intergenos-desktop.gschema.override" \
        "${DESTDIR}/usr/share/glib-2.0/schemas/92_intergenos-desktop.gschema.override"

    # 2. /etc/skel libadwaita bridge — SYMLINK (audit J-005 fix).
    #    Points at the InterGenOS theme's canonical gtk-4.0 stylesheet.
    #    useradd preserves symlinks via cp -a, so new user homes get
    #    the symlink + libadwaita apps (gnome-control-center, etc.)
    #    follow it to the canonical theme location and inherit updates
    #    from theme-package upgrades automatically.
    install -dm755 "${DESTDIR}/etc/skel/.config/gtk-4.0"
    ln -sf /usr/share/themes/InterGenOS/gtk-4.0/gtk.css \
        "${DESTDIR}/etc/skel/.config/gtk-4.0/gtk.css"

    # Defensive asserts: confirm the three .gschema.override files
    # actually staged + the symlink staged as a symlink (not a regular
    # file). If the source-tree paths drift, halt the build rather
    # than shipping an empty / J-005-regressing package.
    for f in 90_intergenos 91_intergenos-extensions 92_intergenos-desktop; do
        if [ ! -f "${DESTDIR}/usr/share/glib-2.0/schemas/${f}.gschema.override" ]; then
            echo "FATAL: gschema override missing in DESTDIR: ${f}.gschema.override" >&2
            echo "Source path: ${sources_dir}/${f}.gschema.override" >&2
            exit 1
        fi
    done

    if [ ! -L "${DESTDIR}/etc/skel/.config/gtk-4.0/gtk.css" ]; then
        echo "FATAL: /etc/skel/.config/gtk-4.0/gtk.css did not stage as a symlink" >&2
        echo "(audit J-005 requires symlink, not regular-file copy)" >&2
        exit 1
    fi
}

post_install() {
    set -e
    # Compile the schemas. Overrides only take effect after compilation.
    # The || true is defensive — if glib2 isn't installed at this exact
    # moment (early-bootstrap edge case), the schemas stay in place and
    # a subsequent glib2 install can compile them. In normal install
    # ordering, glib2 is already present and compilation succeeds.
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
}
