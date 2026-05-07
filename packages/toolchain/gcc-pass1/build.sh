#!/bin/bash
# GCC 15.2.0 — Pass 1 (Cross Compiler, C and C++ only)
# LFS 13.0 Section 5.3
#
# GMP, MPFR, and MPC are extracted into the GCC source tree by the
# build executor (bundled_deps in package.yml). They are built as
# part of GCC automatically.

configure() {
    set -e
    # On x86_64: set default directory name for 64-bit libraries to "lib"
    case $(uname -m) in
        x86_64)
            sed -e '/m64=/s/lib64/lib/' \
                -i.orig gcc/config/i386/t-linux64
        ;;
    esac

    mkdir -v build
    cd       build

    ../configure                             \
        --target=$IGOS_TARGET                \
        --prefix=$IGOS/tools                 \
        --with-glibc-version=2.43            \
        --with-sysroot=$IGOS                 \
        --with-newlib                        \
        --without-headers                    \
        --enable-default-pie                 \
        --enable-default-ssp                 \
        --disable-nls                        \
        --disable-shared                     \
        --disable-multilib                   \
        --disable-threads                    \
        --disable-libatomic                  \
        --disable-libgomp                    \
        --disable-libquadmath                \
        --disable-libssp                     \
        --disable-libvtv                     \
        --disable-libstdcxx                  \
        --enable-languages=c,c++
}

build() {
    set -e
    cd build
    make -j${IGOS_JOBS}
}

install() {
    set -e
    cd build
    make install

    # Create full internal limits.h header
    # GCC installs a partial limits.h that doesn't include the system header.
    # This creates the full version that will be needed later.
    cd ..
    cat gcc/limitx.h gcc/glimits.h gcc/limity.h > \
        $(dirname $($IGOS_TARGET-gcc -print-libgcc-file-name))/include/limits.h
}
