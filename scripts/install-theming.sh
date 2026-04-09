#!/bin/bash
# InterGenOS — Install GNOME themes, extensions, icons, cursors, and configs
#
# Runs INSIDE the chroot (or mounted image). Installs from pre-downloaded
# cache at /mnt/intergenos/build/theming/ — NO network required.
#
# Pre-requisite: run download-theming.sh on the host first.
#
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
        # Compile per-extension schemas if present
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
# Helpers: install themes from tarballs (no eval — safe dispatch)
# ============================================================================

# Extract a tarball to WORK_DIR, return path in THEME_SRC
extract_theme() {
    local tarball="$1"
    local name="$2"

    [ -f "$tarball" ] || { warn "Not found: $tarball"; return 1; }

    THEME_SRC="${WORK_DIR}/$(basename "$tarball" .tar.gz)"
    rm -rf "$THEME_SRC"
    mkdir -p "$THEME_SRC"

    tar -xf "$tarball" -C "$THEME_SRC" --strip-components=1 2>/dev/null || {
        warn "Failed to extract $name"
        return 1
    }
}

# Install a theme that has an install.sh script
install_with_script() {
    local tarball="$1"
    local name="$2"
    local dest_flag="$3"  # e.g., "/usr/share/themes" or "/usr/share/icons"

    extract_theme "$tarball" "$name" || return 1
    (cd "$THEME_SRC" && bash install.sh -d "$dest_flag") 2>/dev/null || {
        warn "Failed to install $name"
        return 1
    }
    log "  Installed: $name"
}

# Install a theme by direct copy
install_with_copy() {
    local tarball="$1"
    local name="$2"
    local dest_dir="$3"  # e.g., "/usr/share/themes/Nordic"

    extract_theme "$tarball" "$name" || return 1
    mkdir -p "$dest_dir"
    cp -r "$THEME_SRC"/. "$dest_dir/"
    log "  Installed: $name"
}

# ============================================================================
# GTK / Shell Themes (10)
# ============================================================================

log ""
log "=== Installing GTK / Shell Themes ==="

install_with_script "${CACHE_DIR}/gtk-themes/Orchis-theme.tar.gz" "Orchis" /usr/share/themes
install_with_script "${CACHE_DIR}/gtk-themes/WhiteSur-gtk-theme.tar.gz" "WhiteSur GTK" /usr/share/themes
install_with_copy "${CACHE_DIR}/gtk-themes/Nordic.tar.gz" "Nordic" /usr/share/themes/Nordic
install_with_copy "${CACHE_DIR}/gtk-themes/Sweet.tar.gz" "Sweet" /usr/share/themes/Sweet
install_with_script "${CACHE_DIR}/gtk-themes/Graphite-gtk-theme.tar.gz" "Graphite" /usr/share/themes
install_with_script "${CACHE_DIR}/gtk-themes/Colloid-gtk-theme.tar.gz" "Colloid GTK" /usr/share/themes
install_with_script "${CACHE_DIR}/gtk-themes/Fluent-gtk-theme.tar.gz" "Fluent GTK" /usr/share/themes
install_with_copy "${CACHE_DIR}/gtk-themes/Dracula.tar.gz" "Dracula" /usr/share/themes/Dracula

# adw-gtk3 — pre-built, just extract to themes dir
if [ -f "${CACHE_DIR}/gtk-themes/adw-gtk3.tar.xz" ]; then
    tar -xJf "${CACHE_DIR}/gtk-themes/adw-gtk3.tar.xz" -C /usr/share/themes/ 2>/dev/null
    log "  Installed: adw-gtk3"
fi

# Catppuccin — pre-built zip
if [ -f "${CACHE_DIR}/gtk-themes/catppuccin-mocha-blue.zip" ]; then
    unzip -qo "${CACHE_DIR}/gtk-themes/catppuccin-mocha-blue.zip" -d /usr/share/themes/ 2>/dev/null || true
    log "  Installed: Catppuccin GTK (Mocha Blue)"
fi

# ============================================================================
# Icon Themes (7)
# ============================================================================

log ""
log "=== Installing Icon Themes ==="

install_with_script "${CACHE_DIR}/icon-themes/papirus-icon-theme.tar.gz" "Papirus" /usr/share/icons
install_with_script "${CACHE_DIR}/icon-themes/WhiteSur-icon-theme.tar.gz" "WhiteSur Icons" /usr/share/icons
install_with_script "${CACHE_DIR}/icon-themes/Tela-icon-theme.tar.gz" "Tela Icons" /usr/share/icons
install_with_script "${CACHE_DIR}/icon-themes/Colloid-icon-theme.tar.gz" "Colloid Icons" /usr/share/icons
install_with_script "${CACHE_DIR}/icon-themes/Qogir-icon-theme.tar.gz" "Qogir Icons" /usr/share/icons

# Kora has no install.sh — direct copy
extract_theme "${CACHE_DIR}/icon-themes/kora.tar.gz" "Kora Icons" && {
    cp -r "$THEME_SRC"/kora "$THEME_SRC"/kora-pgrey /usr/share/icons/ 2>/dev/null || true
    log "  Installed: Kora Icons"
}

install_with_script "${CACHE_DIR}/icon-themes/Fluent-icon-theme.tar.gz" "Fluent Icons" /usr/share/icons

# ============================================================================
# Cursor Themes (4 families)
# ============================================================================

log ""
log "=== Installing Cursor Themes ==="

# Bibata variants
for variant in Modern-Classic Modern-Ice Modern-Amber Original-Classic; do
    f="${CACHE_DIR}/cursor-themes/Bibata-${variant}.tar.xz"
    if [ -f "$f" ]; then
        tar -xJf "$f" -C /usr/share/icons/ 2>/dev/null
        log "  Installed: Bibata ${variant}"
    fi
done

# macOS cursors
if [ -f "${CACHE_DIR}/cursor-themes/macOS-cursor.tar.xz" ]; then
    tar -xJf "${CACHE_DIR}/cursor-themes/macOS-cursor.tar.xz" -C /usr/share/icons/ 2>/dev/null
    log "  Installed: macOS cursors"
fi

# Phinger cursors
if [ -f "${CACHE_DIR}/cursor-themes/phinger-cursors.tar.bz2" ]; then
    tar -xjf "${CACHE_DIR}/cursor-themes/phinger-cursors.tar.bz2" -C /usr/share/icons/ 2>/dev/null
    log "  Installed: Phinger cursors"
fi

# WhiteSur cursors
# WhiteSur cursors — try install.sh, fall back to direct copy
extract_theme "${CACHE_DIR}/cursor-themes/WhiteSur-cursors.tar.gz" "WhiteSur Cursors" && {
    (cd "$THEME_SRC" && bash install.sh 2>/dev/null) || {
        mkdir -p /usr/share/icons/WhiteSur-cursors
        cp -r "$THEME_SRC"/dist/* /usr/share/icons/WhiteSur-cursors/ 2>/dev/null || true
    }
    log "  Installed: WhiteSur Cursors"
}

# ============================================================================
# Fix permissions
# ============================================================================

log ""
log "=== Fixing permissions ==="
chmod -R a+rX /usr/share/themes/ 2>/dev/null || true
chmod -R a+rX /usr/share/icons/ 2>/dev/null || true
chmod -R a+rX /usr/share/gnome-shell/extensions/ 2>/dev/null || true

# Rebuild icon caches
for theme_dir in /usr/share/icons/*/; do
    if [ -f "${theme_dir}index.theme" ]; then
        gtk-update-icon-cache -q "${theme_dir}" 2>/dev/null || true
    fi
done

# Recompile global schemas
glib-compile-schemas /usr/share/glib-2.0/schemas/ 2>/dev/null || true

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
Exec=/usr/bin/python3 /usr/share/intergen-welcome/intergen-welcome.py
Icon=preferences-system
X-GNOME-Autostart-Phase=Application
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
# Default profile applied per-user — create in /etc/skel so every new user gets it
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
        # To disable SSH access, comment out or remove this rule
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

# Create systemd service
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
log "  Themes, icons, cursors: installed from cache"
log "  Welcome greeter: autostart configured"
log "  Burn My Windows: TV effect default"
log "  Firewall: nftables enabled"
log "============================================"
