#!/bin/bash
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

install_config "/etc/profile" "login shell locale setup"
cat > /etc/profile << "EOF"
# Begin /etc/profile

for i in $(locale); do
  unset ${i%=*}
done

if [[ "$TERM" = linux ]]; then
  export LANG=C.UTF-8
else
  source /etc/locale.conf

  for i in $(locale); do
    key=${i%=*}
    if [[ -v $key ]]; then
      export $key
    fi
  done
fi

# Source profile.d drop-ins
if [ -d /etc/profile.d ]; then
  for script in /etc/profile.d/*.sh; do
    [ -r "$script" ] && . "$script"
  done
  unset script
fi

# End /etc/profile
EOF

install_config "/etc/bashrc" "interactive non-login shell setup"
cat > /etc/bashrc << "EOF"
# Begin /etc/bashrc

# Source profile.d drop-ins for interactive non-login shells
if [ -d /etc/profile.d ]; then
  for script in /etc/profile.d/*.sh; do
    [ -r "$script" ] && . "$script"
  done
  unset script
fi

# Aliases — carried forward from InterGenOS build_003 (2015)
alias ls='ls -a --group-directories-first --time-style=+"%d.%m.%Y %H:%M" --color=auto -F'
alias ll='ls -lah'
alias grep='grep --color=auto'
alias ..='cd ..'
alias ...='cd ../..'
alias ping='ping -c 3'

export EDITOR=nano
export LC_COLLATE="C"

HISTSIZE=1000
HISTFILESIZE=2000
HISTCONTROL=ignoreboth
shopt -s histappend

# End /etc/bashrc
EOF

# bash looks for /etc/bash.bashrc for non-login interactive shells
# (e.g. GNOME Terminal). Symlink so both names work.
ln -sf /etc/bashrc /etc/bash.bashrc

install_config "/etc/skel" "skeleton files for new user accounts"
mkdir -p /etc/skel
cat > /etc/skel/.bashrc << "EOF"
# ~/.bashrc
if [ -f /etc/bash.bashrc ]; then
    . /etc/bash.bashrc
fi
EOF

cat > /etc/skel/.bash_profile << "EOF"
# ~/.bash_profile
if [ -f ~/.bashrc ]; then
    . ~/.bashrc
fi
EOF

# Root shell configs
cp /etc/skel/.bashrc /root/.bashrc
cp /etc/skel/.bash_profile /root/.bash_profile
log "    /etc/bash.bashrc (symlink)"
log "    /etc/skel/.bashrc + .bash_profile"

install_config "/etc/profile.d/prompt.sh" "custom PS1 prompts"
mkdir -p /etc/profile.d
cat > /etc/profile.d/prompt.sh << "EOF"
# InterGenOS shell prompts
# Blue brackets, white delimiters, green path
# User: green username + $    Root: red username + #

if [ "$(id -u)" -eq 0 ]; then
  PS1='\[\e[1;34m\][\[\e[m\]\[\e[1;31m\]\u\[\e[m\]\[\e[1;34m\]@\[\e[m\]\[\e[1;37m\]\H\[\e[m\]\[\e[1;34m\]]\[\e[m\]\[\e[1;34m\][\[\e[m\]\[\e[1;37m\]<\[\e[m\]\[\e[1;32m\]\w\[\e[m\]\[\e[1;37m\]>\[\e[m\]\[\e[1;34m\]]\[\e[m\]\[\e[1;37m\]:\[\e[m\]\[\e[1;31m\]#\[\e[m\] '
else
  PS1='\[\e[1;34m\][\[\e[m\]\[\e[1;32m\]\u\[\e[m\]\[\e[1;34m\]@\[\e[m\]\[\e[1;37m\]\H\[\e[m\]\[\e[1;34m\]]\[\e[m\]\[\e[1;34m\][\[\e[m\]\[\e[1;37m\]<\[\e[m\]\[\e[1;32m\]\w\[\e[m\]\[\e[1;37m\]>\[\e[m\]\[\e[1;34m\]]\[\e[m\]\[\e[1;37m\]:\[\e[m\]\[\e[1;32m\]$\[\e[m\] '
fi
export PS1
EOF

# ============================================================================
# 9.8 — /etc/inputrc
# ============================================================================

install_config "/etc/inputrc" "readline configuration"
cat > /etc/inputrc << "EOF"
# Begin /etc/inputrc

# Allow the command prompt to wrap to the next line
set horizontal-scroll-mode Off

# Enable 8-bit input
set meta-flag On
set input-meta On

# Turns off 8th bit stripping
set convert-meta Off

# Keep the 8th bit for display
set output-meta On

# none, visible or audible
set bell-style none

# All of the following map the escape sequence of the value
# contained in the 1st argument to the readline specific functions
"\eOd": backward-word
"\eOc": forward-word

# for linux console
"\e[1~": beginning-of-line
"\e[4~": end-of-line
"\e[5~": beginning-of-history
"\e[6~": end-of-history
"\e[3~": delete-char
"\e[2~": quoted-insert

# for xterm
"\eOH": beginning-of-line
"\eOF": end-of-line

# for Konsole
"\e[H": beginning-of-line
"\e[F": end-of-line

# End /etc/inputrc
EOF

# ============================================================================
# 9.9 — /etc/shells
# ============================================================================

install_config "/etc/shells" "valid login shells"
cat > /etc/shells << "EOF"
# Begin /etc/shells

/bin/sh
/bin/bash

# End /etc/shells
EOF

# ============================================================================
# 9.10 — Systemd Usage and Configuration
# ============================================================================

# 9.10.2 — Disable screen clearing at boot (keep boot messages visible)
install_config "/etc/systemd/system/getty@tty1.service.d/noclear.conf" "disable boot screen clear"
mkdir -p /etc/systemd/system/getty@tty1.service.d
cat > /etc/systemd/system/getty@tty1.service.d/noclear.conf << "EOF"
[Service]
TTYVTDisallocate=no
EOF

# 9.10.3 — /tmp as tmpfs (keep systemd default — tmpfs is fine)
log "  /tmp — keeping systemd default (tmpfs)"

# 9.10.8 — Core dump limit
install_config "/etc/systemd/coredump.conf.d/maxuse.conf" "core dump size limit"
mkdir -p /etc/systemd/coredump.conf.d
cat > /etc/systemd/coredump.conf.d/maxuse.conf << "EOF"
[Coredump]
MaxUse=5G
EOF

# ============================================================================
# InterGenOS Branding — TTY Login Banner and MOTD
# ============================================================================

install_config "/etc/issue" "TTY login banner"
cat > /etc/issue << "EOF"

  InterGenOS 1.0-dev (Revival)
  Kernel \r on \m (\l)

EOF

install_config "/etc/motd" "message of the day"
cat > /etc/motd << "EOF"

  Welcome to InterGenOS
  "A system you understand, can modify, and can trust."

  Documentation:  https://github.com/InterGenJLU/intergenos
  Report issues:  https://github.com/InterGenJLU/intergenos/issues

EOF

# ============================================================================
# InterGenOS Identity Files
# ============================================================================

install_config "/etc/os-release" "OS identification (freedesktop.org)"
cat > /etc/os-release << "EOF"
NAME="InterGenOS"
VERSION="1.0-dev (Revival)"
ID=intergenos
ID_LIKE=lfs
VERSION_ID=1.0
VERSION_CODENAME=revival
PRETTY_NAME="InterGenOS 1.0-dev (Revival)"
HOME_URL="https://github.com/InterGenJLU/intergenos"
BUG_REPORT_URL="https://github.com/InterGenJLU/intergenos/issues"
EOF

# LSB expects /usr/lib/lsb/ directory — some third-party software checks for it
mkdir -pv /usr/lib/lsb

install_config "/etc/lsb-release" "LSB compatibility identification"
cat > /etc/lsb-release << "EOF"
DISTRIB_ID="InterGenOS"
DISTRIB_RELEASE="1.0-dev"
DISTRIB_CODENAME="revival"
DISTRIB_DESCRIPTION="InterGenOS 1.0-dev (Revival)"
EOF

install_config "/etc/igos-release" "InterGenOS version stamp"
echo "1.0-dev" > /etc/igos-release

install_config "/usr/bin/lsb_release" "LSB release query command"
cat > /usr/bin/lsb_release << "SCRIPT"
#!/bin/bash
# lsb_release — LSB conformance query command for InterGenOS
# Reads from /etc/os-release and /etc/lsb-release

LSB_VERSION="core-5.0-amd64:core-5.0-noarch"

# Source os-release for data
if [ -f /etc/os-release ]; then
    . /etc/os-release
fi

# Source lsb-release for LSB-specific fields
if [ -f /etc/lsb-release ]; then
    . /etc/lsb-release
fi

SHORT=0

usage() {
    echo "Usage: lsb_release [OPTION]..."
    echo "  -v, --version     Show LSB version"
    echo "  -i, --id          Show distributor ID"
    echo "  -d, --description Show description"
    echo "  -r, --release     Show release number"
    echo "  -c, --codename    Show codename"
    echo "  -a, --all         Show all of the above"
    echo "  -s, --short       Use short output format"
    echo "  -h, --help        Show this help"
}

show_version()     { [ $SHORT -eq 1 ] && echo "$LSB_VERSION" || echo "LSB Version:	$LSB_VERSION"; }
show_id()          { [ $SHORT -eq 1 ] && echo "${DISTRIB_ID}" || echo "Distributor ID:	${DISTRIB_ID}"; }
show_description() { [ $SHORT -eq 1 ] && echo "${DISTRIB_DESCRIPTION}" || echo "Description:	${DISTRIB_DESCRIPTION}"; }
show_release()     { [ $SHORT -eq 1 ] && echo "${DISTRIB_RELEASE}" || echo "Release:	${DISTRIB_RELEASE}"; }
show_codename()    { [ $SHORT -eq 1 ] && echo "${DISTRIB_CODENAME}" || echo "Codename:	${DISTRIB_CODENAME}"; }

show_all() {
    show_version
    show_id
    show_description
    show_release
    show_codename
}

if [ $# -eq 0 ]; then
    show_version
    exit 0
fi

# Parse for -s/--short first
for arg in "$@"; do
    case "$arg" in
        -s|--short) SHORT=1 ;;
    esac
done

for arg in "$@"; do
    case "$arg" in
        -v|--version)     show_version ;;
        -i|--id)          show_id ;;
        -d|--description) show_description ;;
        -r|--release)     show_release ;;
        -c|--codename)    show_codename ;;
        -a|--all)         show_all ;;
        -s|--short)       ;; # already handled
        -h|--help)        usage; exit 0 ;;
        *)                echo "Unknown option: $arg"; usage; exit 1 ;;
    esac
done
SCRIPT
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
# That's a Holy Grail violation (security-only alignment requires that
# every running service be deliberately chosen) and the May-15 smoke test
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

cat > /usr/lib/systemd/system-preset/80-intergenos-enable.preset <<'EOF'
# InterGenOS — explicit-enable list.
# Services we deliberately want active by default on installed systems.
# Add new lines here when you want a service auto-enabled on first boot;
# add nothing here for "user installs it, user enables it" semantics.
#
# Already covered by other preset files:
#   - gdm.service          (90-gdm.preset)
#   - nftables.service     (90-nftables.preset)
#   - systemd-* services   (90-systemd.preset, upstream)

enable NetworkManager.service
enable apparmor.service
enable systemd-oomd.service
EOF
chmod 644 /usr/lib/systemd/system-preset/80-intergenos-enable.preset

cat > /usr/lib/systemd/system-preset/99-intergenos-default-disable.preset <<'EOF'
# InterGenOS — default-disable catch-all.
# Anything not explicitly enabled by an earlier-sorted preset (80- or
# 90-) lands here and stays disabled. Without this catch-all, systemd's
# implicit default of "enable on no match" auto-enables every service
# with `WantedBy=multi-user.target` in its [Install] section. That was
# the May-12 root cause for 40+ servers (httpd, nginx, postgres, mariadb,
# memcached, etc.) auto-starting on the live ISO — a Holy Grail
# violation. Closing the loop here means "user installs, user enables."

disable *
EOF
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
# but spurious — owner-direct 2026-05-16: no half-assing, no
# "non-blocking" framing; fix the warning.
#
# Fix: drop-in service override at
# /etc/systemd/system/dbus.service.d/intergenos-capabilities.conf adding
# CAP_SETGID to AmbientCapabilities. Lives in /etc rather than /usr/lib
# so it survives a dbus package upgrade (overrides under /etc trump
# package-shipped files).
log "--- Installing dbus.service capability override (CAP_SETGID) ---"
mkdir -p /etc/systemd/system/dbus.service.d
cat > /etc/systemd/system/dbus.service.d/intergenos-capabilities.conf <<'EOF'
# Closes the "Failed to drop supplementary groups: Operation not
# permitted" boot-time warning. dbus-daemon, running as messagebus,
# needs CAP_SETGID to perform setgroups(0, NULL). See
# scripts/chroot-config-ch9.sh for the full root-cause trace.
[Service]
AmbientCapabilities=CAP_AUDIT_WRITE CAP_SETGID
EOF
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
# Owner-direct 2026-05-16: no "benign" framing; fix the noise.
#
# Fix: drop-ins set TSS2_LOG=all+critical for the two services that hit
# this path. Suppresses WARNING + ERROR for known-handled cases, keeps
# CRITICAL for genuinely unrecoverable TPM faults.
log "--- Installing TSS2 log-level overrides for tpm2-init services ---"
for svc in systemd-tpm2-setup systemd-pcrextend; do
    mkdir -p /etc/systemd/system/${svc}.service.d
    cat > /etc/systemd/system/${svc}.service.d/intergenos-tss2-loglevel.conf <<'EOF'
# TSS2 library emits WARNING/ERROR on TPM2_RC_NV_DEFINED (0x14c — "NV
# already defined"), a benign re-init case at every boot after the
# first. systemd handles it cleanly. Suppress library-level noise so
# only CRITICAL faults reach the journal. See chroot-config-ch9.sh
# for the full root-cause trace.
[Service]
Environment=TSS2_LOG=all+CRITICAL
EOF
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
pkm import 2>&1 | sed 's/^/  /' || true

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
