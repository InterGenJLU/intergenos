#!/bin/bash
# InterGenOS — Install GNOME themes, extensions, icons, cursors, and configs
#
# Runs INSIDE the chroot (or mounted image). Installs from pre-downloaded
# assets at /mnt/intergenos/assets/theming/ — NO network required.
#
# Security: no third-party install.sh scripts are executed. All themes are
# installed by direct file extraction and copy. This eliminates the risk of
# running unaudited code from theme authors with root privileges.
#
# Pre-requisite: run download-theming.sh on the host first.
# Called by create-image.sh or can be run standalone inside the chroot.

set -euo pipefail

CACHE_DIR="/mnt/intergenos/assets/theming"
WORK_DIR="/tmp/theming-work"

log() { echo "[THEMING] $*"; }
warn() { echo "[THEMING] WARNING: $*" >&2; }

if [ ! -d "$CACHE_DIR" ]; then
    warn "Cache directory $CACHE_DIR not found."
    warn "Run scripts/download-theming.sh on the host first."
    exit 1
fi

mkdir -p "$WORK_DIR"

# Verify SHA256 checksums of all pre-downloaded assets
if [ -f "${CACHE_DIR}/SHA256SUMS" ]; then
    log "Verifying asset checksums..."
    if (cd "$CACHE_DIR" && sha256sum -c SHA256SUMS --quiet 2>/dev/null); then
        log "  All checksums verified"
    else
        warn "Checksum verification failed — assets may be corrupted or tampered with"
        warn "Re-run scripts/download-theming.sh and regenerate SHA256SUMS"
        exit 1
    fi
else
    warn "No SHA256SUMS file found — skipping checksum verification"
fi

# Helper: extract a tarball, strip top-level directory
extract_to() {
    local tarball="$1"
    local dest="$2"
    [ -f "$tarball" ] || { warn "Not found: $tarball"; return 1; }
    mkdir -p "$dest"
    tar -xf "$tarball" -C "$dest" --strip-components=1 2>/dev/null || {
        warn "Failed to extract $(basename "$tarball")"
        return 1
    }
}

# ============================================================================
# GNOME Shell Extensions (24)
# ============================================================================

log "=== Installing GNOME Shell Extensions ==="
ext_ok=0
ext_fail=0

for zipfile in "${CACHE_DIR}"/extensions/*.zip; do
    [ -f "$zipfile" ] || continue
    uuid="$(basename "$zipfile" .zip)"
    dest="/usr/share/gnome-shell/extensions/${uuid}"

    if [ -d "$dest" ]; then
        log "  Already installed: $uuid"
        ext_ok=$((ext_ok + 1))
        continue
    fi

    mkdir -p "$dest"
    if unzip -qo "$zipfile" -d "$dest" 2>/dev/null; then
        if ls "$dest"/schemas/*.xml >/dev/null 2>&1; then
            glib-compile-schemas "$dest/schemas/" 2>/dev/null || true
            cp "$dest"/schemas/*.xml /usr/share/glib-2.0/schemas/ 2>/dev/null || true
        fi
        ext_ok=$((ext_ok + 1))
        log "  Installed: $uuid"
    else
        ext_fail=$((ext_fail + 1))
        warn "Failed to extract: $uuid"
    fi
done

chmod -R a+rX /usr/share/gnome-shell/extensions/
log "  Extensions: ${ext_ok} installed, ${ext_fail} failed"

# ============================================================================
# GTK / Shell Themes
# Direct extraction only — NO install.sh scripts executed.
# Themes with release/ dirs: extract pre-built tarballs from release/
# Themes without: copy theme directory directly to /usr/share/themes/
# ============================================================================

log ""
log "=== Installing GTK / Shell Themes ==="
gtk_ok=0

# Orchis — has pre-built release tarballs
extract_to "${CACHE_DIR}/gtk-themes/Orchis-theme.tar.gz" "${WORK_DIR}/orchis" && {
    for f in "${WORK_DIR}"/orchis/release/Orchis*.tar.xz; do
        [ -f "$f" ] && tar -xJf "$f" -C /usr/share/themes/ 2>/dev/null
    done
    gtk_ok=$((gtk_ok + 1))
    log "  Installed: Orchis"
} || warn "Failed: Orchis"

# WhiteSur — has pre-built release tarballs
extract_to "${CACHE_DIR}/gtk-themes/WhiteSur-gtk-theme.tar.gz" "${WORK_DIR}/whitesur-gtk" && {
    for f in "${WORK_DIR}"/whitesur-gtk/release/WhiteSur-*.tar.xz; do
        [ -f "$f" ] && tar -xJf "$f" -C /usr/share/themes/ 2>/dev/null
    done
    gtk_ok=$((gtk_ok + 1))
    log "  Installed: WhiteSur GTK"
} || warn "Failed: WhiteSur GTK"

# Graphite — has pre-built release tarballs
extract_to "${CACHE_DIR}/gtk-themes/Graphite-gtk-theme.tar.gz" "${WORK_DIR}/graphite" && {
    for f in "${WORK_DIR}"/graphite/release/Graphite*.tar.xz; do
        [ -f "$f" ] && tar -xJf "$f" -C /usr/share/themes/ 2>/dev/null
    done
    gtk_ok=$((gtk_ok + 1))
    log "  Installed: Graphite"
} || warn "Failed: Graphite"

# Colloid — has pre-built release tarballs
extract_to "${CACHE_DIR}/gtk-themes/Colloid-gtk-theme.tar.gz" "${WORK_DIR}/colloid-gtk" && {
    for f in "${WORK_DIR}"/colloid-gtk/release/Colloid*.tar.xz; do
        [ -f "$f" ] && tar -xJf "$f" -C /usr/share/themes/ 2>/dev/null
    done
    gtk_ok=$((gtk_ok + 1))
    log "  Installed: Colloid GTK"
} || warn "Failed: Colloid GTK"

# Fluent — has pre-built release tarballs
extract_to "${CACHE_DIR}/gtk-themes/Fluent-gtk-theme.tar.gz" "${WORK_DIR}/fluent-gtk" && {
    for f in "${WORK_DIR}"/fluent-gtk/release/Fluent*.tar.xz; do
        [ -f "$f" ] && tar -xJf "$f" -C /usr/share/themes/ 2>/dev/null
    done
    gtk_ok=$((gtk_ok + 1))
    log "  Installed: Fluent GTK"
} || warn "Failed: Fluent GTK"

# Nordic — ready-to-copy (no install.sh, no release dir)
extract_to "${CACHE_DIR}/gtk-themes/Nordic.tar.gz" "/usr/share/themes/Nordic" && {
    gtk_ok=$((gtk_ok + 1))
    log "  Installed: Nordic"
} || warn "Failed: Nordic"

# Sweet — ready-to-copy
extract_to "${CACHE_DIR}/gtk-themes/Sweet.tar.gz" "/usr/share/themes/Sweet" && {
    gtk_ok=$((gtk_ok + 1))
    log "  Installed: Sweet"
} || warn "Failed: Sweet"

# Dracula — ready-to-copy
extract_to "${CACHE_DIR}/gtk-themes/Dracula.tar.gz" "/usr/share/themes/Dracula" && {
    gtk_ok=$((gtk_ok + 1))
    log "  Installed: Dracula"
} || warn "Failed: Dracula"

# adw-gtk3 — pre-built, extract directly
if [ -f "${CACHE_DIR}/gtk-themes/adw-gtk3.tar.xz" ]; then
    tar -xJf "${CACHE_DIR}/gtk-themes/adw-gtk3.tar.xz" -C /usr/share/themes/ 2>/dev/null
    gtk_ok=$((gtk_ok + 1))
    log "  Installed: adw-gtk3"
fi

# Catppuccin — pre-built zip
if [ -f "${CACHE_DIR}/gtk-themes/catppuccin-mocha-blue.zip" ]; then
    unzip -qo "${CACHE_DIR}/gtk-themes/catppuccin-mocha-blue.zip" -d /usr/share/themes/ 2>/dev/null || true
    gtk_ok=$((gtk_ok + 1))
    log "  Installed: Catppuccin GTK (Mocha Blue)"
fi

# InterGenOS Shell Theme — our custom GNOME Shell theme
if [ -d /mnt/intergenos/assets/intergen-shell-theme ]; then
    mkdir -p /usr/share/themes/InterGenOS
    cp -r /mnt/intergenos/assets/intergen-shell-theme/* /usr/share/themes/InterGenOS/
    gtk_ok=$((gtk_ok + 1))
    log "  Installed: InterGenOS Shell Theme"
fi

log "  GTK themes: ${gtk_ok} installed"

# ============================================================================
# Icon Themes
# Direct copy — all icon themes have ready-to-use directories in their tarballs.
# NO install.sh scripts executed.
# ============================================================================

log ""
log "=== Installing Icon Themes ==="
icon_ok=0

# Papirus — contains Papirus/, Papirus-Dark/, Papirus-Light/ at top level
extract_to "${CACHE_DIR}/icon-themes/papirus-icon-theme.tar.gz" "${WORK_DIR}/papirus" && {
    cp -r "${WORK_DIR}"/papirus/Papirus* /usr/share/icons/ 2>/dev/null
    cp -r "${WORK_DIR}"/papirus/ePapirus* /usr/share/icons/ 2>/dev/null || true
    icon_ok=$((icon_ok + 1))
    log "  Installed: Papirus"
} || warn "Failed: Papirus"

# WhiteSur icons — contains src/. We need the links/ or pre-built dirs
extract_to "${CACHE_DIR}/icon-themes/WhiteSur-icon-theme.tar.gz" "${WORK_DIR}/whitesur-icons" && {
    if [ -d "${WORK_DIR}/whitesur-icons/src" ]; then
        for d in "${WORK_DIR}"/whitesur-icons/src/*/; do
            name=$(basename "$d")
            if [ -f "$d/index.theme" ]; then
                cp -r "$d" "/usr/share/icons/$name"
            fi
        done
    fi
    # Also check for pre-built links/ or release/ dirs
    for d in "${WORK_DIR}"/whitesur-icons/links/*/; do
        [ -d "$d" ] && cp -r "$d" /usr/share/icons/ 2>/dev/null
    done
    icon_ok=$((icon_ok + 1))
    log "  Installed: WhiteSur Icons"
} || warn "Failed: WhiteSur Icons"

# Tela — contains src/ with index.theme per variant
extract_to "${CACHE_DIR}/icon-themes/Tela-icon-theme.tar.gz" "${WORK_DIR}/tela" && {
    for d in "${WORK_DIR}"/tela/src/*/; do
        [ -f "$d/index.theme" ] && cp -r "$d" /usr/share/icons/ 2>/dev/null
    done
    icon_ok=$((icon_ok + 1))
    log "  Installed: Tela Icons"
} || warn "Failed: Tela Icons"

# Colloid icons
extract_to "${CACHE_DIR}/icon-themes/Colloid-icon-theme.tar.gz" "${WORK_DIR}/colloid-icons" && {
    for d in "${WORK_DIR}"/colloid-icons/src/*/; do
        [ -f "$d/index.theme" ] && cp -r "$d" /usr/share/icons/ 2>/dev/null
    done
    icon_ok=$((icon_ok + 1))
    log "  Installed: Colloid Icons"
} || warn "Failed: Colloid Icons"

# Qogir icons
extract_to "${CACHE_DIR}/icon-themes/Qogir-icon-theme.tar.gz" "${WORK_DIR}/qogir-icons" && {
    for d in "${WORK_DIR}"/qogir-icons/src/*/; do
        [ -f "$d/index.theme" ] && cp -r "$d" /usr/share/icons/ 2>/dev/null
    done
    icon_ok=$((icon_ok + 1))
    log "  Installed: Qogir Icons"
} || warn "Failed: Qogir Icons"

# Kora — contains kora/ and kora-pgrey/ directories
extract_to "${CACHE_DIR}/icon-themes/kora.tar.gz" "${WORK_DIR}/kora" && {
    cp -r "${WORK_DIR}"/kora/kora /usr/share/icons/ 2>/dev/null || true
    cp -r "${WORK_DIR}"/kora/kora-pgrey /usr/share/icons/ 2>/dev/null || true
    icon_ok=$((icon_ok + 1))
    log "  Installed: Kora Icons"
} || warn "Failed: Kora Icons"

# Fluent icons
extract_to "${CACHE_DIR}/icon-themes/Fluent-icon-theme.tar.gz" "${WORK_DIR}/fluent-icons" && {
    for d in "${WORK_DIR}"/fluent-icons/src/*/; do
        [ -f "$d/index.theme" ] && cp -r "$d" /usr/share/icons/ 2>/dev/null
    done
    icon_ok=$((icon_ok + 1))
    log "  Installed: Fluent Icons"
} || warn "Failed: Fluent Icons"

# Cybernetic — InterGenOS canonical icon theme (per README screenshots).
# Tarball ships with top-level "Cybernetic - Blue/" directory containing the
# theme. Author: SethStormR (https://github.com/SethStormR/Cybernetic).
extract_to "${CACHE_DIR}/icon-themes/Cybernetic.tar.gz" "${WORK_DIR}/cybernetic" && {
    for d in "${WORK_DIR}"/cybernetic/*/; do
        [ -f "$d/index.theme" ] && cp -r "$d" /usr/share/icons/ 2>/dev/null
    done
    icon_ok=$((icon_ok + 1))
    log "  Installed: Cybernetic (InterGenOS canonical icon theme)"
} || warn "Failed: Cybernetic"

log "  Icon themes: ${icon_ok} installed"

# ============================================================================
# Cursor Themes — all are pre-built, just extract to /usr/share/icons/
# ============================================================================

log ""
log "=== Installing Cursor Themes ==="
cursor_ok=0

# Bibata variants
for variant in Modern-Classic Modern-Ice Modern-Amber Original-Classic; do
    f="${CACHE_DIR}/cursor-themes/Bibata-${variant}.tar.xz"
    if [ -f "$f" ]; then
        tar -xJf "$f" -C /usr/share/icons/ 2>/dev/null
        cursor_ok=$((cursor_ok + 1))
        log "  Installed: Bibata ${variant}"
    fi
done

# macOS cursors
if [ -f "${CACHE_DIR}/cursor-themes/macOS-cursor.tar.xz" ]; then
    tar -xJf "${CACHE_DIR}/cursor-themes/macOS-cursor.tar.xz" -C /usr/share/icons/ 2>/dev/null
    cursor_ok=$((cursor_ok + 1))
    log "  Installed: macOS cursors"
fi

# Phinger cursors
if [ -f "${CACHE_DIR}/cursor-themes/phinger-cursors.tar.bz2" ]; then
    tar -xjf "${CACHE_DIR}/cursor-themes/phinger-cursors.tar.bz2" -C /usr/share/icons/ 2>/dev/null
    cursor_ok=$((cursor_ok + 1))
    log "  Installed: Phinger cursors"
fi

# WhiteSur cursors — extract and copy dist/ contents
extract_to "${CACHE_DIR}/cursor-themes/WhiteSur-cursors.tar.gz" "${WORK_DIR}/whitesur-cursors" && {
    if [ -d "${WORK_DIR}/whitesur-cursors/dist" ]; then
        cp -r "${WORK_DIR}"/whitesur-cursors/dist/* /usr/share/icons/ 2>/dev/null
    fi
    cursor_ok=$((cursor_ok + 1))
    log "  Installed: WhiteSur Cursors"
} || warn "Failed: WhiteSur Cursors"

log "  Cursor themes: ${cursor_ok} installed"

# ============================================================================
# Fix permissions and rebuild caches
# ============================================================================

log ""
log "=== Fixing permissions and rebuilding caches ==="
chmod -R a+rX /usr/share/themes/ 2>/dev/null || true
chmod -R a+rX /usr/share/icons/ 2>/dev/null || true
chmod -R a+rX /usr/share/gnome-shell/extensions/ 2>/dev/null || true

for theme_dir in /usr/share/icons/*/; do
    if [ -f "${theme_dir}index.theme" ]; then
        gtk-update-icon-cache -q "${theme_dir}" 2>/dev/null || true
    fi
done

glib-compile-schemas /usr/share/glib-2.0/schemas/ 2>/dev/null || true
log "  Done"

# ============================================================================
# System-wide theming defaults — OWNED BY packages/core/intergenos-default-settings
#
# Per D-006 owner directive 2026-05-18 + the T0-7-A/T0-7-C ratification
# commit chain, system-wide GNOME defaults + libadwaita /etc/skel bridge
# are owned by the intergenos-default-settings package as the canonical
# SSoT. The package ships:
#   - /usr/share/glib-2.0/schemas/90_intergenos.gschema.override
#   - /usr/share/glib-2.0/schemas/91_intergenos-extensions.gschema.override
#   - /usr/share/glib-2.0/schemas/92_intergenos-desktop.gschema.override
#   - /etc/skel/.config/gtk-4.0/gtk.css (symlink to canonical theme stylesheet
#     per audit row J-005)
# post_install runs glib-compile-schemas.
#
# This block (formerly install-theming.sh:345-389) is retired here per
# the D-006 SSoT consolidation.
# ============================================================================

# ============================================================================
# Welcome greeter autostart
# ============================================================================

log ""
log "=== Installing Welcome Greeter ==="
if [ -f /mnt/intergenos/assets/intergen-welcome/intergen-welcome.py ]; then
    mkdir -p /usr/share/intergen-welcome
    cp /mnt/intergenos/assets/intergen-welcome/intergen-welcome.py \
       /usr/share/intergen-welcome/
    chmod 755 /usr/share/intergen-welcome/intergen-welcome.py

    mkdir -p /etc/xdg/autostart
    cat > /etc/xdg/autostart/intergen-welcome.desktop << 'DESKEOF'
[Desktop Entry]
Type=Application
Name=InterGenOS Welcome
Comment=First-boot setup and customization
Exec=sh -c 'sleep 3 && /usr/bin/python3 /usr/share/intergen-welcome/intergen-welcome.py'
Icon=preferences-system
X-GNOME-Autostart-Phase=Applications
X-GNOME-Autostart-Delay=3
OnlyShowIn=GNOME;
DESKEOF
    log "  Welcome greeter installed with autostart"
else
    warn "Welcome greeter not found at /mnt/intergenos/assets/intergen-welcome/"
fi

# ============================================================================
# Burn My Windows default profile — OWNED BY packages/core/intergenos-default-settings
#
# Per D-015 owner directive 2026-05-20 (D-006 scope extension), the
# Burn My Windows TV-effect profile is shipped by the
# intergenos-default-settings package as the canonical SSoT (migrated in
# 72fc9188). This script was the historical dual-writer; with D-015 the
# block is removed and intergenos-default-settings becomes the sole
# writer of /etc/skel/.config/burn-my-windows/profiles/default.conf.
# ============================================================================

# ============================================================================
# nftables firewall — OWNED BY packages/core/intergenos-firewall-defaults
#
# Per D-011 owner directive 2026-05-19, /etc/nftables.conf is shipped by
# the intergenos-firewall-defaults package as the canonical SSoT for
# system-wide firewall policy. Default-deny INPUT/FORWARD; SSH closed by
# default; established/related + loopback + ICMP echo-request +
# fragmentation-needed + IPv6 ND allowed. The upstream packages/core/
# nftables/ package stays policy-neutral (ships the tool + service unit
# only).
#
# This block (formerly install-theming.sh:435-516) is retired here per
# the D-011 ratification — install-theming.sh was a second writer with
# inverted defaults (audit row J-021). The firewall-defaults package is
# the sole writer now.
#
# The nftables.service unit + 90-nftables.preset are still shipped by
# packages/core/nftables/ (preset enables nftables.service at install
# time; no explicit enable needed here).
# ============================================================================

# ============================================================================
# Cleanup
# ============================================================================

rm -rf "$WORK_DIR"

# ============================================================================
# Summary
# ============================================================================

log ""
log "============================================"
log "  Theming installation complete"
log "  Extensions: ${ext_ok} installed, ${ext_fail} failed"
log "  GTK themes: ${gtk_ok} installed"
log "  Icon themes: ${icon_ok} installed"
log "  Cursor themes: ${cursor_ok} installed"
log "  Welcome greeter: configured"
log "  Burn My Windows: TV effect default"
log "  Firewall: nftables enabled"
log "============================================"
