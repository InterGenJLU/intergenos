#!/bin/bash
# InterGenOS Chroot Entry — "Drop In"
# LFS 13.0 Section 7.4
#
# Enters the chroot environment with a clean, controlled environment.
# Can be used interactively or to run a script inside the chroot.
#
# Usage (as root on build VM):
#   sudo bash /mnt/intergenos/scripts/chroot-enter.sh                    # interactive
#   sudo bash /mnt/intergenos/scripts/chroot-enter.sh /path/to/script    # run script inside
#
# The script path must be accessible from INSIDE the chroot.
# For our build scripts, they're on the virtiofs mount, so use:
#   sudo bash /mnt/intergenos/scripts/chroot-enter.sh /mnt/intergenos/scripts/chroot-build.sh

IGOS=/mnt/igos

# Verify we're root
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root."
    exit 1
fi

# Verify virtual filesystems are mounted
if ! mountpoint -q $IGOS/dev; then
    echo "ERROR: Virtual filesystems not mounted. Run chroot-setup.sh first."
    exit 1
fi

# Determine what to run inside the chroot
if [ -n "$1" ]; then
    # Run a specific script inside the chroot (pass all arguments through)
    CHROOT_CMD="/bin/bash $*"
else
    # Interactive shell
    CHROOT_CMD="/bin/bash --login"
fi

# Number of cores for parallel builds
JOBS=$(nproc)

# Capture host timezone for use inside chroot.
# The chroot has no zoneinfo database until glibc Ch.8, so Olson names
# like "America/Chicago" resolve to UTC. Instead, compute a POSIX TZ
# string (e.g., "CST6CDT") on the host where zoneinfo exists, and pass
# that in. POSIX TZ strings work without any zoneinfo files.
HOST_TZ_OLSON="$(cat /etc/timezone 2>/dev/null || echo UTC)"
HOST_TZ_POSIX="$(TZ="$HOST_TZ_OLSON" date +%Z 2>/dev/null || echo UTC)"
# Build a POSIX offset string: e.g., CST6CDT or EST5EDT
# date +%z gives offset like -0500, convert to hours
HOST_OFFSET="$(TZ="$HOST_TZ_OLSON" date +%z 2>/dev/null || echo +0000)"
OFFSET_SIGN="${HOST_OFFSET:0:1}"
OFFSET_HH="${HOST_OFFSET:1:2}"
# POSIX TZ offsets are inverted: UTC-5 is expressed as XXX5
# Strip leading zero for POSIX format
OFFSET_NUM=$((10#$OFFSET_HH))
if [ "$OFFSET_SIGN" = "-" ]; then
    POSIX_OFFSET="$OFFSET_NUM"
else
    POSIX_OFFSET="-$OFFSET_NUM"
fi
# Use the abbreviated zone name with the offset
HOST_TZ="${HOST_TZ_POSIX}${POSIX_OFFSET}"

# Enter the chroot with a clean environment
# env -i clears ALL host environment variables
# Only HOME, TERM, TZ, PS1, PATH, MAKEFLAGS, TESTSUITEFLAGS survive
chroot "$IGOS" /usr/bin/env -i               \
    HOME=/root                               \
    TERM="$TERM"                             \
    TZ="$HOST_TZ"                            \
    PS1='\[\e[1;34m\][\[\e[m\]\[\e[1;31m\](igos-chroot)\[\e[m\]\[\e[1;34m\]]\[\e[m\]\[\e[1;34m\][\[\e[m\]\[\e[1;37m\]<\[\e[m\]\[\e[1;32m\]\w\[\e[m\]\[\e[1;37m\]>\[\e[m\]\[\e[1;34m\]]\[\e[m\]\[\e[1;37m\]:\[\e[m\]\[\e[1;31m\]#\[\e[m\] ' \
    PATH=/usr/bin:/usr/sbin:/bin:/sbin        \
    MAKEFLAGS="-j${JOBS}"                    \
    TESTSUITEFLAGS="-j${JOBS}"               \
    IGOS_JOBS="${JOBS}"                       \
    IGOS_SOURCES=/sources                    \
    IGOS_PATCHES=/sources                    \
    IGOS_LOGS=/var/log/igos-build            \
    PKG_VERSION=""                           \
    IGOS_START_AT="${IGOS_START_AT:-}"       \
    $CHROOT_CMD
