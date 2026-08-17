#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# packages/desktop/intergenos-wallpapers/build.sh
#
# Ships 4 first-party 3840x2160 InterGenOS wallpapers + a
# gnome-background-properties XML manifest:
#   1. InterGenOS_Wallpaper_ItIsOnly.png    -- default (decided 2026-05-22)
#   2. InterGenOS_Wallpaper_Helix.png       -- DNA helix
#   3. InterGenOS_Wallpaper_Overwatch.png   -- cosmic overwatch
#   4. InterGenOS_Wallpaper_Pulse.png       -- ECG pulse mark
#
# Sources rendered via Real-ESRGAN x4plus from 1672x941 originals, then
# Lanczos-downscaled to 3840x2160. Per "what other distros do" review
# (Ubuntu / Fedora / Pop!_OS / elementary), single 4K master per wallpaper
# is canonical practice -- GNOME handles per-display Lanczos at draw time.
#
# Default-set is keyed via picture-uri / picture-uri-dark gschema
# overrides shipped by the intergenos-default-settings package (D-006
# SSoT). This package ships only the wallpapers + the GNOME picker
# manifest; it does NOT touch gschema directly.

configure() { :; }
build() { :; }

do_install() {
    set -e
    local assets="${ASSETS}"
    if [ -z "$assets" ] || [ ! -d "$assets" ]; then
        # build.sh is sourced; use ${BASH_SOURCE[0]} (not $0 which is the
        # calling chroot-build script).
        assets="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/assets"
    fi

    install -dv "${DESTDIR}/usr/share/backgrounds/intergenos"
    for wp in ItIsOnly Helix Overwatch Pulse; do
        install -m 0644 -v "${assets}/wallpapers/InterGenOS_Wallpaper_${wp}.png" \
            "${DESTDIR}/usr/share/backgrounds/intergenos/InterGenOS_Wallpaper_${wp}.png"
    done

    # 00- prefix sorts the manifest before adwaita.xml + every other
    # /usr/share/gnome-background-properties/*.xml entry; gnome-control-
    # center loads them in directory order so our 4 first-party
    # wallpapers render first in the Appearance > Background picker.
    install -dv "${DESTDIR}/usr/share/gnome-background-properties"
    install -m 0644 -v "${assets}/00-intergenos.xml" \
        "${DESTDIR}/usr/share/gnome-background-properties/00-intergenos.xml"
}
