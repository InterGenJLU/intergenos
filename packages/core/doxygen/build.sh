#!/bin/bash
# doxygen 1.16.1 — Documentation generation tool
# BLFS 13.0

configure() {
    # pipefail: grep | xargs pipe — without pipefail, grep failure
    # would be masked by xargs succeeding on empty input
    set -e -o pipefail
    # BLFS: fix python shebangs
    grep -rl '^#!.*python$' | xargs sed -i '1s/python/&3/'

    mkdir -p build
    cd    build

    cmake -G "Unix Makefiles"          \
          -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5   \
          -DCMAKE_INSTALL_PREFIX=/usr  \
          -Dbuild_wizard=OFF           \
          -Wno-dev ..
}

build() {
    set -e
    cd build
    make
}

do_install() {
    set -e
    cd build
    make DESTDIR="$DESTDIR" install
}
