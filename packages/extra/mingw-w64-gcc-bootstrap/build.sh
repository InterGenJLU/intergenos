#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# mingw-w64-gcc-bootstrap 15.2.0 — `all-gcc` only, both PE triplets
# (GE extra-tier wave, RT-15 stage 3 — the cycle-breaker). Grounded
# against Fedora's mingw-gcc bootstrap pass + upstream howto-build +
# GLFS; flags per the research doc landed with this set.

TRIPLETS="x86_64-w64-mingw32 i686-w64-mingw32"

configure() {
    set -e
    local T
    for T in ${TRIPLETS}; do
        mkdir -p "build-${T}"
        ( cd "build-${T}" &&
          ../configure --prefix=/usr                     \
                       --target="${T}"                   \
                       --disable-shared                  \
                       --disable-multilib                \
                       --disable-threads                 \
                       --disable-nls                     \
                       --enable-languages=c,c++ )
        # NO --with-sysroot (GE-01 L21): a mingw-targeted gcc's built-in
        #   NATIVE_SYSTEM_HEADER_DIR is /mingw/include RELATIVE to the
        #   sysroot, so --with-sysroot=/usr/${T} sent fixincludes to
        #   /usr/${T}/mingw/include — which nothing populates — and
        #   all-gcc died at stmp-fixinc. Without a sysroot the tooldir
        #   convention finds /usr/${T}/include, exactly where
        #   mingw-w64-headers installs. BOTH references build sysroot-less
        #   (GLFS 13.0 static pass verbatim; Arch mingw-w64-gcc likewise).
        # --disable-threads/--disable-shared: the bootstrap pass builds no
        #   target libs at all (make all-gcc below), so the threads model
        #   is irrelevant here; the FINAL gcc pins --enable-threads=posix
        #   (DXVK hard requirement). System gmp/mpfr/mpc from the chroot
        #   (declared build deps), same as the native gcc-core.
    done
}

build() {
    set -e
    local T
    for T in ${TRIPLETS}; do
        # all-gcc = driver + cc1/cc1plus ONLY — no libgcc (which cannot
        # link before crt exists). This is the entire point of the stage.
        make -C "build-${T}" -j${IGOS_JOBS} all-gcc
    done
}

do_install() {
    set -e
    local T
    for T in ${TRIPLETS}; do
        make -C "build-${T}" DESTDIR="$DESTDIR" install-gcc
    done
    # Doc trees collide with the native gcc's (info/man carry shared names
    # like cpp.1/gcc.info between the two triplet loops and the host
    # compiler; locale likewise). The native gcc owns the documentation —
    # drop them here, same as Arch's mingw-w64-gcc does.
    rm -rf "${DESTDIR}/usr/share/info" \
           "${DESTDIR}/usr/share/man"  \
           "${DESTDIR}/usr/share/locale"
    # Libtool archives: none expected from all-gcc, but the delete is the
    # standing hygiene (Fedora deletes across the mingw family).
    find "${DESTDIR}" -name "*.la" -delete
}
