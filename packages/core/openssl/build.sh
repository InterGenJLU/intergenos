#!/bin/bash
# OpenSSL 3.6.1
# LFS 13.0 Section 8.48

configure() {
    set -e
    ./config --prefix=/usr         \
        --openssldir=/etc/ssl      \
        --libdir=lib               \
        shared                     \
        zlib-dynamic
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    # HARNESS_JOBS speeds up the test suite significantly
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        env HARNESS_JOBS=$(nproc) make test
}

do_install() {
    set -e
    # Static archives libcrypto.a + libssl.a are RETAINED (no INSTALL_LIBS
    # sed strip). Consumers that need them: tpm2-tools-static (D-001
    # EXPERIMENTAL TPM2 unlock) + fido2-tools-static (D-001 EXPERIMENTAL
    # FIDO2 unlock). Both produce statically-linked binaries that live
    # inside the FDE initramfs envelope where no dynamic loader is
    # present. Footprint cost ~6 MB in chroot /usr/lib; dynamic-link
    # consumers are unaffected — .so emission is unchanged.
    make DESTDIR="$DESTDIR" MANSUFFIX=ssl install

    # Add version to documentation directory
    mv -v "${DESTDIR}/usr/share/doc/openssl" "${DESTDIR}/usr/share/doc/openssl-3.6.1"
    cp -vfr doc/* "${DESTDIR}/usr/share/doc/openssl-3.6.1"
}
