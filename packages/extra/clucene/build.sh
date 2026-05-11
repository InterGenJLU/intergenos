#!/bin/bash
# clucene 2.3.3.4 — C++ port of Lucene high performance text search engine
# BLFS 13.0

configure() {
    set -e
    # Patch applied by builder PATCH phase (package.yml) with SHA256 validation.

    # BLFS: fix missing ctime include
    sed -i '/Misc.h/a #include <ctime>' src/core/CLucene/document/DateTools.cpp

    mkdir -p build
    cd    build

    cmake -D CMAKE_INSTALL_PREFIX=/usr        \
          -D CMAKE_POLICY_VERSION_MINIMUM=3.5 \
          -D BUILD_CONTRIBS_LIB=ON            \
          -W no-dev ..
}

build() {
    set -e
    cd build
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" make install
}
