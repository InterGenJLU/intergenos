#!/bin/bash
# boost 1.86.0 — C++ utility libraries
# BLFS 13.0

configure() {
    # Fix stacktrace issue on i686
    case $(uname -m) in
        i?86)
            sed -e "s/defined(__MINGW32__)/& || defined(__i386__)/" \
                -i ./libs/stacktrace/src/exception_headers.h ;;
    esac

    ./bootstrap.sh --prefix=/usr --with-python=python3
}

build() {
    ./b2 stage -j${IGOS_JOBS} threading=multi link=shared
}

do_install() {
    # Remove old cmake files before installing
    rm -rf "${DESTDIR}/usr/lib/cmake/[Bb]oost"* 2>/dev/null || true

    ./b2 --prefix="${DESTDIR}/usr" install threading=multi link=shared
}
