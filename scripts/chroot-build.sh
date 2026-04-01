#!/bin/bash
# InterGenOS Chroot Build — LFS 13.0 Sections 7.5-7.12
#
# Runs INSIDE the chroot (launched via chroot-enter.sh).
# Creates directory layout, essential files, and builds 6 packages.
# All commands match LFS 13.0 book verbatim.
#
# Usage:
#   sudo bash /mnt/intergenos/scripts/chroot-enter.sh /mnt/intergenos/scripts/chroot-build.sh

set +h
umask 022

IGOS_SOURCES=/sources
IGOS_PATCHES=/sources
IGOS_LOGS=/var/log/igos-build
IGOS_JOBS=$(nproc)

mkdir -pv $IGOS_LOGS

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

log "--- 7.6: Creating Essential Files ---"

# /etc/mtab
ln -sv /proc/self/mounts /etc/mtab

# /etc/hosts
cat > /etc/hosts << EOF
127.0.0.1  localhost $(hostname)
::1        localhost
EOF

# /etc/passwd
cat > /etc/passwd << "EOF"
root:x:0:0:root:/root:/bin/bash
bin:x:1:1:bin:/dev/null:/usr/bin/false
daemon:x:6:6:Daemon User:/dev/null:/usr/bin/false
messagebus:x:18:18:D-Bus Message Daemon User:/run/dbus:/usr/bin/false
systemd-journal-gateway:x:73:73:systemd Journal Gateway:/:/usr/bin/false
systemd-journal-remote:x:74:74:systemd Journal Remote:/:/usr/bin/false
systemd-journal-upload:x:75:75:systemd Journal Upload:/:/usr/bin/false
systemd-network:x:76:76:systemd Network Management:/:/usr/bin/false
systemd-resolve:x:77:77:systemd Resolver:/:/usr/bin/false
systemd-timesync:x:78:78:systemd Time Synchronization:/:/usr/bin/false
systemd-coredump:x:79:79:systemd Core Dumper:/:/usr/bin/false
uuidd:x:80:80:UUID Generation Daemon User:/dev/null:/usr/bin/false
systemd-oom:x:81:81:systemd Out Of Memory Daemon:/:/usr/bin/false
nobody:x:65534:65534:Unprivileged User:/dev/null:/usr/bin/false
EOF

# /etc/group
cat > /etc/group << "EOF"
root:x:0:
bin:x:1:daemon
sys:x:2:
kmem:x:3:
tape:x:4:
tty:x:5:
daemon:x:6:
floppy:x:7:
disk:x:8:
lp:x:9:
dialout:x:10:
audio:x:11:
video:x:12:
utmp:x:13:
clock:x:14:
cdrom:x:15:
adm:x:16:
messagebus:x:18:
systemd-journal:x:23:
input:x:24:
mail:x:34:
kvm:x:61:
systemd-journal-gateway:x:73:
systemd-journal-remote:x:74:
systemd-journal-upload:x:75:
systemd-network:x:76:
systemd-resolve:x:77:
systemd-timesync:x:78:
systemd-coredump:x:79:
uuidd:x:80:
systemd-oom:x:81:
wheel:x:97:
users:x:999:
nogroup:x:65534:
EOF

# Test user (needed by some Chapter 8 test suites)
echo "tester:x:101:101::/home/tester:/bin/bash" >> /etc/passwd
echo "tester:x:101:" >> /etc/group
install -o tester -d /home/tester

# Initialize log files
touch /var/log/{btmp,lastlog,faillog,wtmp}
chgrp -v utmp /var/log/lastlog
chmod -v 664  /var/log/lastlog
chmod -v 600  /var/log/btmp

log "  Essential files created"

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
    bash -e "$@" >> "$pkg_log" 2>&1
    local rc=$?

    local elapsed=$(( $(date +%s) - start ))

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
