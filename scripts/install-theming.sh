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

CACHE_DIR="/mnt/intergenos/build/theming"
WORK_DIR="/tmp/theming-work"

log() { echo "[THEMING] $*"; }
warn() { echo "[THEMING] WARNING: $*" >&2; }

if [ ! -d "$CACHE_DIR" ]; then
    warn "Cache directory $CACHE_DIR not found."
    warn "Run scripts/download-theming.sh on the host first."
    exit 1
fi

mkdir -p "$WORK_DIR"

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
# Helper: install a theme from tarball using its install.sh
# ============================================================================

install_theme_tarball() {
    local tarball="$1"
    local name="$2"
    local install_cmd="$3"

    [ -f "$tarball" ] || { warn "Not found: $tarball"; return 1; }

    local src="${WORK_DIR}/$(basename "$tarball" .tar.gz)"
    rm -rf "$src"
    mkdir -p "$src"

    tar -xf "$tarball" -C "$src" --strip-components=1 2>/dev/null || {
        warn "Failed to extract $name"
        return 1
    }

    (cd "$src" && eval "$install_cmd") 2>/dev/null || {
        warn "Failed to install $name"
        return 1
    }

    log "  Installed: $name"
}

# ============================================================================
# GTK / Shell Themes (10)
# ============================================================================

log ""
log "=== Installing GTK / Shell Themes ==="

install_theme_tarball "${CACHE_DIR}/gtk-themes/Orchis-theme.tar.gz" "Orchis" \
    "bash install.sh -d /usr/share/themes"

install_theme_tarball "${CACHE_DIR}/gtk-themes/WhiteSur-gtk-theme.tar.gz" "WhiteSur GTK" \
    "bash install.sh -d /usr/share/themes"

install_theme_tarball "${CACHE_DIR}/gtk-themes/Nordic.tar.gz" "Nordic" \
    "mkdir -p /usr/share/themes/Nordic && cp -r . /usr/share/themes/Nordic/"

install_theme_tarball "${CACHE_DIR}/gtk-themes/Sweet.tar.gz" "Sweet" \
    "mkdir -p /usr/share/themes/Sweet && cp -r . /usr/share/themes/Sweet/"

install_theme_tarball "${CACHE_DIR}/gtk-themes/Graphite-gtk-theme.tar.gz" "Graphite" \
    "bash install.sh -d /usr/share/themes"

install_theme_tarball "${CACHE_DIR}/gtk-themes/Colloid-gtk-theme.tar.gz" "Colloid GTK" \
    "bash install.sh -d /usr/share/themes"

install_theme_tarball "${CACHE_DIR}/gtk-themes/Fluent-gtk-theme.tar.gz" "Fluent GTK" \
    "bash install.sh -d /usr/share/themes"

install_theme_tarball "${CACHE_DIR}/gtk-themes/Dracula.tar.gz" "Dracula" \
    "mkdir -p /usr/share/themes/Dracula && cp -r . /usr/share/themes/Dracula/"

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

install_theme_tarball "${CACHE_DIR}/icon-themes/papirus-icon-theme.tar.gz" "Papirus" \
    "bash install.sh -d /usr/share/icons || { cp -r Papirus* /usr/share/icons/; }"

install_theme_tarball "${CACHE_DIR}/icon-themes/WhiteSur-icon-theme.tar.gz" "WhiteSur Icons" \
    "bash install.sh -d /usr/share/icons"

install_theme_tarball "${CACHE_DIR}/icon-themes/Tela-icon-theme.tar.gz" "Tela Icons" \
    "bash install.sh -d /usr/share/icons"

install_theme_tarball "${CACHE_DIR}/icon-themes/Colloid-icon-theme.tar.gz" "Colloid Icons" \
    "bash install.sh -d /usr/share/icons"

install_theme_tarball "${CACHE_DIR}/icon-themes/Qogir-icon-theme.tar.gz" "Qogir Icons" \
    "bash install.sh -d /usr/share/icons"

install_theme_tarball "${CACHE_DIR}/icon-themes/kora.tar.gz" "Kora Icons" \
    "cp -r kora kora-pgrey /usr/share/icons/ 2>/dev/null || true"

install_theme_tarball "${CACHE_DIR}/icon-themes/Fluent-icon-theme.tar.gz" "Fluent Icons" \
    "bash install.sh -d /usr/share/icons"

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
install_theme_tarball "${CACHE_DIR}/cursor-themes/WhiteSur-cursors.tar.gz" "WhiteSur Cursors" \
    "bash install.sh 2>/dev/null || { mkdir -p /usr/share/icons/WhiteSur-cursors && cp -r dist/* /usr/share/icons/WhiteSur-cursors/; }"

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

        # Established/related connections
        ct state established,related accept

        # ICMP (ping)
        ip protocol icmp accept
        ip6 nexthdr ipv6-icmp accept

        # SSH
        tcp dport 22 accept

        # mDNS (Avahi)
        udp dport 5353 accept

        # DHCP client
        udp sport 67 udp dport 68 accept

        # Log and drop everything else
        log prefix "nftables-drop: " counter drop
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
