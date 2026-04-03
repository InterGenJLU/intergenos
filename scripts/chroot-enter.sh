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
    # Run a specific script inside the chroot
    CHROOT_CMD="/bin/bash $1"
else
    # Interactive shell
    CHROOT_CMD="/bin/bash --login"
fi

# Number of cores for parallel builds
JOBS=$(nproc)

# Capture host timezone before entering chroot
HOST_TZ="$(cat /etc/timezone 2>/dev/null || echo UTC)"

# Enter the chroot with a clean environment
# env -i clears ALL host environment variables
# Only HOME, TERM, TZ, PS1, PATH, MAKEFLAGS, TESTSUITEFLAGS survive
chroot "$IGOS" /usr/bin/env -i               \
    HOME=/root                               \
    TERM="$TERM"                             \
    TZ="$HOST_TZ"                            \
    PS1='\[\e[1;34m\][\[\e[m\]\[\e[1;31m\](igos-chroot)\[\e[m\]\[\e[1;34m\]]\[\e[m\]\[\e[1;34m\][\[\e[m\]\[\e[1;37m\]<\[\e[m\]\[\e[1;32m\]\w\[\e[m\]\[\e[1;37m\]>\[\e[m\]\[\e[1;34m\]]\[\e[m\]\[\e[1;37m\]:\[\e[m\]\[\e[1;31m\]#\[\e[m\] ' \
    PATH=/usr/bin:/usr/sbin                  \
    MAKEFLAGS="-j${JOBS}"                    \
    TESTSUITEFLAGS="-j${JOBS}"               \
    IGOS_JOBS="${JOBS}"                       \
    IGOS_SOURCES=/sources                    \
    IGOS_PATCHES=/sources                    \
    IGOS_LOGS=/var/log/igos-build            \
    PKG_VERSION=""                           \
    IGOS_START_AT="${IGOS_START_AT:-}"       \
    $CHROOT_CMD
