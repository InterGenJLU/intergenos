#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# GCC 15.2.0 (final system)
# LFS 13.0 Section 8.30

configure() {
    set -e
    # Fix for glibc-2.43 compatibility
    sed -i 's/char [*]q/const &/' libgomp/affinity-fmt.c

    # 64-bit libraries live in "lib", 32-bit multilib in "lib32"
    # (Multilib-LFS 13.0 §8.31 two-expression form; GE arc, D-W0-5)
    case $(uname -m) in
        x86_64)
            sed -e '/m64=/s/lib64/lib/' \
                -e '/m32=/s/m32=.*/m32=..\/lib32$(call if_multiarch,:i386-linux-gnu)/' \
                -i.orig gcc/config/i386/t-linux64
        ;;
    esac

    # Default -m32 code to stack realignment (Multilib-LFS 13.0; D-W0-2):
    # prebuilt 32-bit binaries (Steam/Wine era) assume a 4-byte-aligned stack
    # and SIGSEGV on SSE movaps without it. Compiler default, not per-recipe CFLAGS.
    sed '/STACK_REALIGN_DEFAULT/s/0/(!TARGET_64BIT \&\& TARGET_SSE)/' \
        -i gcc/config/i386/i386.h

    mkdir -v build
    cd       build

    ../configure --prefix=/usr            \
        --build=x86_64-igos-linux-gnu     \
        --host=x86_64-igos-linux-gnu      \
        --target=x86_64-igos-linux-gnu    \
        LD=ld                             \
        --enable-languages=c,c++,fortran  \
        --enable-default-pie              \
        --enable-default-ssp              \
        --enable-host-pie                 \
        --enable-multilib                 \
        --with-multilib-list=m64,m32      \
        --disable-bootstrap               \
        --disable-fixincludes             \
        --with-system-zlib                \
        --with-pkgversion='InterGenOS GCC 15.2.0' \
        --with-bugurl='https://github.com/InterGenJLU/intergenos/issues'
}

build() {
    set -e
    cd build
    make -j${IGOS_JOBS}
}

check() {
    set -e
    cd build
    # Increase stack size for tests
    ulimit -s -H unlimited

    # Remove test known to fail with current Python
    sed -e '/cpython/d' -i ../gcc/testsuite/gcc.dg/plugin/plugin.exp

    # Run tests as non-root (some tests fail as root)
    chown -R tester .
    su tester -c "PATH=$PATH make -k -j${IGOS_JOBS} check" || true

    echo ""
    echo "=== GCC Test Summary ==="
    ../contrib/test_summary | grep -A7 '=== .* Summary ===' || true
}

do_install() {
    set -e
    cd build
    make DESTDIR="$DESTDIR" install

    # Compatibility symlinks
    ln -svr "${DESTDIR}/usr/bin/cpp" "${DESTDIR}/usr/lib"
    ln -sv gcc.1 "${DESTDIR}/usr/share/man/man1/cc.1"
    # /usr/bin/cc -> gcc (LFS ch8 gcc ships this; the recipe already shipped
    # the cc.1 MAN PAGE above while omitting the binary it documents).
    # PI-Z16: installed systems had no `cc` — first runtime consumer was the
    # nvidia module rebuild (src/nvidia Makefile compiles with plain `cc`,
    # Error 127 on every object). The chroot masked it: the toolchain gcc
    # creates the symlink there, but targets only get gcc-core.
    ln -sv gcc "${DESTDIR}/usr/bin/cc"

    # Add LTO plugin to linker
    mkdir -pv "${DESTDIR}/usr/lib/bfd-plugins/"
    ln -sfv ../../libexec/gcc/$(gcc -dumpmachine)/15.2.0/liblto_plugin.so \
        "${DESTDIR}/usr/lib/bfd-plugins/"

    # Expose the static libgcc in the default library search path.
    # gcc's install places the /usr/lib/libgcc_s.so ld script — which is
    # GROUP ( libgcc_s.so.1 -lgcc ) — while libgcc.a stays in gcc's private
    # libdir. GCC-driven links resolve -lgcc through their own -L there,
    # but other toolchains do not: clang's GCC-installation detection has
    # no x86_64-igos-linux-gnu in its candidate-triple list, so clang+lld
    # fail every link that pulls -lgcc_s (ld.lld: unable to find -lgcc).
    # The symlink makes the script's own GROUP reference resolvable to any
    # linker; gcc's behavior is unchanged (its private -L wins, same file).
    ln -sv gcc/$(gcc -dumpmachine)/15.2.0/libgcc.a "${DESTDIR}/usr/lib/libgcc.a"

    # Move gdb python files
    mkdir -pv "${DESTDIR}/usr/share/gdb/auto-load/usr/lib"
    mv -v "${DESTDIR}/usr/lib"/*gdb.py "${DESTDIR}/usr/share/gdb/auto-load/usr/lib"

    # Fix ownership of headers
    chown -v -R root:root \
        "${DESTDIR}/usr/lib/gcc/$(gcc -dumpmachine)/15.2.0/include"{,-fixed}
}

# Post-install: runs on the live system AFTER deploy
post_install() {
    set -e
    # GCC sanity check — must pass or stop the build
    echo ""
    echo "=== GCC Sanity Check ==="
    echo 'int main(){}' > /tmp/dummy.c
    cc /tmp/dummy.c -o /tmp/a.out -v -Wl,--verbose &> /tmp/dummy.log
    readelf -l /tmp/a.out | grep ': /lib'

    echo ""
    echo "=== Start Files ==="
    grep -E -o '/usr/lib.*/S?crt[1in].*succeeded' /tmp/dummy.log

    echo ""
    echo "=== Header Search Paths ==="
    grep -B4 '^ /usr/include' /tmp/dummy.log

    echo ""
    echo "=== Linker Search Paths ==="
    grep 'SEARCH.*/usr/lib' /tmp/dummy.log | sed 's|; |\n|g'

    echo ""
    echo "=== Correct libc ==="
    grep "/lib.*/libc.so.6 " /tmp/dummy.log

    echo ""
    echo "=== Dynamic Linker ==="
    grep found /tmp/dummy.log

    rm -v /tmp/dummy.c /tmp/a.out /tmp/dummy.log
}
