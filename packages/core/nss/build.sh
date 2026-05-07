#!/bin/bash
# NSS 3.121 — Network Security Services
# BLFS 13.0
# Non-standard build: uses raw make, no configure

configure() {
    set -e
    # Patch applied by builder PATCH phase (package.yml) with SHA256 validation.
    :
}

build() {
    set -e
    cd nss

    make BUILD_OPT=1                      \
      NSPR_INCLUDE_DIR=/usr/include/nspr  \
      USE_SYSTEM_ZLIB=1                   \
      ZLIB_LIBS=-lz                       \
      NSS_ENABLE_WERROR=0                 \
      NSS_USE_SYSTEM_SQLITE=1             \
      $([ $(uname -m) = x86_64 ] && echo USE_64=1) \
      -j${IGOS_JOBS}
}

do_install() {
    set -e
    cd dist

    install -v -m755 -d "${DESTDIR}/usr/lib"
    install -v -m755 Linux*/lib/*.so              "${DESTDIR}/usr/lib"
    install -v -m644 Linux*/lib/{*.chk,libcrmf.a} "${DESTDIR}/usr/lib"

    install -v -m755 -d                           "${DESTDIR}/usr/include/nss"
    cp -v -RL {public,private}/nss/*              "${DESTDIR}/usr/include/nss"

    install -v -m755 -d "${DESTDIR}/usr/bin"
    install -v -m755 Linux*/bin/{certutil,nss-config,pk12util} "${DESTDIR}/usr/bin"

    install -v -m755 -d "${DESTDIR}/usr/lib/pkgconfig"

    # Generate nss.pc from upstream template. NSS's Makefiles do not
    # process pkg/pkg-config/nss.pc.in themselves — the BLFS recipe's
    # `install Linux*/lib/pkgconfig/nss.pc` assumes a file that never
    # actually gets generated. Substitute the standard placeholders
    # against /usr install paths and our NSS+NSPR versions, then drop
    # the result into pkgconfig directly.
    sed -e 's|%prefix%|/usr|g'                       \
        -e 's|%exec_prefix%|${prefix}|g'             \
        -e 's|%libdir%|${prefix}/lib|g'              \
        -e 's|%includedir%|${prefix}/include/nss|g'  \
        -e "s|%NSS_VERSION%|${PKG_VERSION}|g"        \
        -e 's|%NSPR_VERSION%|4.38.2|g'               \
        ../nss/pkg/pkg-config/nss.pc.in              \
        > "${DESTDIR}/usr/lib/pkgconfig/nss.pc"
    chmod 644 "${DESTDIR}/usr/lib/pkgconfig/nss.pc"

    # p11-kit trust module symlink
    ln -sfv ./pkcs11/p11-kit-trust.so "${DESTDIR}/usr/lib/libnssckbi.so"
}
