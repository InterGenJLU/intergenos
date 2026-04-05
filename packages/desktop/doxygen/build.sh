#!/bin/bash
# doxygen 1.16.1 — Documentation generation tool
# BLFS 13.0

configure() {
    # BLFS: fix python shebangs
    grep -rl '^#!.*python$' | xargs sed -i '1s/python/&3/'

    mkdir build
    cd    build

    cmake -G "Unix Makefiles"          \
          -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5   \
          -DCMAKE_INSTALL_PREFIX=/usr  \
          -Dbuild_wizard=OFF           \
          -Wno-dev ..
}

build() {
    cd build
    make
}

do_install() {
    cd build
    make DESTDIR="$DESTDIR" install
}
