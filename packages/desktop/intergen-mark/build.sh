#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# packages/desktop/intergen-mark/build.sh
#
# intergen-mark 1.0 -- ships the canonical InterGenOS brand-mark asset
# stack to standard system locations. Closes:
#   - audit-row D-010 (Medium) "intergen-mark brand assets never installed"
#   - audit-row J-016 (Low) "brand assets missing from /usr/share/icons/hicolor/"
#   - theming-arc dispatch Item C (canonical brand-mark package)
#
# Supersedes the duplicate-wordmark bandaid in
# packages/core/intergenos-default-settings/build.sh (release 4); the
# duplicate copy + the assets/intergenos_wordmark_transparent.png in that
# package are scheduled for removal in the follow-up commit once this
# package is the canonical writer at /usr/share/intergenos/.
#
# Source-of-truth pattern: brand-source canonical lives at
# /mnt/intergenos/assets/intergen-mark/ (generate.py + README +
# 9 SVG variants + 26 PNG variants). This package's build.sh reaches
# into that location via IGOS_SOURCE_ROOT, mirroring how
# intergenos-default-settings consumes config/gsettings/ + config/
# burn-my-windows/. Avoids duplicate-asset-in-package; one source.

build() {
    set -e
    # No build step -- pure asset shipping. Files staged in do_install.
    return 0
}

do_install() {
    set -e
    local sources_dir="${IGOS_SOURCE_ROOT:-/mnt/intergenos}/assets/intergen-mark"

    if [ ! -d "${sources_dir}" ]; then
        echo "FATAL: brand-source directory missing: ${sources_dir}" >&2
        exit 1
    fi

    # 1. Hicolor sized icon stack at /usr/share/icons/hicolor/<size>/apps/
    #    intergenos.png. Transparent variants are the hicolor convention
    #    (transparent background expected for icon-theme consumers).
    #    The 9 size dirs (16, 24, 32, 48, 64, 128, 256, 512, 1024) are
    #    all valid hicolor sizes. The non-transparent base PNGs in
    #    assets/intergen-mark/png/ are kept brand-source-only -- not
    #    runtime-needed; consumers that want the tile-background variant
    #    can read directly from the brand-source repo via the
    #    Trademark license carve-out for redistribution.
    for size in 16 24 32 48 64 128 256 512 1024; do
        local dir="${DESTDIR}/usr/share/icons/hicolor/${size}/apps"
        install -dm755 "${dir}"
        install -m644 "${sources_dir}/png/intergenos_icon_transparent_$(printf '%03d' "${size}").png" \
            "${dir}/intergenos.png"
    done

    # 2. Hicolor scalable SVG variants. The 9 SVG variants in
    #    assets/intergen-mark/svg/ map to 4 install entry points:
    #    - intergenos.svg               = simple flat scalable (canonical app icon)
    #    - intergenos-symbolic.svg      = white-on-transparent (panel/symbolic surface)
    #    - intergenos-full.svg          = full-detail icon (high-fidelity surface)
    #    - intergenos-logo.svg          = lockup with wordmark (kept under /usr/share/intergenos/ below;
    #                                     NOT installed to hicolor/scalable/apps/ because logos with
    #                                     wordmarks are not appropriate as the "Icon=intergenos" target)
    install -dm755 "${DESTDIR}/usr/share/icons/hicolor/scalable/apps"
    install -m644 "${sources_dir}/svg/intergenos_icon_simple.svg" \
        "${DESTDIR}/usr/share/icons/hicolor/scalable/apps/intergenos.svg"
    install -m644 "${sources_dir}/svg/intergenos_icon_simple_white.svg" \
        "${DESTDIR}/usr/share/icons/hicolor/scalable/apps/intergenos-symbolic.svg"
    install -m644 "${sources_dir}/svg/intergenos_icon_full.svg" \
        "${DESTDIR}/usr/share/icons/hicolor/scalable/apps/intergenos-full.svg"

    # Symbolic boxed mark for the ArcMenu custom "InterGenOS" category.
    # Greyscale single-fill so GNOME/ArcMenu recolors
    # it to the sidebar text colour. Referenced by the carried ArcMenu patch's
    # Categories entry as Icon name "intergenos-category-symbolic".
    install -m644 "${sources_dir}/symbolic/intergenos_category_symbolic.svg" \
        "${DESTDIR}/usr/share/icons/hicolor/scalable/apps/intergenos-category-symbolic.svg"

    # 3. Pixmaps fallback at /usr/share/pixmaps/intergenos.png. The
    #    Pixmaps location is deprecated per XDG but some legacy apps
    #    + os-release LOGO consumers still read from there. We
    #    install a copy of the 256x256 transparent icon (the canonical
    #    medium-resolution app icon).
    install -dm755 "${DESTDIR}/usr/share/pixmaps"
    install -m644 "${sources_dir}/png/intergenos_icon_transparent_256.png" \
        "${DESTDIR}/usr/share/pixmaps/intergenos.png"

    # 4. /usr/share/intergenos/ stack -- non-app-icon brand assets that
    #    surfaces (welcomer hero, About dialog, GDM logo key, intergen-
    #    toggle wordmark, etc.) reach via fixed paths. THIS PATH IS THE
    #    SUCCESSOR to intergenos-default-settings' duplicate-wordmark
    #    bandaid. The bandaid + duplicate asset copy will be removed in
    #    the follow-up commit.
    install -dm755 "${DESTDIR}/usr/share/intergenos"

    # 4a. Wordmark (with + without alpha background) + header-bar crop.
    #     The full transparent wordmark (1751x898, pulse + tagline + glow)
    #     is the canonical brand asset consumed by the GDM greeter logo
    #     and any surface that wants the full lockup. The header crop
    #     (170x45, tagline + glow removed) is consumed by GTK4 header-bar
    #     surfaces (intergen-welcome, intergen-toggle) where the widget
    #     pins the picture to a 170x46 logical slot -- the full wordmark
    #     either rendered oversized (welcomer top-bar grew with it) or
    #     tripped a Gtk.Picture+ContentFit.CONTAIN measure assertion
    #     ('for_size >= -1' failed in libadwaita layout) when the image
    #     aspect was shorter than the widget's 170:46 = 3.696. Aspect
    #     ratio 170:45 = 3.778 stays just above the widget aspect so the
    #     image is width-bound under CONTAIN, sidestepping the bug.
    install -m644 "${sources_dir}/png/intergenos_wordmark.png" \
        "${DESTDIR}/usr/share/intergenos/intergenos_wordmark.png"
    install -m644 "${sources_dir}/png/intergenos_wordmark_transparent.png" \
        "${DESTDIR}/usr/share/intergenos/intergenos_wordmark_transparent.png"
    install -m644 "${sources_dir}/png/intergenos_wordmark_header.png" \
        "${DESTDIR}/usr/share/intergenos/intergenos_wordmark_header.png"

    # 4a.1. Forge installer hero banner (1920×640, ~3:1) — ECG pulse on
    #       void with subtle circuit-pattern halo. Consumed by the Forge
    #       GUI welcome screen as an edge-to-edge banner in the pre-clamp
    #       slot (see installer/frontend/gui/screens/welcome.py). Source
    #       PNG is operator-authored; treated as a brand-managed asset
    #       and shipped through intergen-mark so /usr/share/intergenos/
    #       is the single canonical home for InterGenOS brand surfaces.
    install -m644 "${sources_dir}/png/intergenos_pulse_forge_hero.png" \
        "${DESTDIR}/usr/share/intergenos/intergenos_pulse_forge_hero.png"

    # 4b. Full logo SVG (icon + wordmark lockup) + transparent + white
    install -m644 "${sources_dir}/svg/intergenos_logo.svg" \
        "${DESTDIR}/usr/share/intergenos/intergenos_logo.svg"
    install -m644 "${sources_dir}/svg/intergenos_logo_transparent.svg" \
        "${DESTDIR}/usr/share/intergenos/intergenos_logo_transparent.svg"
    install -m644 "${sources_dir}/svg/intergenos_logo_white.svg" \
        "${DESTDIR}/usr/share/intergenos/intergenos_logo_white.svg"

    # 4c. Large logo PNG renders (512 / 1024 / 1536 / 2048) for surfaces
    #     that need raster lockup at hero scale (welcomer splash, About
    #     dialogs at 2K+ displays, etc.).
    for size in 512 1024 1536 2048; do
        install -m644 "${sources_dir}/png/intergenos_logo_${size}.png" \
            "${DESTDIR}/usr/share/intergenos/intergenos_logo_${size}.png"
    done
    install -m644 "${sources_dir}/png/intergenos_logo_transparent_1024.png" \
        "${DESTDIR}/usr/share/intergenos/intergenos_logo_transparent_1024.png"
    install -m644 "${sources_dir}/png/intergenos_logo_transparent_2048.png" \
        "${DESTDIR}/usr/share/intergenos/intergenos_logo_transparent_2048.png"

    # 4d. Full-detail icon SVG variants (for surfaces that want the
    #     full-detail icon WITHOUT the wordmark lockup, e.g. circular
    #     avatar slots)
    install -m644 "${sources_dir}/svg/intergenos_icon_full.svg" \
        "${DESTDIR}/usr/share/intergenos/intergenos_icon_full.svg"
    install -m644 "${sources_dir}/svg/intergenos_icon_full_transparent.svg" \
        "${DESTDIR}/usr/share/intergenos/intergenos_icon_full_transparent.svg"
    install -m644 "${sources_dir}/svg/intergenos_icon_full_white.svg" \
        "${DESTDIR}/usr/share/intergenos/intergenos_icon_full_white.svg"

    # Defensive asserts. If brand-source layout drifts, halt the build
    # rather than shipping an incomplete brand-mark package.
    for size in 16 24 32 48 64 128 256 512 1024; do
        if [ ! -f "${DESTDIR}/usr/share/icons/hicolor/${size}/apps/intergenos.png" ]; then
            echo "FATAL: hicolor ${size}x${size} icon missing in DESTDIR" >&2
            exit 1
        fi
    done

    for svg in intergenos.svg intergenos-symbolic.svg intergenos-full.svg intergenos-category-symbolic.svg; do
        if [ ! -f "${DESTDIR}/usr/share/icons/hicolor/scalable/apps/${svg}" ]; then
            echo "FATAL: hicolor scalable ${svg} missing in DESTDIR" >&2
            exit 1
        fi
    done

    if [ ! -f "${DESTDIR}/usr/share/pixmaps/intergenos.png" ]; then
        echo "FATAL: pixmaps/intergenos.png missing in DESTDIR" >&2
        exit 1
    fi

    if [ ! -f "${DESTDIR}/usr/share/intergenos/intergenos_wordmark_transparent.png" ]; then
        echo "FATAL: /usr/share/intergenos/intergenos_wordmark_transparent.png missing in DESTDIR" >&2
        echo "(this is the path the intergenos-default-settings bandaid currently shipped;" >&2
        echo " this package takes ownership of it -- removal failure leaves consumers broken)" >&2
        exit 1
    fi

    if [ ! -f "${DESTDIR}/usr/share/intergenos/intergenos_wordmark_header.png" ]; then
        echo "FATAL: /usr/share/intergenos/intergenos_wordmark_header.png missing in DESTDIR" >&2
        echo "(header-bar wordmark crop consumed by intergen-welcome + intergen-toggle)" >&2
        exit 1
    fi

    if [ ! -f "${DESTDIR}/usr/share/intergenos/intergenos_pulse_forge_hero.png" ]; then
        echo "FATAL: /usr/share/intergenos/intergenos_pulse_forge_hero.png missing in DESTDIR" >&2
        echo "(Forge installer welcome-page hero banner)" >&2
        exit 1
    fi
}

post_install() {
    set -e
    # Update the hicolor icon-theme cache so consumers (file managers,
    # app switchers, GNOME Activities, GDM logo lookup, etc.) start
    # resolving Icon=intergenos to our brand mark instead of upstream
    # stock fallbacks. Defensive || true -- if gtk-update-icon-cache
    # isn't installed at this exact moment (early-bootstrap edge case),
    # the icons stay in place and a later GTK-stack install can rebuild
    # the cache. In normal install ordering, gtk4 is present.
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache --quiet --force /usr/share/icons/hicolor 2>/dev/null || true
    fi
}
