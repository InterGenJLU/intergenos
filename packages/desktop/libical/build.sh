#!/bin/bash
# libical 3.0.20 — iCalendar protocol implementation
# BLFS 13.0

configure() {
    cmake -B build                    \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5  \
          -DSHARED_ONLY=yes \
          -DICAL_BUILD_DOCS=false \
          -DICAL_GLIB_VAPI=true \
          -DGOBJECT_INTROSPECTION=true
}

build() {
    cmake --build build -j1
}

do_install() {
    DESTDIR="$DESTDIR" cmake --install build
}
