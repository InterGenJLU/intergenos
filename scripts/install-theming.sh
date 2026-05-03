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
# System-wide theming defaults (dconf + libadwaita override for /etc/skel)
#
# Without this, themes/icons are *available* but GNOME falls back to the
# gsettings-schema baseline (Adwaita/adw-gtk3-dark). Lock in the canonical
# InterGenOS look as the system default for every new user.
#
# libadwaita apps (gnome-control-center, etc.) ignore gtk-theme by default;
# they read ~/.config/gtk-4.0/gtk.css instead. Putting the InterGenOS gtk-4.0
# stylesheet into /etc/skel/.config makes new users automatically inherit it.
# ============================================================================

log ""
log "=== Configuring system-wide theming defaults ==="

# 1. dconf profile + system-wide defaults
mkdir -p /etc/dconf/profile /etc/dconf/db/local.d
cat > /etc/dconf/profile/user << 'DCONFPROF'
user-db:user
system-db:local
DCONFPROF

cat > /etc/dconf/db/local.d/00-intergenos-defaults << 'DCONFDEF'
[org/gnome/desktop/interface]
gtk-theme='InterGenOS'
icon-theme='Cybernetic - Blue'
cursor-theme='Bibata-Modern-Classic'
color-scheme='prefer-dark'

[org/gnome/shell/extensions/user-theme]
name='InterGenOS'
DCONFDEF

dconf update 2>/dev/null || true
log "  dconf defaults written: gtk=InterGenOS, icons=Cybernetic - Blue, cursor=Bibata-Modern-Classic"

# 2. libadwaita override for new users (gnome-control-center, etc.)
if [ -f /usr/share/themes/InterGenOS/gtk-4.0/gtk.css ]; then
    mkdir -p /etc/skel/.config/gtk-4.0
    cp /usr/share/themes/InterGenOS/gtk-4.0/gtk.css \
       /etc/skel/.config/gtk-4.0/gtk.css
    log "  libadwaita override staged in /etc/skel/.config/gtk-4.0/gtk.css"
else
    warn "  /usr/share/themes/InterGenOS/gtk-4.0/gtk.css missing — libadwaita override skipped"
fi

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
# Burn My Windows default profile (TV power-off effect)
# ============================================================================

log ""
log "=== Configuring Burn My Windows ==="
mkdir -p /etc/skel/.config/burn-my-windows/profiles
cat > /etc/skel/.config/burn-my-windows/profiles/default.conf << 'BMWEOF'
[burn-my-windows-profile]
fire-enable-effect=false
tv-enable-effect=true
profile-animation-type=0
BMWEOF
log "  Burn My Windows TV effect configured in /etc/skel"

# ============================================================================
# nftables firewall
# ============================================================================

log ""
log "=== Installing nftables firewall ==="
cat > /etc/nftables.conf << 'NFTEOF'
#!/usr/sbin/nft -f
# InterGenOS default firewall — deny inbound, allow outbound

flush ruleset

table inet filter {
    chain input {
        type filter hook input priority filter; policy drop;

        # Loopback
        iif "lo" accept

        # Drop invalid connections early
        ct state invalid drop

        # Established/related connections
        ct state established,related accept

        # ICMP — rate-limited to prevent ping floods
        ip protocol icmp icmp type echo-request limit rate 10/second accept
        ip protocol icmp accept
        ip6 nexthdr ipv6-icmp icmpv6 type { echo-request } limit rate 10/second accept
        ip6 nexthdr ipv6-icmp icmpv6 type { nd-neighbor-solicit, nd-neighbor-advert, nd-router-solicit, nd-router-advert } accept

        # SSH — rate-limited to prevent brute force
        tcp dport 22 ct state new limit rate 15/minute burst 5 accept

        # mDNS (Avahi zero-conf)
        udp dport 5353 accept

        # DHCP client
        udp sport 67 udp dport 68 accept

        # Log and drop — rate-limited to prevent log flooding
        limit rate 10/minute burst 5 log prefix "nftables-drop: "
        drop
    }

    chain forward {
        type filter hook forward priority filter; policy drop;
    }

    chain output {
        type filter hook output priority filter; policy accept;
    }
}
NFTEOF
chmod 644 /etc/nftables.conf

cat > /usr/lib/systemd/system/nftables.service << 'SVCEOF'
[Unit]
Description=nftables firewall
Documentation=man:nft(8)
Before=network-pre.target
Wants=network-pre.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f /etc/nftables.conf
ExecReload=/usr/sbin/nft -f /etc/nftables.conf
ExecStop=/usr/sbin/nft flush ruleset

[Install]
WantedBy=multi-user.target
SVCEOF

ln -sf /usr/lib/systemd/system/nftables.service \
    /etc/systemd/system/multi-user.target.wants/nftables.service 2>/dev/null || true

log "  nftables firewall installed and enabled"

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
