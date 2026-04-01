#!/bin/bash
# GCC 15.2.0 (final system)
# LFS 13.0 Section 8.30

configure() {
    # Change default directory for 64-bit libraries to "lib"
    case $(uname -m) in
        x86_64)
            sed -e '/m64=/s/lib64/lib/' \
                -i.orig gcc/config/i386/t-linux64
        ;;
    esac

    mkdir -v build
    cd       build

    ../configure --prefix=/usr            \
        LD=ld                             \
        --enable-languages=c,c++          \
        --enable-default-pie              \
        --enable-default-ssp              \
        --enable-host-pie                 \
        --disable-multilib                \
        --disable-fixincludes             \
        --with-system-zlib
}

build() {
    cd build
    make -j${IGOS_JOBS}
}

check() {
    cd build
    # Increase stack size for tests
    ulimit -s 32768

    # Run tests as non-root (some tests fail as root)
    chown -R tester .
    su tester -c "PATH=$PATH make -k check" || true

    echo ""
    echo "=== GCC Test Summary ==="
    ../contrib/test_summary | grep -A7 '=== .* Summary ===' || true
}

install() {
    cd build
    make install

    # Compatibility symlink for cc
    ln -svr /usr/bin/cpp /usr/lib
    ln -sv gcc.1 /usr/share/man/man1/cc.1

    # Compatibility symlink: many packages use cc instead of gcc
    ln -sfv gcc /usr/bin/cc

    # Add LTO plugin to linker
    mkdir -pv /usr/lib/bfd-plugins/
    ln -sfv ../../libexec/gcc/$(gcc -dumpmachine)/15.2.0/liblto_plugin.so \
        /usr/lib/bfd-plugins/

    # Sanity check
    echo ""
    echo "=== GCC Sanity Check ==="
    echo 'int main(){}' > dummy.c
    cc dummy.c -v -Wl,--verbose &> dummy.log
    readelf -l a.out | grep ': /lib'

    echo ""
    echo "=== Start Files ==="
    grep -E -o '/usr/lib.*/S?crt[1in].*succeeded' dummy.log

    echo ""
    echo "=== Header Search Paths ==="
    grep -B4 '^ /usr/include' dummy.log

    echo ""
    echo "=== Linker Search Paths ==="
    grep 'SEARCH.*/usr/lib' dummy.log | sed 's|; |\n|g'

    echo ""
    echo "=== Correct libc ==="
    grep "/lib.*/libc.so.6 " dummy.log

    echo ""
    echo "=== Dynamic Linker ==="
    grep found dummy.log

    rm -v dummy.c a.out dummy.log

    # Move a misplaced file
    mkdir -pv /usr/share/gdb/auto-load/usr/lib
    mv -v /usr/lib/*gdb.py /usr/share/gdb/auto-load/usr/lib
}
