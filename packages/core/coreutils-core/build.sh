#!/bin/bash
# Coreutils 9.10
# LFS 13.0 Section 8.61

configure() {
    # Apply i18n patch for multibyte locale compliance
    patch -Np1 -i ${IGOS_PATCHES}/coreutils-9.10-i18n-1.patch

    # Regenerate build system after patching
    autoreconf -fv
    automake -af

    FORCE_UNSAFE_CONFIGURE=1 ./configure --prefix=/usr
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    # Run root-specific tests first
    make NON_ROOT_USERNAME=tester check-root

    # Create test group and run full test suite as tester
    groupadd -g 102 dummy -U tester

    chown -R tester .

    # < /dev/null prevents hang in graphical/SSH sessions
    su tester -c "PATH=$PATH make -k RUN_EXPENSIVE_TESTS=yes check" \
        < /dev/null || true

    groupdel dummy
}

do_install() {
    make DESTDIR="$DESTDIR" install

    # Move chroot to /usr/sbin and fix its man page section
    mv -v "${DESTDIR}/usr/bin/chroot" "${DESTDIR}/usr/sbin"
    mkdir -pv "${DESTDIR}/usr/share/man/man8"
    mv -v "${DESTDIR}/usr/share/man/man1/chroot.1" \
          "${DESTDIR}/usr/share/man/man8/chroot.8"
    sed -i 's/"1"/"8"/' "${DESTDIR}/usr/share/man/man8/chroot.8"
}
