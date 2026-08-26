#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Glibc 2.43
# LFS 13.0 Section 5.5
#
# The main C library. This is the most critical package in the
# toolchain — if glibc doesn't build correctly, nothing else will work.
#
# IMPORTANT: The glibc-fhs-1.patch is applied by the build executor
# in the patch phase (declared in package.yml).

configure() {
    set -e
    # Create LSB compliance symlink and dynamic loader compatibility link
    case $(uname -m) in
        i?86)
            ln -sfv ld-linux.so.2 $IGOS/lib/ld-lsb.so.3
        ;;
        x86_64)
            ln -sfv ../lib/ld-linux-x86-64.so.2 $IGOS/lib64
            ln -sfv ../lib/ld-linux-x86-64.so.2 $IGOS/lib64/ld-lsb-x86-64.so.3
        ;;
    esac

    mkdir -v build
    cd       build

    # Ensure ldconfig and sln install into /usr/sbin
    echo "rootsbindir=/usr/sbin" > configparms

    ../configure                                \
        --prefix=/usr                           \
        --host=$IGOS_TARGET                     \
        --build=$(../scripts/config.guess)      \
        --enable-kernel=5.4                     \
        --disable-nscd                          \
        libc_cv_slibdir=/usr/lib
}

build() {
    set -e
    cd build
    make -j${IGOS_JOBS}
}

install() {
    set -e
    cd build
    make DESTDIR=$IGOS install

    # Fix hard coded path to the executable loader in ldd
    sed '/RTLDLIST=/s@/usr@@g' -i $IGOS/usr/bin/ldd

    # --- m32 temp glibc pass (Multilib-LFS 13.0 §5.5.2; GE arc Wave 0) ---
    # The 64-bit pass above is byte-identical to vanilla LFS; this appended
    # pass builds the 32-bit temp glibc into /usr/lib32. Header-clobber
    # protection is the DESTDIR-then-allowlist pattern (D-W0-6): ONLY the
    # gnu/{lib-names,stubs}-32.h pair leaves the m32 DESTDIR — a direct
    # m32 "make install" over the sysroot would overwrite the 64-bit headers.
    make clean
    find .. -name "*.a" -delete

    echo "rootsbindir=/usr/sbin" > configparms

    CC="$IGOS_TARGET-gcc -m32"                  \
    CXX="$IGOS_TARGET-g++ -m32"                 \
    ../configure                                \
        --prefix=/usr                           \
        --host=i686-igos-linux-gnu              \
        --build=$(../scripts/config.guess)      \
        --enable-kernel=5.4                     \
        --disable-nscd                          \
        --with-headers=$IGOS/usr/include        \
        --libdir=/usr/lib32                     \
        --libexecdir=/usr/lib32                 \
        libc_cv_slibdir=/usr/lib32

    make -j${IGOS_JOBS}
    make DESTDIR=$PWD/DESTDIR install

    cp -a DESTDIR/usr/lib32 $IGOS/usr/
    # /usr/bin/install MUST be called by FULL PATH here: this function is
    # itself named `install`, so a bare `install` resolves to the FUNCTION
    # and re-enters it — infinite self-recursion re-running the whole
    # 64-bit install + m32 clean/configure/build/install cycle (~25k log
    # lines per iteration; 56 iterations / ~5h burned on the GE-01 launch,
    # 2026-07-03, before the operator caught it). It survived instead of
    # crashing because the phase driver's `install || { ... }` suspends
    # set -e through the whole function body (the errexit-suspension
    # class). The multilib-LFS book's bare `install -vm644` is written for
    # a plain shell — inside our install() phase function the full path is
    # load-bearing.
    /usr/bin/install -vm644 DESTDIR/usr/include/gnu/{lib-names,stubs}-32.h \
                   $IGOS/usr/include/gnu/

    # 32-bit dynamic loader compatibility link (m32 PT_INTERP = /lib/ld-linux.so.2)
    ln -svf ../lib32/ld-linux.so.2 $IGOS/lib/ld-linux.so.2
}

check() {
    set -e
    # Sanity check — verify the cross-toolchain works correctly
    cd build

    echo 'int main(){}' | $IGOS_TARGET-gcc -x c - -v -Wl,--verbose &> dummy.log
    readelf -l a.out | grep ': /lib'

    # Expected: [Requesting program interpreter: /lib64/ld-linux-x86-64.so.2]
    # The path should NOT contain the $IGOS prefix.

    echo ""
    echo "=== Sanity Check: Program Interpreter ==="
    readelf -l a.out | grep ': /lib'

    echo ""
    echo "=== Sanity Check: Start Files ==="
    grep -E -o "$IGOS/lib.*/S?crt[1in].*succeeded" dummy.log

    echo ""
    echo "=== Sanity Check: Header Search Paths ==="
    grep -B3 "^ $IGOS/usr/include" dummy.log

    echo ""
    echo "=== Sanity Check: Linker Search Paths ==="
    grep 'SEARCH.*/usr/lib' dummy.log | sed 's|; |\n|g'

    echo ""
    echo "=== Sanity Check: Correct libc ==="
    grep "/lib.*/libc.so.6 " dummy.log

    echo ""
    echo "=== Sanity Check: Dynamic Linker ==="
    grep found dummy.log

    # Clean up
    rm -v a.out dummy.log

    # --- m32 sanity (multilib): interpreter must be /lib/ld-linux.so.2 ---
    echo 'int main(){}' | $IGOS_TARGET-gcc -m32 -x c - -o a32.out
    echo ""
    echo "=== Sanity Check (m32): Program Interpreter ==="
    readelf -l a32.out | grep ': /lib'
    readelf -l a32.out | grep -q '/lib/ld-linux.so.2'
    rm -v a32.out
}
