#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# mingw-w64-gcc 15.2.0 — full cross-gcc for both PE triplets
# (GE extra-tier wave, RT-15 stage 6). Grounded against the Arch
# mingw-w64-gcc PKGBUILD, Fedora's mingw-gcc full pass, and GLFS; flags
# per the research doc landed with this set.
#
# Threads model is POSIX and non-negotiable: DXVK's README documents the
# hard requirement (win32 threads fail its C++ build with std::cv_status
# errors and an instruction to recompile the cross gcc). Every reference
# distro (Arch/Fedora/GLFS/Homebrew) ships posix.

TRIPLETS="x86_64-w64-mingw32 i686-w64-mingw32"

configure() {
    set -e
    local T dwarf_flags
    for T in ${TRIPLETS}; do
        # --with-dwarf2 on the i686 target only (Arch/GLFS): 32-bit PE
        # uses DWARF-2 EH there; x86_64 uses SEH (--disable-sjlj on both).
        case "${T}" in
            i686-*) dwarf_flags="--with-dwarf2" ;;
            *)      dwarf_flags="" ;;
        esac
        mkdir -p "build-${T}"
        ( cd "build-${T}" &&
          ../configure --prefix=/usr                     \
                       --target="${T}"                   \
                       --enable-threads=posix            \
                       --enable-shared                   \
                       --enable-static                   \
                       --disable-multilib                \
                       --disable-sjlj-exceptions         \
                       --disable-nls                     \
                       --enable-languages=c,c++          \
                       ${dwarf_flags} )
        # NO --with-sysroot (GE-01 L21, same defect as the bootstrap
        #   recipe): mingw gcc's built-in NATIVE_SYSTEM_HEADER_DIR is
        #   /mingw/include relative to the sysroot, so a sysroot of
        #   /usr/${T} points fixincludes/header-search at the nonexistent
        #   /usr/${T}/mingw/include. Sysroot-less, the tooldir convention
        #   resolves /usr/${T}/include + /usr/${T}/lib — exactly where
        #   mingw-w64-headers/crt/winpthreads install. Both references
        #   (GLFS 13.0 + Arch) build sysroot-less.
        # c,c++ covers all three consumers (wine PE side, DXVK, and
        # vkd3d-proton — GLFS, the same-use-case precedent, pins exactly
        # this set); system gmp/mpfr/mpc from the chroot, like gcc-core.
    done
}

build() {
    set -e
    local T
    for T in ${TRIPLETS}; do
        # Full make: libgcc/libstdc++ now build — crt + winpthreads exist
        # in the sysroots (the whole point of the staged sequence).
        make -C "build-${T}" -j${IGOS_JOBS}
    done
}

do_install() {
    set -e
    local T
    for T in ${TRIPLETS}; do
        make -C "build-${T}" DESTDIR="$DESTDIR" install
        # Target DLLs (libstdc++-6.dll, libgcc_s_*.dll) move lib -> bin
        # per mingw convention (Arch does the same; winpthreads installs
        # its DLL to bin natively) — PE loaders search the exe dir/bin,
        # never a Unix-style lib dir.
        if compgen -G "${DESTDIR}/usr/${T}/lib/*.dll" > /dev/null; then
            # mkdir first (L22): /usr/${T}/bin EXISTS on the live root
            # (binutils + winpthreads packages own it) but NOT in THIS
            # package's DESTDIR staging — gcc's own install populates
            # only usr/${T}/lib, so the mv target must be created here.
            mkdir -p "${DESTDIR}/usr/${T}/bin"
            mv "${DESTDIR}/usr/${T}/lib/"*.dll "${DESTDIR}/usr/${T}/bin/"
        fi
    done
    # Doc trees collide with the native gcc's (same reasoning + same
    # handling as the bootstrap recipe; Arch removes usr/share wholesale).
    rm -rf "${DESTDIR}/usr/share/info" \
           "${DESTDIR}/usr/share/man"  \
           "${DESTDIR}/usr/share/locale"
    # libcc1 is the gdb compile-support plugin for the HOST toolchain —
    # the native gcc owns /usr/lib/libcc1.*; the cross build's copy would
    # collide (Arch removes it too).
    rm -f "${DESTDIR}"/usr/lib/libcc1.*
    find "${DESTDIR}" -name "*.la" -delete
}
