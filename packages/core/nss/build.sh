#!/bin/bash
# NSS 3.121 — Network Security Services
# BLFS 13.0
# Non-standard build: uses raw make, no configure

configure() {
    :  # Patch applied in patch phase
}

build() {
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
    cd dist

    install -v -m755 -d "${DESTDIR}/usr/lib"
    install -v -m755 Linux*/lib/*.so              "${DESTDIR}/usr/lib"
    install -v -m644 Linux*/lib/{*.chk,libcrmf.a} "${DESTDIR}/usr/lib"

    install -v -m755 -d                           "${DESTDIR}/usr/include/nss"
    cp -v -RL {public,private}/nss/*              "${DESTDIR}/usr/include/nss"

    install -v -m755 -d "${DESTDIR}/usr/bin"
    install -v -m755 Linux*/bin/{certutil,nss-config,pk12util} "${DESTDIR}/usr/bin"

    install -v -m755 -d "${DESTDIR}/usr/lib/pkgconfig"
    install -v -m644 Linux*/lib/pkgconfig/nss.pc  "${DESTDIR}/usr/lib/pkgconfig"

    # p11-kit trust module symlink
    ln -sfv ./pkcs11/p11-kit-trust.so "${DESTDIR}/usr/lib/libnssckbi.so"
}
