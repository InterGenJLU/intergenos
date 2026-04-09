#!/bin/bash
# clucene 2.3.3.4 — C++ port of Lucene high performance text search engine
# BLFS 13.0

configure() {
    # BLFS: apply contribs library patch
    patch -Np1 -i "${IGOS_SOURCES}/clucene-2.3.3.4-contribs_lib-1.patch"

    # BLFS: fix missing ctime include
    sed -i '/Misc.h/a #include <ctime>' src/core/CLucene/document/DateTools.cpp

    mkdir build
    cd    build

    cmake -D CMAKE_INSTALL_PREFIX=/usr        \
          -D CMAKE_POLICY_VERSION_MINIMUM=3.5 \
          -D BUILD_CONTRIBS_LIB=ON            \
          -W no-dev ..
}

build() {
    cd build
    make -j${IGOS_JOBS}
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" make install
}
