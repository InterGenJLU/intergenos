#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
# InterGenOS Chroot Build — LFS 13.0 Sections 7.5-7.12
#
# Runs INSIDE the chroot (launched via chroot-enter.sh).
# Creates directory layout, essential files, and builds 6 packages.
# All commands match LFS 13.0 book verbatim.
#
# Usage:
#   sudo bash /mnt/intergenos/scripts/chroot-enter.sh /mnt/intergenos/scripts/chroot-build.sh

set +h
set -euo pipefail
umask 022

IGOS_SOURCES=/sources
IGOS_PATCHES=/sources
IGOS_LOGS=/mnt/intergenos/build/logs
IGOS_JOBS=$(nproc)

mkdir -p "$IGOS_LOGS"

# Forensic-trace bash companion (no-op when IGOS_BUILD_DEBUG_VERBOSE unset).
# Runs INSIDE the chroot; IGOS_TRACE_ROOT (passed by chroot-enter.sh) is
# bind-mounted into the chroot by chroot-setup.sh so writes land on the
# durable host sink under the shared runid.
# shellcheck disable=SC1091
[ -f /mnt/intergenos/scripts/lib/trace.sh ] && source /mnt/intergenos/scripts/lib/trace.sh
[ "${IGOS_TRACE_LIB_LOADED:-0}" = "1" ] && trace_init "tier-chroottools"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$IGOS_LOGS/chroot-build.log"
}

log ""
log "============================================"
log "  InterGenOS Chroot Build"
log "  LFS 13.0 Chapter 7.5-7.12"
log "  Start: $(date)"
log "============================================"
log ""

# ============================================================================
# 7.5: Creating Directories
# ============================================================================

log "--- 7.5: Creating Directories ---"

mkdir -pv /{boot,home,mnt,opt,srv}
mkdir -pv /etc/{opt,sysconfig}
mkdir -pv /lib/firmware
mkdir -pv /media/{floppy,cdrom}
mkdir -pv /usr/{,local/}{include,src}
mkdir -pv /usr/lib/locale
mkdir -pv /usr/local/{bin,lib,sbin}
mkdir -pv /usr/{,local/}share/{color,dict,doc,info,locale,man}
mkdir -pv /usr/{,local/}share/{misc,terminfo,zoneinfo}
mkdir -pv /usr/{,local/}share/man/man{1..8}
mkdir -pv /var/{cache,local,log,mail,opt,spool}
mkdir -pv /var/lib/{color,misc,locate}

ln -sfv /run /var/run
ln -sfv /run/lock /var/lock

install -dv -m 0750 /root
install -dv -m 1777 /tmp /var/tmp

log "  Directories created"

# ============================================================================
# 7.6: Creating Essential Files and Symlinks
# ============================================================================
#
# Per plan v2 (2026-05-27, bilateral review APPROVE-clean at 06:41Z),
# /etc/{passwd,group,shadow,gshadow} + /etc/{profile,bashrc,...} are
# owned by packages/core/intergenos-base-files/. Same files/ tree
# bootstraps the build chroot here (single source of truth — eliminates
# the drift hazard that produced install attempts #21-#22 first-boot
# cascading failures). chroot-build.sh runs INSIDE the build chroot;
# /mnt/intergenos/packages/core/intergenos-base-files/files/ is
# reachable via self-contained-chroot pattern at scripts/build-
# intergenos.sh:910-924.

log "--- 7.6: Creating Essential Files (from intergenos-base-files package) ---"

# /etc/mtab — kept here (LFS 7.6 convention, not package-shipped)
ln -sv /proc/self/mounts /etc/mtab

# /etc/hosts — initial baseline only (Ch.9 config overwrites with full
# IPv6 entries via cp -a from the package's files/etc/...).
cat > /etc/hosts << EOF
127.0.0.1  localhost $(hostname)
::1        localhost
EOF

# /etc/passwd + /etc/group: cp -a from intergenos-base-files package
# files/ tree (single source of truth per plan v2). shadow + gshadow
# are not bootstrapped here — glibc / shadow build phase runs
# pwconv / grpconv to materialize them from passwd / group above.
# The final install target receives the canonical shadow / gshadow
# content via the intergenos-base-files package install during
# phase_core_extra.
BASEFILES_SRC=/mnt/intergenos/packages/core/intergenos-base-files/files
if [ ! -d "$BASEFILES_SRC" ]; then
    echo "FATAL: intergenos-base-files content tree missing at $BASEFILES_SRC" >&2
    echo "Self-contained-chroot rsync at build-intergenos.sh:910-924 should have placed it." >&2
    exit 1
fi

ACCOUNT_SKEL="$BASEFILES_SRC/usr/share/intergenos-base-files/account-skel"
if [ ! -d "$ACCOUNT_SKEL" ]; then
    echo "FATAL: account skeleton missing at $ACCOUNT_SKEL" >&2
    echo "The account databases moved out of files/etc/ on 2026-07-24 so they are" >&2
    echo "no longer deploy-target bytes; they ship as reference data here." >&2
    exit 1
fi
cp -av "$ACCOUNT_SKEL/passwd" /etc/passwd
cp -av "$ACCOUNT_SKEL/group"  /etc/group

# Test user (needed by some Chapter 8 test suites) — appended to the
# package-shipped baseline. Build-time-only addition.
echo "tester:x:101:101::/home/tester:/bin/bash" >> /etc/passwd
echo "tester:x:101:" >> /etc/group
install -o tester -d /home/tester

# Initialize log files (build-time-only; install target gets these via
# the intergenos-base-files package's tmpfiles.d/00-intergenos.conf
# applied by Forge PHASE_SERVICES systemd-tmpfiles --create).
touch /var/log/{btmp,lastlog,faillog,wtmp}
chgrp -v utmp /var/log/lastlog
chmod -v 664  /var/log/lastlog
chmod -v 600  /var/log/btmp

log "  Essential files created (passwd/group from package, tester appended)"

# ============================================================================
# Build helper
# ============================================================================

build_in_chroot() {
    local name="$1"
    local version="$2"
    local tarball="$3"
    shift 3

    export PKG_VERSION="$version"

    local pkg_log="$IGOS_LOGS/${name}-chroot-$(date '+%Y%m%d-%H%M%S').log"
    local workdir="/tmp/igos-build/${name}"

    log "=========================================="
    log "Building: $name $version (in chroot)"
    log "Log: $pkg_log"
    log "=========================================="

    rm -rf "$workdir"
    mkdir -pv "$workdir"
    tar -xf "$IGOS_SOURCES/$tarball" -C "$workdir" --strip-components=1
    cd "$workdir"

    local start=$(date +%s)

    # Run the build commands
    local rc=0
    bash -e "$@" >> "$pkg_log" 2>&1 || rc=$?

    local elapsed=$(( $(date +%s) - start ))

    # Byte-capture: full per-package output log + the exact input command file.
    # Fires for BOTH success and failure (before the failure return below).
    trace_pkg_capture --pkg "$name" --version "$version" --tier chroottools --phase all --rc "$rc" --duration-ms "$(( elapsed * 1000 ))" --log "$pkg_log" --cmd-file "$1"

    if [ $rc -ne 0 ]; then
        log "FAILED: $name $version (${elapsed}s, exit $rc)"
        log "Last 20 lines:"
        tail -20 "$pkg_log" | while read l; do log "  $l"; done
        return 1
    fi

    log "SUCCESS: $name $version (${elapsed}s)"
    log ""
    cd /
    rm -rf "$workdir"
    return 0
}

# ============================================================================
# 7.7: Gettext
# ============================================================================

cat > /tmp/build-gettext-chroot.sh << 'BUILDEOF'
./configure --disable-shared
make -j${IGOS_JOBS}
cp -v gettext-tools/src/{msgfmt,msgmerge,xgettext} /usr/bin
BUILDEOF
build_in_chroot "gettext" "1.0" "gettext-1.0.tar.xz" /tmp/build-gettext-chroot.sh || exit 1

# ============================================================================
# 7.8: Bison
# ============================================================================

cat > /tmp/build-bison-chroot.sh << 'BUILDEOF'
./configure --prefix=/usr --docdir=/usr/share/doc/bison-$PKG_VERSION
make -j${IGOS_JOBS}
make install
BUILDEOF
build_in_chroot "bison" "3.8.2" "bison-3.8.2.tar.xz" /tmp/build-bison-chroot.sh || exit 1

# ============================================================================
# 7.9: Perl
# ============================================================================

cat > /tmp/build-perl-chroot.sh << 'BUILDEOF'
sh Configure -des                                         \
             -D prefix=/usr                               \
             -D vendorprefix=/usr                         \
             -D useshrplib                                \
             -D privlib=/usr/lib/perl5/5.42/core_perl     \
             -D archlib=/usr/lib/perl5/5.42/core_perl     \
             -D sitelib=/usr/lib/perl5/5.42/site_perl     \
             -D sitearch=/usr/lib/perl5/5.42/site_perl    \
             -D vendorlib=/usr/lib/perl5/5.42/vendor_perl \
             -D vendorarch=/usr/lib/perl5/5.42/vendor_perl

make -j${IGOS_JOBS}
make install
BUILDEOF
build_in_chroot "perl" "5.42.0" "perl-5.42.0.tar.xz" /tmp/build-perl-chroot.sh || exit 1

# ============================================================================
# 7.10: Python
# ============================================================================

cat > /tmp/build-python-chroot.sh << 'BUILDEOF'
./configure --prefix=/usr   \
            --enable-shared \
            --without-ensurepip

make -j${IGOS_JOBS}
make install
BUILDEOF
build_in_chroot "python" "3.14.3" "Python-3.14.3.tar.xz" /tmp/build-python-chroot.sh || exit 1

# ============================================================================
# 7.11: Texinfo
# ============================================================================

cat > /tmp/build-texinfo-chroot.sh << 'BUILDEOF'
./configure --prefix=/usr
make -j${IGOS_JOBS}
make install
BUILDEOF
build_in_chroot "texinfo" "7.2" "texinfo-7.2.tar.xz" /tmp/build-texinfo-chroot.sh || exit 1

# ============================================================================
# 7.12: Util-linux
# ============================================================================

cat > /tmp/build-utillinux-chroot.sh << 'BUILDEOF'
mkdir -pv /var/lib/hwclock

./configure --libdir=/usr/lib     \
            --runstatedir=/run    \
            --disable-chfn-chsh   \
            --disable-login       \
            --disable-nologin     \
            --disable-su          \
            --disable-setpriv     \
            --disable-runuser     \
            --disable-pylibmount  \
            --disable-static      \
            --disable-liblastlog2 \
            --without-python      \
            ADJTIME_PATH=/var/lib/hwclock/adjtime \
            --docdir=/usr/share/doc/util-linux-$PKG_VERSION

make -j${IGOS_JOBS}
make install
BUILDEOF
build_in_chroot "util-linux" "2.41.3" "util-linux-2.41.3.tar.xz" /tmp/build-utillinux-chroot.sh || exit 1

# ============================================================================
# 7.13: Cleaning up
# ============================================================================

log "--- 7.13: Cleaning up ---"

# Remove temporary documentation
rm -rf /usr/share/{info,man,doc}/*

# Remove libtool .la files
find /usr/{lib,libexec} -name \*.la -delete

# Remove cross-compilation tools (no longer needed)
rm -rf /tools

log "  Cleanup complete"

# ============================================================================
# Summary
# ============================================================================

log ""
log "============================================"
log "  CHROOT BUILD COMPLETE"
log "  Packages built: 6"
log "    1. gettext 1.0"
log "    2. bison 3.8.2"
log "    3. perl 5.42.0"
log "    4. python 3.14.3"
log "    5. texinfo 7.2"
log "    6. util-linux 2.41.3"
log ""
log "  Directory layout created (FHS compliant)"
log "  Essential files created (passwd, group, hosts)"
log "  Cross-toolchain removed (/tools deleted)"
log "  System ready for Chapter 8 (core system build)"
log "============================================"
