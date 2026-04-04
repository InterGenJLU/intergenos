#!/bin/bash
# Util-linux 2.41.3
# LFS 13.0 Section 8.80

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

    if command -v su >/dev/null 2>&1 && id tester >/dev/null 2>&1; then
        chown -R tester .
        su tester -c "make -k check" || true
    else
        make -k check || true
    fi
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
