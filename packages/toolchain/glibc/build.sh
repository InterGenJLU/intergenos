#!/bin/bash
# Glibc 2.43
# LFS 13.0 Section 5.5
#
# The main C library. This is the most critical package in the
# toolchain — if glibc doesn't build correctly, nothing else will work.
#
# IMPORTANT: The glibc-fhs-1.patch is applied by the build executor
# in the patch phase (declared in package.yml).

configure() {
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
        --with-headers=$IGOS/usr/include        \
        --disable-nscd                          \
        libc_cv_slibdir=/usr/lib
}

build() {
    cd build
    make -j${IGOS_JOBS}
}

install() {
    cd build
    make DESTDIR=$IGOS install

    # Fix hard coded path to the executable loader in ldd
    sed '/RTLDLIST=/s@/usr@@g' -i $IGOS/usr/bin/ldd
}

check() {
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
}
