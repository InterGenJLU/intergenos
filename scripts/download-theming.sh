#!/bin/bash
# InterGenOS — Download GNOME themes, extensions, icons, and cursors
#
# Runs on the HOST (not in chroot). Downloads all theming assets to
# build/theming/ for offline installation during image creation.
#
# Usage:
#   bash /mnt/intergenos/scripts/download-theming.sh
#
# After running, build/theming/ contains everything install-theming.sh
# needs — no network required during image creation.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CACHE_DIR="${PROJECT_DIR}/build/theming"

mkdir -p "${CACHE_DIR}/extensions"
mkdir -p "${CACHE_DIR}/gtk-themes"
mkdir -p "${CACHE_DIR}/icon-themes"
mkdir -p "${CACHE_DIR}/cursor-themes"

log() { echo "[DOWNLOAD] $*"; }
warn() { echo "[DOWNLOAD] WARNING: $*" >&2; }

TOTAL=0
OK=0
FAIL=0

# ============================================================================
# Helper: download a GitHub release tarball
# ============================================================================

download_github_release() {
    local repo="$1"
    local dest="$2"
    local name="$(basename "$repo")"

    if [ -f "$dest" ]; then
        log "  Already cached: $name"
        OK=$((OK + 1))
        return 0
    fi

    TOTAL=$((TOTAL + 1))
    log "  Fetching: $repo"

    # Try latest release tarball first
    local dl_url
    dl_url=$(curl -sL "https://api.github.com/repos/${repo}/releases/latest" 2>/dev/null | \
        python3 -c "import sys,json; print(json.load(sys.stdin)['tarball_url'])" 2>/dev/null) || {
        # Fallback: default branch
        dl_url="https://github.com/${repo}/archive/refs/heads/master.tar.gz"
    }

    if curl -sL "$dl_url" -o "$dest" 2>/dev/null && [ -s "$dest" ]; then
        OK=$((OK + 1))
        log "  Cached: $name ($(du -h "$dest" | cut -f1))"
    else
        FAIL=$((FAIL + 1))
        rm -f "$dest"
        warn "Failed to download $repo"
    fi
}

# ============================================================================
# Helper: download a GitHub release asset by pattern
# ============================================================================

download_github_asset() {
    local repo="$1"
    local pattern="$2"  # Python expression to match asset name
    local dest="$3"

    if [ -f "$dest" ]; then
        log "  Already cached: $(basename "$dest")"
        OK=$((OK + 1))
        return 0
    fi

    TOTAL=$((TOTAL + 1))
    log "  Fetching asset from: $repo"

    local asset_url
    asset_url=$(curl -sL "https://api.github.com/repos/${repo}/releases/latest" 2>/dev/null | \
        python3 -c "
import sys,json
assets = json.load(sys.stdin).get('assets', [])
for a in assets:
    name = a['name']
    if ${pattern}:
        print(a['browser_download_url'])
        break
" 2>/dev/null) || true

    if [ -n "$asset_url" ]; then
        if curl -sL "$asset_url" -o "$dest" 2>/dev/null && [ -s "$dest" ]; then
            OK=$((OK + 1))
            log "  Cached: $(basename "$dest") ($(du -h "$dest" | cut -f1))"
        else
            FAIL=$((FAIL + 1))
            rm -f "$dest"
            warn "Failed to download asset from $repo"
        fi
    else
        FAIL=$((FAIL + 1))
        warn "No matching asset found in $repo for pattern"
    fi
}

# ============================================================================
# Helper: download GNOME extension from extensions.gnome.org
# ============================================================================

download_extension() {
    local uuid="$1"
    local dest="${CACHE_DIR}/extensions/${uuid}.zip"

    if [ -f "$dest" ]; then
        log "  Already cached: $uuid"
        OK=$((OK + 1))
        return 0
    fi

    TOTAL=$((TOTAL + 1))
    log "  Fetching extension: $uuid"

    local info
    info=$(curl -sL "https://extensions.gnome.org/extension-info/?uuid=${uuid}&shell_version=49" 2>/dev/null) || {
        FAIL=$((FAIL + 1))
        warn "Failed to get info for $uuid"
        return 1
    }

    local dl_url
    dl_url=$(echo "$info" | python3 -c "import sys,json; print(json.load(sys.stdin)['download_url'])" 2>/dev/null) || {
        FAIL=$((FAIL + 1))
        warn "Failed to parse download URL for $uuid"
        return 1
    }

    if curl -sL "https://extensions.gnome.org${dl_url}" -o "$dest" 2>/dev/null && [ -s "$dest" ]; then
        OK=$((OK + 1))
        log "  Cached: $uuid ($(du -h "$dest" | cut -f1))"
    else
        FAIL=$((FAIL + 1))
        rm -f "$dest"
        warn "Failed to download $uuid"
    fi
}

# ============================================================================
# GNOME Shell Extensions (24)
# ============================================================================

log ""
log "=== GNOME Shell Extensions ==="

EXTENSIONS=(
    "user-theme@gnome-shell-extensions.gcampax.github.com"
    "appindicatorsupport@rgcjonas.gmail.com"
    "CoverflowAltTab@palatis.blogspot.com"
    "blur-my-shell@aunetx"
    "bluetooth-quick-connect@bjarosze.gmail.com"
    "burn-my-windows@schneegans.github.com"
    "dash-to-dock@micxgx.gmail.com"
    "dash-to-panel@jderose9.github.com"
    "arcmenu@arcmenu.com"
    "just-perfection-desktop@just-perfection"
    "caffeine@patapon.info"
    "gsconnect@andyholmes.github.io"
    "Vitals@CoreCoding.com"
    "clipboard-indicator@tudmotu.com"
    "ding@rastersoft.com"
    "tilingshell@ferrarodomenico.com"
    "forge@jmmaranan.com"
    "mediacontrols@cliffniff.github.com"
    "desktop-cube@schneegans.github.com"
    "AlphabeticalAppGrid@stuarthayhurst"
    "ddterm@amezin.github.com"
    "nightthemeswitcher@romainvigier.fr"
    "show-desktop-button@amivaleo"
    "rounded-window-corners@fxgn"
)

for uuid in "${EXTENSIONS[@]}"; do
    download_extension "$uuid"
done

# ============================================================================
# GTK / Shell Themes (10)
# ============================================================================

log ""
log "=== GTK / Shell Themes ==="

download_github_release "vinceliuice/Orchis-theme" \
    "${CACHE_DIR}/gtk-themes/Orchis-theme.tar.gz"
download_github_release "vinceliuice/WhiteSur-gtk-theme" \
    "${CACHE_DIR}/gtk-themes/WhiteSur-gtk-theme.tar.gz"
download_github_release "EliverLara/Nordic" \
    "${CACHE_DIR}/gtk-themes/Nordic.tar.gz"
download_github_release "EliverLara/Sweet" \
    "${CACHE_DIR}/gtk-themes/Sweet.tar.gz"
download_github_release "vinceliuice/Graphite-gtk-theme" \
    "${CACHE_DIR}/gtk-themes/Graphite-gtk-theme.tar.gz"
download_github_release "vinceliuice/Colloid-gtk-theme" \
    "${CACHE_DIR}/gtk-themes/Colloid-gtk-theme.tar.gz"
download_github_release "vinceliuice/Fluent-gtk-theme" \
    "${CACHE_DIR}/gtk-themes/Fluent-gtk-theme.tar.gz"
download_github_release "dracula/gtk" \
    "${CACHE_DIR}/gtk-themes/Dracula.tar.gz"

# adw-gtk3 — pre-built release asset (.tar.xz)
download_github_asset "lassekongo83/adw-gtk3" \
    "name.endswith('.tar.xz')" \
    "${CACHE_DIR}/gtk-themes/adw-gtk3.tar.xz"

# Catppuccin — pre-built release (Mocha Blue)
download_github_asset "catppuccin/gtk" \
    "'blue' in name.lower() and 'mocha' in name.lower()" \
    "${CACHE_DIR}/gtk-themes/catppuccin-mocha-blue.zip"

# ============================================================================
# Icon Themes (7)
# ============================================================================

log ""
log "=== Icon Themes ==="

download_github_release "PapirusDevelopmentTeam/papirus-icon-theme" \
    "${CACHE_DIR}/icon-themes/papirus-icon-theme.tar.gz"
download_github_release "vinceliuice/WhiteSur-icon-theme" \
    "${CACHE_DIR}/icon-themes/WhiteSur-icon-theme.tar.gz"
download_github_release "vinceliuice/Tela-icon-theme" \
    "${CACHE_DIR}/icon-themes/Tela-icon-theme.tar.gz"
download_github_release "vinceliuice/Colloid-icon-theme" \
    "${CACHE_DIR}/icon-themes/Colloid-icon-theme.tar.gz"
download_github_release "vinceliuice/Qogir-icon-theme" \
    "${CACHE_DIR}/icon-themes/Qogir-icon-theme.tar.gz"
download_github_release "bikass/kora" \
    "${CACHE_DIR}/icon-themes/kora.tar.gz"
download_github_release "vinceliuice/Fluent-icon-theme" \
    "${CACHE_DIR}/icon-themes/Fluent-icon-theme.tar.gz"

# ============================================================================
# Cursor Themes (4 families)
# ============================================================================

log ""
log "=== Cursor Themes ==="

# Bibata — 4 variants
for variant in Modern-Classic Modern-Ice Modern-Amber Original-Classic; do
    download_github_asset "ful1e5/Bibata_Cursor" \
        "'${variant}' in name and name.endswith('.tar.xz')" \
        "${CACHE_DIR}/cursor-themes/Bibata-${variant}.tar.xz"
done

# macOS cursors
download_github_asset "ful1e5/apple_cursor" \
    "'macOS' in name and name.endswith('.tar.xz')" \
    "${CACHE_DIR}/cursor-themes/macOS-cursor.tar.xz"

# Phinger cursors
download_github_asset "phisch/phinger-cursors" \
    "name.endswith('.tar.bz2')" \
    "${CACHE_DIR}/cursor-themes/phinger-cursors.tar.bz2"

# WhiteSur cursors
download_github_release "vinceliuice/WhiteSur-cursors" \
    "${CACHE_DIR}/cursor-themes/WhiteSur-cursors.tar.gz"

# ============================================================================
# Summary
# ============================================================================

log ""
log "============================================"
log "  Theming download complete"
log "  Cached: ${OK} assets"
log "  Failed: ${FAIL} assets"
log "  Location: ${CACHE_DIR}/"
log "============================================"

if [ "$FAIL" -gt 0 ]; then
    warn "Some downloads failed — re-run to retry (cached assets are skipped)"
    exit 1
fi
