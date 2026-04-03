#!/bin/bash
# CMake 4.3.1 — Cross-platform build system
# BLFS 13.0

configure() {
    # Fix lib64 path
    sed -i '/"lib64"/s/64//' Modules/GNUInstallDirs.cmake

    ./bootstrap --prefix=/usr        \
                --system-libs        \
                --mandir=/usr/share/man  \
                --no-system-jsoncpp     \
                --no-system-cppdap      \
                --no-system-librhash    \
                --docdir=/usr/share/doc/cmake-${PKG_VERSION}
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
