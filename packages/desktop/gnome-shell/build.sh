#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# gnome-shell 49.4 — GNOME desktop shell
# BLFS 13.0

configure() {
    set -e

    # InterGenOS patches — applied from packages/desktop/gnome-shell/patches/.
    # The build environment sets IGOS_PACKAGE_DIR to the package recipe
    # directory; fall back to the canonical workspace path if unset (some
    # surgical-rebuild invocations don't propagate it).
    local patches_dir="${IGOS_PACKAGE_DIR:-/mnt/intergenos/packages/desktop/gnome-shell}/patches"
    if [ -d "$patches_dir" ]; then
        for p in "$patches_dir"/*.patch; do
            [ -f "$p" ] || continue
            echo "Applying patch: $(basename "$p")"
            patch -p1 -i "$p"
        done
    fi

    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dtests=false \
          -Dman=false
}

build() {
    set -e
    cd build
    ninja
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install

    # --- InterGenOS GDM greeter customization (gresource injection) ---------
    # The GDM greeter loads its shell theme from the COMPILED
    # /usr/share/gnome-shell/gnome-shell-theme.gresource, NOT the file-based
    # /usr/share/themes/InterGenOS theme (that only reaches the user session via
    # user-theme). So greeter-only CSS must be injected into the gresource. We
    # append two InterGenOS overrides to the stock shell stylesheet(s), then
    # recompile in-place in DESTDIR (so the package archive carries the modified
    # gresource — gnome-shell owns the file, no pkm ownership conflict).
    #   1. Suppress the generic auth-prompt avatar (disable-user-list=true makes
    #      the greeter ask for a typed username; the generic person-icon adds
    #      nothing and collided with the ECG/wordmark logo). Scoped to
    #      .login-dialog so the lock screen keeps its real user avatar.
    #   2. Drop the username box BELOW the brand logo (the auth prompt is
    #      vertically centered on the dialog content box; padding-top on
    #      .login-dialog shifts the centre down while the bottom-pinned logo
    #      stays put).
    # Ref: docs/research/theming/gnome_shell_theming_research_2026-04-09.md
    #      ("GDM uses a GResource" section). Validated on the GBC002.5 install
    #      2026-06-09 (operator: greeter "PERFECT").
    local GRES="${DESTDIR}/usr/share/gnome-shell/gnome-shell-theme.gresource"
    local PREFIX="/org/gnome/shell/theme"
    if [ ! -f "$GRES" ]; then
        echo "gnome-shell: gresource not found at $GRES — greeter overrides NOT injected" >&2
        exit 1
    fi
    local WORK; WORK="$(mktemp -d)"
    local -a ENTRIES
    mapfile -t ENTRIES < <(gresource list "$GRES")
    local e rel
    for e in "${ENTRIES[@]}"; do
        rel="${e#$PREFIX/}"
        mkdir -p "$WORK/$(dirname "$rel")"
        gresource extract "$GRES" "$e" > "$WORK/$rel"
    done
    cat > "$WORK/igos-greeter.css" <<'GREETERCSS'

/* ===== InterGenOS GDM greeter overrides (injected into the gresource) ===== */
.login-dialog .user-widget .user-icon,
.login-dialog .user-widget.vertical .user-icon,
.login-dialog .user-widget.horizontal .user-icon {
    icon-size: 0;
    background-color: transparent;
    background-image: none;
    background-size: 0;
    border: none;
    box-shadow: none;
    padding: 0;
    margin: 0;
}
.login-dialog .user-widget .user-icon StIcon,
.login-dialog .user-widget.vertical .user-icon StIcon,
.login-dialog .user-widget.horizontal .user-icon StIcon {
    padding: 0; width: 0; height: 0;
}
.login-dialog .user-widget,
.login-dialog .user-widget.vertical,
.login-dialog .user-widget.horizontal { spacing: 0; }
.login-dialog { padding-top: 360px; }

/* Greeter background. The GNOME-49 greeter renders ONLY from this gresource
 * (NOT org/gnome/desktop/background picture-uri, NOT the file-based
 * /usr/share/themes/InterGenOS theme — user-theme does not run under
 * --mode=gdm). #lockDialogGroup is the greeter/lock background actor; the PNG
 * is embedded into this same gresource below and referenced by resource:// URL
 * (bare relative url() is unreliable in the greeter). #050810 = --bg-void
 * fallback. This is what actually paints the branded greeter (PI-14 paint). */
#lockDialogGroup {
    background: #050810 url("resource:///org/gnome/shell/theme/igos-greeter-background.png");
    background-repeat: no-repeat;
    background-size: cover;
    background-position: center;
}
GREETERCSS
    local css
    for css in gnome-shell-dark.css gnome-shell-light.css gnome-shell-high-contrast.css; do
        [ -f "$WORK/$css" ] && cat "$WORK/igos-greeter.css" >> "$WORK/$css"
    done

    # Embed the InterGenOS greeter background INTO the gresource so the
    # #lockDialogGroup rule above can reference it via resource:// (the greeter
    # cannot read a file-based theme path). Source from the in-tree asset.
    local GREETER_BG="${IGOS_SOURCE_ROOT:-/mnt/intergenos}/assets/intergen-shell-theme/gnome-shell/greeter-background.png"
    if [ ! -f "$GREETER_BG" ]; then
        echo "gnome-shell: greeter background asset not found at $GREETER_BG — greeter would render unbranded (PI-14)" >&2
        exit 1
    fi
    cp "$GREETER_BG" "$WORK/igos-greeter-background.png"

    {
        echo '<?xml version="1.0" encoding="UTF-8"?>'
        echo '<gresources>'
        echo "  <gresource prefix=\"$PREFIX\">"
        for e in "${ENTRIES[@]}"; do echo "    <file>${e#$PREFIX/}</file>"; done
        echo '    <file>igos-greeter-background.png</file>'
        echo '  </gresource>'
        echo '</gresources>'
    } > "$WORK/igos.gresource.xml"
    glib-compile-resources --sourcedir="$WORK" --target="$GRES" "$WORK/igos.gresource.xml"
    rm -rf "$WORK"
    echo "gnome-shell: injected InterGenOS greeter overrides into gnome-shell-theme.gresource"
}

post_install() {
    set -e
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
    update-desktop-database /usr/share/applications 2>/dev/null || true
}
