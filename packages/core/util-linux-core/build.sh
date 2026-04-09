#!/bin/bash
# Util-linux 2.41.3
# LFS 13.0 Section 8.82

configure() {
    ./configure --bindir=/usr/bin      \
        --libdir=/usr/lib              \
        --runstatedir=/run             \
        --sbindir=/usr/sbin            \
        --disable-chfn-chsh            \
        --disable-login                \
        --disable-nologin              \
        --disable-su                   \
        --disable-setpriv              \
        --disable-runuser              \
        --disable-pylibmount           \
        --disable-liblastlog2          \
        --disable-static               \
        --without-python               \
        ADJTIME_PATH=/var/lib/hwclock/adjtime \
        --docdir=/usr/share/doc/util-linux-2.41.3
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    # LFS: touch /etc/fstab to prevent two test failures
    touch /etc/fstab

    # WARNING: Running tests as root can be harmful to the system
    # Some tests require specific kernel config options
    chown -R tester .
    su tester -c "make -k check" || true
}

do_install() {
    make DESTDIR="$DESTDIR" install

    # Set setuid bits — mount/umount need setuid for non-root user mounts.
    # Must be set here because tar-based deployment strips setuid bits.
    chmod 4755 "${DESTDIR}/usr/bin/mount"
    chmod 4755 "${DESTDIR}/usr/bin/umount"
}
