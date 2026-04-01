#!/bin/bash
# GCC 15.2.0 (final system)
# LFS 13.0 Section 8.30

configure() {
    # Fix for glibc-2.43 compatibility
    sed -i 's/char [*]q/const &/' libgomp/affinity-fmt.c

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
        --build=x86_64-igos-linux-gnu     \
        --host=x86_64-igos-linux-gnu      \
        --target=x86_64-igos-linux-gnu    \
        LD=ld                             \
        --enable-languages=c,c++          \
        --enable-default-pie              \
        --enable-default-ssp              \
        --enable-host-pie                 \
        --disable-multilib                \
        --disable-bootstrap               \
        --disable-fixincludes             \
        --with-system-zlib                \
        --with-pkgversion='InterGenOS GCC 15.2.0' \
        --with-bugurl='https://github.com/InterGenOS/intergenos/issues'
}

build() {
    cd build
    make -j${IGOS_JOBS}
}

check() {
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
    cd build
    make DESTDIR="$DESTDIR" install

    # Compatibility symlinks
    ln -svr "${DESTDIR}/usr/bin/cpp" "${DESTDIR}/usr/lib"
    ln -sv gcc.1 "${DESTDIR}/usr/share/man/man1/cc.1"

    # Add LTO plugin to linker
    mkdir -pv "${DESTDIR}/usr/lib/bfd-plugins/"
    ln -sfv ../../libexec/gcc/$(gcc -dumpmachine)/15.2.0/liblto_plugin.so \
        "${DESTDIR}/usr/lib/bfd-plugins/"

    # Move gdb python files
    mkdir -pv "${DESTDIR}/usr/share/gdb/auto-load/usr/lib"
    mv -v "${DESTDIR}/usr/lib"/*gdb.py "${DESTDIR}/usr/share/gdb/auto-load/usr/lib"

    # Fix ownership of headers
    chown -v -R root:root \
        "${DESTDIR}/usr/lib/gcc/$(gcc -dumpmachine)/15.2.0/include"{,-fixed}
}

# Post-install: runs on the live system AFTER deploy
post_install() {
    # GCC sanity check — must pass or stop the build
    echo ""
    echo "=== GCC Sanity Check ==="
    echo 'int main(){}' > /tmp/dummy.c
    cc /tmp/dummy.c -v -Wl,--verbose &> /tmp/dummy.log
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
