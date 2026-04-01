#!/bin/bash
# E2fsprogs 1.47.3
# LFS 13.0 Section 8.81

configure() {
    mkdir -v build
    cd       build

    ../configure --prefix=/usr           \
        --sysconfdir=/etc                \
        --enable-elf-shlibs              \
        --disable-libblkid               \
        --disable-libuuid                \
        --disable-uuidd                  \
        --disable-fsck
}

build() {
    cd build
    make -j${IGOS_JOBS}
}

check() {
    cd build
    make check || true
}

do_install() {
    cd build
    make DESTDIR="$DESTDIR" install

    # Remove useless static libraries
    rm -fv "${DESTDIR}/usr/lib"/{libcom_err,libe2p,libext2fs,libss}.a

    # Update system info file
    gunzip -v "${DESTDIR}/usr/share/info/libext2fs.info.gz"
    install-info --dir-file="${DESTDIR}/usr/share/info/dir" "${DESTDIR}/usr/share/info/libext2fs.info"

    # Create and install additional documentation
    makeinfo -o doc/com_err.info ../lib/et/com_err.texinfo
    install -v -m644 doc/com_err.info "${DESTDIR}/usr/share/info"
    install-info --dir-file="${DESTDIR}/usr/share/info/dir" "${DESTDIR}/usr/share/info/com_err.info"
}
