#!/bin/bash
# vulkan-loader 1.4.341.0 — Vulkan ICD loader
# BLFS 13.0

configure() {
    # BLFS required fixes
    sed "s/'git', 'clone'/\&, '--depth=1', '-b', self.commit/" -i ../scripts/update_deps.py
    cmake -B build                    \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5  
}

build() {
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    DESTDIR="$DESTDIR" cmake --install build
}
