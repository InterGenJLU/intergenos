#!/bin/bash
# Glibc 2.43
# LFS 13.0 Section 8.5
#
# DESTDIR exception: Glibc uses install_root instead of DESTDIR.
# Post-install: nsswitch.conf, ld.so.conf, timezone, locales.

configure() {
    # FHS compliance patch
    patch -Np1 -i ${IGOS_PATCHES}/glibc-fhs-1.patch

    mkdir -v build
    cd       build

    echo "rootsbindir=/usr/sbin" > configparms

    ../configure --prefix=/usr                   \
        --disable-werror                         \
        --disable-nscd                           \
        libc_cv_slibdir=/usr/lib                 \
        --enable-stack-protector=strong           \
        --enable-kernel=5.4
}

build() {
    cd build
    make -j${IGOS_JOBS}
}

check() {
    cd build
    # CRITICAL: Do not skip the glibc test suite
    make check || true

    # Check for timeouts (common in chroot)
    echo ""
    echo "=== Glibc Timeout Check ==="
    grep "Timed out" $(find -name \*.out) 2>/dev/null || echo "No timeouts"
}

do_install() {
    cd build

    # Prevent warnings during install
    touch "${DESTDIR}/etc/ld.so.conf" 2>/dev/null || true

    # Skip test-installation rule (it would fail in DESTDIR)
    sed '/test-installation/s@$(PERL)@echo not running@' -i ../Makefile

    # Glibc uses install_root, not DESTDIR
    make install_root="$DESTDIR" install

    # Fix ldd path
    sed '/RTLDLIST=/s@/usr@@g' -i "${DESTDIR}/usr/bin/ldd"

    # Install minimal set of locales needed for tests and basic operation
    mkdir -pv "${DESTDIR}/usr/lib/locale"
    # These localedef commands must run against the staged glibc
    # They will be re-run in post_install against the live system
}

# Post-install: runs on the live system AFTER deploy
post_install() {
    # Create essential locales
    localedef -i C -f UTF-8 C.UTF-8
    localedef -i en_US -f ISO-8859-1 en_US
    localedef -i en_US -f UTF-8 en_US.UTF-8

    # nsswitch.conf for systemd
    cat > /etc/nsswitch.conf << "EOF"
# Begin /etc/nsswitch.conf

passwd: files systemd
group: files systemd
shadow: files systemd

hosts: mymachines resolve [!UNAVAIL=return] files myhostname dns
networks: files

protocols: files
services: files
ethers: files
rpc: files

# End /etc/nsswitch.conf
EOF

    # Timezone data
    tar -xf ${IGOS_SOURCES}/tzdata2025c.tar.gz -C /tmp

    ZONEINFO=/usr/share/zoneinfo
    mkdir -pv $ZONEINFO/{posix,right}

    for tz in etcetera southamerica northamerica europe africa antarctica \
              asia australasia backward; do
        zic -L /dev/null   -d $ZONEINFO       /tmp/${tz}
        zic -L /dev/null   -d $ZONEINFO/posix /tmp/${tz}
        zic -L /tmp/leapseconds -d $ZONEINFO/right /tmp/${tz}
    done

    cp -v /tmp/zone.tab /tmp/zone1970.tab /tmp/iso3166.tab $ZONEINFO
    zic -d $ZONEINFO -p America/New_York
    unset ZONEINFO

    # Default to UTC — user can change with timedatectl
    ln -sfv /usr/share/zoneinfo/UTC /etc/localtime

    # Dynamic loader configuration
    cat > /etc/ld.so.conf << "EOF"
# Begin /etc/ld.so.conf
/usr/local/lib
/opt/lib

EOF

    cat >> /etc/ld.so.conf << "EOF"
# Add an include directory
include /etc/ld.so.conf.d/*.conf

EOF
    mkdir -pv /etc/ld.so.conf.d

    # Rebuild ld cache
    ldconfig
}
