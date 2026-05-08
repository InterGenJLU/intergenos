#!/bin/bash
# E2fsprogs 1.47.3
# LFS 13.0 Section 8.83

configure() {
    set -e
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
    set -e
    cd build
    make -j${IGOS_JOBS}
}

check() {
    set -e
    cd build
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        make check
}

do_install() {
    set -e
    cd build
    make DESTDIR="$DESTDIR" install

    # Remove useless static libraries
    rm -fv "${DESTDIR}/usr/lib"/{libcom_err,libe2p,libext2fs,libss}.a

    # Update system info file
    gunzip -v "${DESTDIR}/usr/share/info/libext2fs.info.gz"
    install-info --dir-file="${DESTDIR}/usr/share/info/dir" "${DESTDIR}/usr/share/info/libext2fs.info"

    # Create and install additional documentation
    makeinfo -o doc/com_err.info ../lib/et/com_err.texinfo
    install -v -m644 doc/com_err.info "${DESTDIR}/usr/share/info/"
    install-info --dir-file="${DESTDIR}/usr/share/info/dir" "${DESTDIR}/usr/share/info/com_err.info"
}

# Post-install: fix mke2fs defaults on live system
post_install() {
    set -e
    # Remove metadata_csum_seed from default mkfs features
    # (compatibility with older bootloaders/kernels)
    sed 's/metadata_csum_seed,//' -i /etc/mke2fs.conf
}
