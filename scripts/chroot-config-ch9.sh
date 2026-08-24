#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# InterGenOS Chapter 9 — System Configuration
# LFS 13.0 Systemd
#
# Runs INSIDE the chroot (launched via chroot-enter.sh).
# Creates all system configuration files for Chapter 9.
#
# Usage:
#   sudo bash /mnt/intergenos/scripts/chroot-enter.sh \
#        /mnt/intergenos/scripts/chroot-config-ch9.sh

set -e
umask 022

IGOS_LOGS=/mnt/intergenos/build/logs
mkdir -p "$IGOS_LOGS"

LOGFILE="$IGOS_LOGS/ch9-config-$(date '+%Y%m%d-%H%M%S').log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOGFILE"
}

install_config() {
    local dest="$1"
    local desc="$2"
    log "  Installing $dest — $desc"
}

log "=========================================="
log "  InterGenOS Chapter 9: System Configuration"
log "=========================================="

# ============================================================================
# 9.2.1 — Network Interface Configuration (systemd-networkd, DHCP)
# ============================================================================

install_config "/etc/systemd/network/10-dhcp.network" "DHCP network config"
mkdir -p /etc/systemd/network
cat > /etc/systemd/network/10-dhcp.network << "EOF"
[Match]
Name=en*

[Network]
DHCP=ipv4

[DHCPv4]
UseDNS=true
UseDomains=true
EOF

# ============================================================================
# 9.2.2 — /etc/resolv.conf
# ============================================================================

# systemd-resolved creates /etc/resolv.conf as a symlink on boot.
# DNS servers are configured in the .network file above.
# No static resolv.conf needed — systemd-resolved handles it.
log "  /etc/resolv.conf — managed by systemd-resolved (no static file created)"

# ============================================================================
# 9.2.3 — /etc/hostname
# ============================================================================

install_config "/etc/hostname" "system hostname"
echo "intergenos" > /etc/hostname

# ============================================================================
# 9.2.4 — /etc/hosts
# ============================================================================

install_config "/etc/hosts" "static host lookups"
cat > /etc/hosts << "EOF"
# Begin /etc/hosts

127.0.0.1    localhost
127.0.1.1    intergenos.localdomain intergenos
::1          localhost ip6-localhost ip6-loopback
ff02::1      ip6-allnodes
ff02::2      ip6-allrouters

# End /etc/hosts
EOF

# ============================================================================
# 9.5 — System Clock
# ============================================================================

# Hardware clock is UTC (KVM default).
# systemd-timedated assumes UTC when /etc/adjtime is absent.
log "  /etc/adjtime — not created (systemd defaults to UTC)"

# ============================================================================
# 9.6 — Console Configuration
# ============================================================================

install_config "/etc/vconsole.conf" "console keymap and font"
cat > /etc/vconsole.conf << "EOF"
KEYMAP=us
FONT=Lat2-Terminus16
EOF

# ============================================================================
# 9.7 — System Locale
# ============================================================================

install_config "/etc/locale.conf" "system locale"
cat > /etc/locale.conf << "EOF"
LANG=en_US.UTF-8
EOF

# /etc/profile + /etc/bashrc + /etc/skel/.{bashrc,bash_profile} +
# /etc/profile.d/prompt.sh + /etc/bash.bashrc symlink + /root/.{bashrc,
# bash_profile}: cp -a from intergenos-base-files package files/ tree
# per plan v2 (2026-05-27, single source of truth). build_in_chroot has
# /mnt/intergenos mounted via self-contained-chroot pattern; package
# content reachable here.
BASEFILES_SRC=/mnt/intergenos/packages/core/intergenos-base-files/files
if [ ! -d "$BASEFILES_SRC" ]; then
    echo "FATAL: intergenos-base-files content tree missing at $BASEFILES_SRC" >&2
    exit 1
fi

# Copy a base-files config into place, then normalize ownership to root:root.
# cp -av preserves the repo source's build-user uid/gid (1000), which would ship
# these root-owned system files as user-owned in the squashfs. Fix at the source
# so build-squashfs.sh's ownership guard stays PASS (same uid-1000 leak class as
# chroot-build-bootloader.sh, found 2026-06-04).
cp_basefile() { cp -av "$1" "$2" && chown -h root:root "$2"; }

install_config "/etc/profile" "login shell locale setup (from intergenos-base-files)"
cp_basefile "$BASEFILES_SRC/etc/profile" /etc/profile

install_config "/etc/bashrc" "interactive non-login shell setup (from intergenos-base-files)"
cp_basefile "$BASEFILES_SRC/etc/bashrc" /etc/bashrc

# bash looks for /etc/bash.bashrc for non-login interactive shells
# (e.g. GNOME Terminal). Symlink so both names work.
ln -sf /etc/bashrc /etc/bash.bashrc

install_config "/etc/skel" "skeleton files (from intergenos-base-files)"
mkdir -p /etc/skel
cp_basefile "$BASEFILES_SRC/etc/skel/.bashrc"       /etc/skel/.bashrc
cp_basefile "$BASEFILES_SRC/etc/skel/.bash_profile" /etc/skel/.bash_profile

# Root shell configs
cp /etc/skel/.bashrc /root/.bashrc
cp /etc/skel/.bash_profile /root/.bash_profile
log "    /etc/bash.bashrc (symlink)"
log "    /etc/skel/.bashrc + .bash_profile"

install_config "/etc/profile.d/prompt.sh" "custom PS1 prompts (from intergenos-base-files)"
mkdir -p /etc/profile.d
cp_basefile "$BASEFILES_SRC/etc/profile.d/prompt.sh" /etc/profile.d/prompt.sh
chmod 0755 /etc/profile.d/prompt.sh

# ============================================================================
# 9.8 — /etc/inputrc
# ============================================================================

install_config "/etc/inputrc" "readline configuration (from intergenos-base-files)"
cp_basefile "$BASEFILES_SRC/etc/inputrc" /etc/inputrc

# ============================================================================
# 9.9 — /etc/shells
# ============================================================================

install_config "/etc/shells" "valid login shells (from intergenos-base-files)"
cp_basefile "$BASEFILES_SRC/etc/shells" /etc/shells

# ============================================================================
# 9.10 — Systemd Usage and Configuration
# ============================================================================

# 9.10.2 — Disable screen clearing at boot (from intergenos-base-files)
install_config "/etc/systemd/system/getty@tty1.service.d/noclear.conf" "disable boot screen clear (from intergenos-base-files)"
mkdir -p /etc/systemd/system/getty@tty1.service.d
cp_basefile "$BASEFILES_SRC/etc/systemd/system/getty@tty1.service.d/noclear.conf" /etc/systemd/system/getty@tty1.service.d/noclear.conf

# 9.10.3 — /tmp as tmpfs (keep systemd default — tmpfs is fine)
log "  /tmp — keeping systemd default (tmpfs)"

# 9.10.8 — Core dump limit (from intergenos-base-files)
install_config "/etc/systemd/coredump.conf.d/maxuse.conf" "core dump size limit (from intergenos-base-files)"
mkdir -p /etc/systemd/coredump.conf.d
cp_basefile "$BASEFILES_SRC/etc/systemd/coredump.conf.d/maxuse.conf" /etc/systemd/coredump.conf.d/maxuse.conf

# ============================================================================
# InterGenOS Branding — TTY Login Banner + MOTD + Identity files
# (all from intergenos-base-files)
# ============================================================================

install_config "/etc/issue" "TTY login banner (from intergenos-base-files)"
cp_basefile "$BASEFILES_SRC/etc/issue" /etc/issue

install_config "/etc/motd" "message of the day (from intergenos-base-files)"
cp_basefile "$BASEFILES_SRC/etc/motd" /etc/motd

install_config "/etc/os-release" "OS identification (from intergenos-base-files)"
cp_basefile "$BASEFILES_SRC/etc/os-release" /etc/os-release

# LSB expects /usr/lib/lsb/ directory — some third-party software checks for it
mkdir -pv /usr/lib/lsb

install_config "/etc/lsb-release" "LSB compatibility identification (from intergenos-base-files)"
cp_basefile "$BASEFILES_SRC/etc/lsb-release" /etc/lsb-release

install_config "/etc/igos-release" "InterGenOS version stamp (from intergenos-base-files)"
cp_basefile "$BASEFILES_SRC/etc/igos-release" /etc/igos-release

install_config "/usr/bin/lsb_release" "LSB release query command (from intergenos-base-files)"
cp_basefile "$BASEFILES_SRC/usr/bin/lsb_release" /usr/bin/lsb_release
chmod 755 /usr/bin/lsb_release

# ============================================================================
# 9.X — Systemd preset policy: default-disable + explicit-enable
# ============================================================================

# Background: systemd's `systemctl preset-all` (run by core/systemd/build.sh
# post_install) enables every service with `WantedBy=multi-user.target` in
# its [Install] section UNLESS an earlier-sorted preset file explicitly
# disables it. The implicit default is "enable on no match." Without a
# catch-all `disable *`, the live ISO ends up with 40+ auto-starting
# services including httpd, nginx, mariadb, postgres, memcached, etcd,
# valkey, influxdb, transmission, caddy, haproxy — every "extra" tier
# server flips on automatically.
#
# That violates security-only alignment (every running service must be
# deliberately chosen) and the May-15 smoke test
# surfaced the impact on the live ISO. Root-caused 2026-05-16: missing
# default-disable catch-all preset.
#
# Fix: ship a 99-intergenos-default-disable.preset with `disable *` plus
# an 80-intergenos-enable.preset listing services we deliberately want
# on by default. Then re-run preset-all to apply.
#
# What stays enabled after this preset policy lands:
#   - gdm.service              (90-gdm.preset)
#   - nftables.service         (90-nftables.preset)
#   - NetworkManager.service   (80-intergenos-enable.preset, this file)
#   - apparmor.service         (80-intergenos-enable.preset, this file)
#   - systemd-oomd.service     (80-intergenos-enable.preset, this file)
#   - systemd-*                (upstream 90-systemd.preset for core systemd
#                               infrastructure: remote-*.target,
#                               systemd-homed.service, systemd-networkd.service,
#                               etc.)
log "--- Installing InterGenOS systemd preset policy ---"

# Preset policy files from intergenos-base-files (single source of truth).
mkdir -p /usr/lib/systemd/system-preset
cp_basefile "$BASEFILES_SRC/usr/lib/systemd/system-preset/80-intergenos-enable.preset" /usr/lib/systemd/system-preset/80-intergenos-enable.preset
chmod 644 /usr/lib/systemd/system-preset/80-intergenos-enable.preset

cp_basefile "$BASEFILES_SRC/usr/lib/systemd/system-preset/99-intergenos-default-disable.preset" /usr/lib/systemd/system-preset/99-intergenos-default-disable.preset
chmod 644 /usr/lib/systemd/system-preset/99-intergenos-default-disable.preset

log "  /usr/lib/systemd/system-preset/80-intergenos-enable.preset"
log "  /usr/lib/systemd/system-preset/99-intergenos-default-disable.preset"

# Apply the new preset policy. preset-all walks all units and applies
# the policy from the .preset files. Symlinks for now-disabled services
# get removed; explicit-enabled services land in *.target.wants/.
log "--- Re-running systemctl preset-all under new policy ---"
systemctl preset-all 2>&1 | sed 's/^/  /' || true
log "  preset-all applied; multi-user.target.wants/ contents:"
ls /etc/systemd/system/multi-user.target.wants/ 2>/dev/null | sed 's/^/    /'

# ============================================================================
# 9.X — GDM Wayland-only policy (D-014 closure 2026-05-22)
# ============================================================================
#
# Wayland-only ratified 2026-05-18 (operator chat + VISION:212). InterGenOS
# explicitly enables Wayland and does not fall back to X11 — provides a
# consistent first-login experience across hardware regardless of GDM's
# GPU-detection auto-fallback heuristic. Closes audit row D-014.
#
# Shipped at /etc/gdm/custom.conf in the chroot so the squashfs payload
# carries it into installed systems automatically. Live ISO overlay
# additionally writes its own custom.conf with AutomaticLoginEnable=true
# (installer/init/init.sh:286-298); this default is for installed systems
# (no autologin) and tracks the Wayland setting in both contexts.
log "--- Installing GDM Wayland-only policy (D-014) ---"

mkdir -p /etc/gdm
cp_basefile "$BASEFILES_SRC/etc/gdm/custom.conf" /etc/gdm/custom.conf

log "  /etc/gdm/custom.conf — [daemon] WaylandEnable=true (from intergenos-base-files)"

# ============================================================================
# 9.X — dbus capability override (close setgroups EPERM)
# ============================================================================
#
# Upstream dbus 1.16.2's shipped dbus.service uses:
#   User=messagebus
#   Group=messagebus
#   AmbientCapabilities=CAP_AUDIT_WRITE
#
# systemd switches UID to messagebus before invoking dbus-daemon. The
# daemon then tries `setgroups(0, NULL)` to drop supplementary groups as
# a self-hardening step. Without CAP_SETGID in the inherited cap set,
# setgroups returns EPERM, which dbus logs as:
#   dbus-daemon[404]: Failed to drop supplementary groups: Operation
#   not permitted
# Surfaced in cycle-3 smoke test serial log. Non-fatal (dbus continues)
# but spurious — decided 2026-05-16: no half-assing, no
# "non-blocking" framing; fix the warning.
#
# Fix: drop-in service override at
# /etc/systemd/system/dbus.service.d/intergenos-capabilities.conf adding
# CAP_SETGID to AmbientCapabilities. Lives in /etc rather than /usr/lib
# so it survives a dbus package upgrade (overrides under /etc trump
# package-shipped files).
log "--- Installing dbus.service capability override (CAP_SETGID, from intergenos-base-files) ---"
mkdir -p /etc/systemd/system/dbus.service.d
cp_basefile "$BASEFILES_SRC/etc/systemd/system/dbus.service.d/intergenos-capabilities.conf" /etc/systemd/system/dbus.service.d/intergenos-capabilities.conf
chmod 644 /etc/systemd/system/dbus.service.d/intergenos-capabilities.conf
log "  /etc/systemd/system/dbus.service.d/intergenos-capabilities.conf"

# ============================================================================
# 9.X — TSS2 log-noise suppression for TPM-init services
# ============================================================================
#
# systemd-tpm2-setup and systemd-pcrextend re-initialize NV PCR slots at
# each boot. When the slots are already present (every boot after the
# first), the TPM returns TPM2_RC_NV_DEFINED (0x14c — "NV Index or
# persistent object already defined"). systemd handles this gracefully
# ("1 NvPCRs were already initialized") and the unit finishes [OK].
#
# But the TSS2 library underneath doesn't know the caller treats this
# response as success — it logs the response as:
#   WARNING:esys:... Esys_NV_DefineSpace_Finish() Received TPM Error
#   ERROR:esys:... Esys_NV_DefineSpace() Esys Finish ErrorCode (0x0000014c)
# Spurious noise in every boot's journal. Surfaced in cycle-3 smoke test.
# Decided 2026-05-16: no "benign" framing; fix the noise.
#
# Fix: drop-ins set TSS2_LOG=all+critical for the two services that hit
# this path. Suppresses WARNING + ERROR for known-handled cases, keeps
# CRITICAL for genuinely unrecoverable TPM faults.
log "--- Installing TSS2 log-level overrides for tpm2-init services (from intergenos-base-files) ---"
for svc in systemd-tpm2-setup systemd-pcrextend; do
    mkdir -p /etc/systemd/system/${svc}.service.d
    cp_basefile "$BASEFILES_SRC/etc/systemd/system/${svc}.service.d/intergenos-tss2-loglevel.conf" /etc/systemd/system/${svc}.service.d/intergenos-tss2-loglevel.conf
    chmod 644 /etc/systemd/system/${svc}.service.d/intergenos-tss2-loglevel.conf
    log "  /etc/systemd/system/${svc}.service.d/intergenos-tss2-loglevel.conf"
done

# ============================================================================
# 9.X — Register all installed packages with pkm SQLite DB
# ============================================================================
#
# Root cause traced 2026-05-16: the bash build pipeline
# (scripts/pkg-functions.sh:pkg_install) writes the text manifest to
# /var/lib/igos/packages/<name>-<version> and the archive to
# /var/lib/igos/archives/<name>-<version>.igos.tar.gz, but does NOT
# write to the pkm SQLite database. Only the Python orchestrator
# (igos-build/tracker.py:pkg_register_pkm_db) writes SQLite at the
# gate-3 post-deploy step. Net effect: every package built by the
# bash chroot-build-*.sh scripts (tier:core, tier:base, plus some
# tier:extra) is "phantom-installed" — files on disk, manifest on disk,
# archive on disk, but pkm DB does not know about it. Symptoms:
#   - `pkm provides <file>` returns "No package owns" for files in
#     the phantom packages, even though the files exist
#   - `pkm info <name>` says "not installed"
#   - `pkm files <name>` returns empty
# Inflicted 236 of our 765 packages pre-fix. Discovered when
# `/usr/bin/ping` triaged as an "orphan binary" (inetutils owns it but
# pkm didn't know inetutils was installed).
#
# Fix: `pkm import` scans /var/lib/igos/packages/ manifests and
# creates DB entries for any package not yet registered. Idempotent —
# already-tracked packages are skipped. Runs once at config-phase end
# to reconcile the DB with the on-disk state.
#
# The proper fix in pkg-functions.sh:pkg_install would be to call into
# pkm's add_installed() after pkg_deploy succeeds. Tracked as followup
# (parity with tracker.py:pkg_register_pkm_db). For now `pkm import`
# closes the loop reliably.
log "--- Reconciling pkm SQLite DB with on-disk package manifests ---"
# Single-flight asserted the same way pkg_install does it, so the build's premise
# that exactly one pkm runs at a time is checked here too rather than assumed.
for _sf_lib in /mnt/intergenos/scripts/lib/pkm-single-flight.sh \
               "$(dirname "${BASH_SOURCE[0]}")/lib/pkm-single-flight.sh"; do
    if [ -f "$_sf_lib" ]; then . "$_sf_lib"; break; fi
done
unset _sf_lib
if declare -F pkg_run_pkm_single_flight >/dev/null 2>&1; then
    pkg_run_pkm_single_flight import
else
    # No bare fallback here on purpose. Running pkm without the guard is exactly
    # the unserialized write the guard exists to prevent, and a missing library
    # in a tree that ships it means a broken checkout, not a normal condition.
    log "error: scripts/lib/pkm-single-flight.sh was not found, so this phase cannot"
    log "error: assert that only one pkm runs at a time. Refusing to invoke pkm"
    log "error: unguarded rather than writing the package database unserialized."
    exit 1
fi

# ============================================================================
# 9.X — Dirty Frag / Fragnesia mitigation modprobe blacklist (2026-05-18)
# ============================================================================
#
# Defense-in-depth alongside the in-tree kernel patches at
# packages/core/linux-kernel/patches/CVE-2026-{43284,43500,46300}-*.patch.
#
# The kernel patches close the three known CVEs (Dirty Frag xfrm-ESP,
# Dirty Frag rxrpc, Fragnesia shared-frag-marker). Fragnesia is itself
# evidence that the original Dirty Frag patch did not cover the related
# code path — exactly the failure-mode defense-in-depth pays for. If a
# "Fragnesia-2" CVE emerges in the same code area before we ship a
# fresh kernel, this blacklist blocks the modules from loading entirely.
#
# Filter applied: user control + security-only alignment.
# - User control preserved — blacklist lives in /etc/modprobe.d/, plainly
#   visible, user-editable. Any user who needs IPsec ESP or OpenAFS
#   (rxrpc) can rm the file or `modprobe esp4` manually. Documentation
#   at docs/security/advisories/dirty-frag-fragnesia.md covers the
#   opt-out path.
# - Modern consumer VPNs (WireGuard, OpenVPN) do not use esp4/esp6;
#   rxrpc is OpenAFS-only. Disabling these by default breaks essentially
#   no v1.0 user workflow.
log "--- Installing Dirty Frag / Fragnesia mitigation modprobe blacklist (from intergenos-base-files) ---"
mkdir -p /etc/modprobe.d
cp_basefile "$BASEFILES_SRC/etc/modprobe.d/igos-dirty-frag-mitigation.conf" /etc/modprobe.d/igos-dirty-frag-mitigation.conf
chmod 644 /etc/modprobe.d/igos-dirty-frag-mitigation.conf
log "  /etc/modprobe.d/igos-dirty-frag-mitigation.conf"

log ""
log "=========================================="
log "  Chapter 9 Configuration Complete"
log "=========================================="
log ""
log "  Files created:"
log "    /etc/systemd/network/10-dhcp.network"
log "    /etc/hostname"
log "    /etc/hosts"
log "    /etc/vconsole.conf"
log "    /etc/locale.conf"
log "    /etc/profile"
log "    /etc/bashrc"
log "    /etc/profile.d/prompt.sh"
log "    /etc/inputrc"
log "    /etc/shells"
log "    /etc/issue"
log "    /etc/motd"
log "    /etc/os-release"
log "    /etc/lsb-release"
log "    /etc/igos-release"
log "    /usr/bin/lsb_release"
log "    /etc/systemd/system/getty@tty1.service.d/noclear.conf"
log "    /etc/systemd/coredump.conf.d/maxuse.conf"
log ""
log "  Systemd preset policy:"
log "    80-intergenos-enable.preset       — explicit-enable list"
log "    99-intergenos-default-disable.preset — disable * catch-all"
log "    preset-all applied at chapter-9 config time"
log ""
log "  Not created (by design):"
log "    /etc/resolv.conf — managed by systemd-resolved"
log "    /etc/adjtime — absent = UTC (systemd default)"
log ""
log "  Log: $LOGFILE"
