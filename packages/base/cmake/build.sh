#!/bin/bash
# CMake 4.2.3 — Cross-platform build system
# BLFS 13.0

configure() {
    # Fix lib64 path
    sed -i '/"lib64"/s/64//' Modules/GNUInstallDirs.cmake

    ./bootstrap --prefix=/usr        \
                --system-libs        \
                --mandir=/share/man  \
                --no-system-jsoncpp  \
                --no-system-cppdap   \
                --no-system-librhash \
                --docdir=/share/doc/cmake-4.2.3
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    bin/ctest -j$(nproc) || true
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
