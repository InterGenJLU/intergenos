#!/bin/bash
# libical 3.0.20 — iCalendar protocol implementation
# BLFS 13.0

configure() {
    cmake -B build                    \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release  \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DBUILD_SHARED_LIBS=ON \
          -DGOBJECT_INTROSPECTION=true
}

build() {
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    DESTDIR="$DESTDIR" cmake --install build
}
