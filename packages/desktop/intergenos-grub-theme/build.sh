#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# packages/desktop/intergenos-grub-theme/build.sh -- K1 closure.
#
# Installs the operator-authored GRUB scaffold (2026-05-21):
#   1. 12 resolution-specific PNGs under /boot/grub/backgrounds/<WxH>.png
#      (Steam hardware-survey resolution set; covers 16:9 + 16:10 + 21:9 aspect families)
#   2. Default fallback /boot/grub/grub_background.png (1920x1080 copy; most-common
#      monitor resolution per Steam survey -- used when $gfxmode matches none of the 12)
#   3. theme.txt + assets at /boot/grub/themes/intergenos/ (minimal menu styling,
#      no menu border so brand mark stays unobscured)
#   4. /etc/grub.d/06_intergenos_multi_background script (resolution-aware override
#      that runs after upstream 05_debian_theme, composes-with-not-forks)
#   5. /etc/default/grub modifications: GRUB_TIMEOUT_STYLE + GRUB_TIMEOUT +
#      GRUB_GFXMODE chain (12 resolutions + auto) + GRUB_BACKGROUND +
#      GRUB_THEME + GRUB_COLOR_NORMAL + GRUB_COLOR_HIGHLIGHT
#   6. post_install: regenerate /boot/grub/grub.cfg via update-grub

configure() { :; }
build() { :; }

do_install() {
    set -e
    local assets="${ASSETS}"
    if [ -z "$assets" ] || [ ! -d "$assets" ]; then
        # Fallback for builders that don't export ${ASSETS}: derive from
        # this script's location. build.sh is sourced (not invoked), so
        # use ${BASH_SOURCE[0]} (not $0 — that resolves to the calling
        # chroot-build script).
        assets="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/assets"
    fi

    # 1+2. Background images (12 resolutions + fallback) under /boot/grub/.
    install -dv "${DESTDIR}/boot/grub/backgrounds"
    local bgsrc="${assets}/grub-theme/intergenos/backgrounds"
    for res in 800x600 1024x768 \
               1280x800 1366x768 1600x900 1680x1050 1920x1080 1920x1200 \
               2560x1440 2560x1600 3072x1920 3440x1440 3840x2160 3840x2400; do
        install -m 0644 -v "${bgsrc}/${res}.png" "${DESTDIR}/boot/grub/backgrounds/${res}.png"
    done
    # Default fallback at /boot/grub/grub_background.png (loaded by 05_debian_theme
    # via GRUB_BACKGROUND when $gfxmode doesn't match any resolution-specific PNG).
    install -m 0644 -v "${bgsrc}/grub_background.png" "${DESTDIR}/boot/grub/grub_background.png"

    # 3. Theme dir at /boot/grub/themes/intergenos/. Includes theme.txt
    # AND the backgrounds/ subdir the theme.txt `desktop-image` directive
    # references ("backgrounds/grub_background.png" — relative to
    # theme.txt's dir). Surfaced 2026-05-27 install #27: theme.txt was
    # shipping but the backgrounds/ subdir adjacent to it was not,
    # leaving the theme's desktop-image lookup unresolvable. GRUB then
    # silently fell back to no-background rendering at menu display
    # time; the 06_intergenos_multi_background script's background_image
    # only fired during the brief theme-unload window mid-chainload,
    # producing the "off-center cropped flash" the operator surfaced.
    install -dv "${DESTDIR}/boot/grub/themes/intergenos"
    install -m 0644 -v "${assets}/grub-theme/intergenos/theme.txt" \
        "${DESTDIR}/boot/grub/themes/intergenos/theme.txt"
    install -dv "${DESTDIR}/boot/grub/themes/intergenos/backgrounds"
    install -m 0644 -v "${assets}/grub-theme/intergenos/backgrounds/grub_background.png" \
        "${DESTDIR}/boot/grub/themes/intergenos/backgrounds/grub_background.png"

    # 4. /etc/grub.d/06_intergenos_multi_background (must be 0755 to run during update-grub).
    install -dv "${DESTDIR}/etc/grub.d"
    install -m 0755 -v "${assets}/etc-grub.d/06_intergenos_multi_background" \
        "${DESTDIR}/etc/grub.d/06_intergenos_multi_background"

    # 4b. /etc/grub.d/06_uki — auto-detect UKIs at /boot/efi/EFI/Linux/
    # and emit chainloader menuentries. Numbered 06_ so the UKI entries
    # land BEFORE 10_linux's bare-vmlinuz entries, making the UKI the
    # default boot path under GRUB_DEFAULT=0. GRUB's stock 25_bli only
    # detects Boot Loader Spec Type 1 (loader/entries/*.conf used by
    # systemd-boot); it does NOT auto-detect Type 2 UKIs at /EFI/Linux/.
    # Without this script the UKI sits on the ESP invisible to GRUB and
    # 10_linux's bare-vmlinuz-no-initramfs entry kernel-panics on boot.
    # Surfaced 2026-05-26 install attempt #21 first-boot triage.
    install -m 0755 -v "${assets}/etc-grub.d/06_uki" \
        "${DESTDIR}/etc/grub.d/06_uki"

    # 5. /etc/default/grub modifications. Append/replace canonical entries via sed.
    # Idempotent: each setting is removed if present, then appended once.
    install -dm755 "${DESTDIR}/etc/default"
    local defgrub="${DESTDIR}/etc/default/grub"
    if [ ! -f "$defgrub" ]; then
        # Upstream grub package owns the canonical default file. If it's not
        # in DESTDIR (split-install), create a minimal one so update-grub has
        # something to consume.
        cat > "$defgrub" << 'DEFGRUB_HEADER'
# /etc/default/grub -- InterGenOS defaults.
# Generated by the intergenos-grub-theme package. Override entries below
# are appended at install time. Upstream grub default settings remain
# active where this file does not explicitly override them.
GRUB_DEFAULT=0
# D-030 transparent boot (GBC001.5): no `quiet` — show the full boot.
GRUB_CMDLINE_LINUX_DEFAULT=""
GRUB_CMDLINE_LINUX=""
DEFGRUB_HEADER
    fi
    # Marker block for our overrides -- removable + idempotent on reinstall.
    local marker_start="# >>> intergenos-grub-theme >>>"
    local marker_end="# <<< intergenos-grub-theme <<<"
    # Strip any prior block (idempotent reinstall).
    sed -i "/${marker_start}/,/${marker_end}/d" "$defgrub"
    cat >> "$defgrub" << 'DEFGRUB_BLOCK'
# >>> intergenos-grub-theme >>>
# Resolution-aware GRUB backgrounds + theme (K1 closure 2026-05-21).
GRUB_TIMEOUT_STYLE=menu
GRUB_TIMEOUT=10
GRUB_GFXMODE=1920x1080,3840x2400,3840x2160,3440x1440,3072x1920,2560x1600,2560x1440,1920x1200,1680x1050,1600x900,1366x768,1280x800,1024x768,800x600,auto
GRUB_BACKGROUND="/boot/grub/grub_background.png"
GRUB_THEME="/boot/grub/themes/intergenos/theme.txt"
GRUB_COLOR_NORMAL="light-gray/black"
GRUB_COLOR_HIGHLIGHT="light-blue/black"
# <<< intergenos-grub-theme <<<
DEFGRUB_BLOCK
}

post_install() {
    # 6. Regenerate /boot/grub/grub.cfg with our 06_intergenos_multi_background
    # script in the active set. Safe to invoke multiple times.
    if command -v update-grub >/dev/null 2>&1; then
        update-grub || true
    elif command -v grub-mkconfig >/dev/null 2>&1; then
        grub-mkconfig -o /boot/grub/grub.cfg || true
    fi
}
