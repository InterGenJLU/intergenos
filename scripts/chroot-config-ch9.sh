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

IGOS_LOGS=/var/log/igos-build
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

# End /etc/bashrc
EOF

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
# Summary
# ============================================================================

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
log "    /etc/os-release"
log "    /etc/lsb-release"
log "    /etc/igos-release"
log "    /usr/bin/lsb_release"
log "    /etc/systemd/system/getty@tty1.service.d/noclear.conf"
log "    /etc/systemd/coredump.conf.d/maxuse.conf"
log ""
log "  Not created (by design):"
log "    /etc/resolv.conf — managed by systemd-resolved"
log "    /etc/adjtime — absent = UTC (systemd default)"
log ""
log "  Log: $LOGFILE"
