#!/bin/bash
# mesa 25.3.5 — OpenGL, Vulkan, and OpenCL implementation
# BLFS 13.0

configure() {
    set -e
    # Pre-place Rust crate tarballs for offline build.
    # NVK (Nouveau Vulkan) and other Rust-based components require 27 crates
    # from crates.io. Since the chroot has no internet, we pre-download them
    # on the host and archive them as mesa-25.3.5-rust-crates.tar.gz.
    # Meson checks subprojects/packagecache/ before attempting downloads.
    if [ -f "${IGOS_SOURCES}/mesa-25.3.5-rust-crates.tar.gz" ]; then
        mkdir -p subprojects/packagecache
        tar -xf "${IGOS_SOURCES}/mesa-25.3.5-rust-crates.tar.gz" \
            -C subprojects/packagecache
    fi

    mkdir build
    cd    build

    meson setup ..                 \
          --prefix=/usr            \
          --libdir=/usr/lib        \
          --buildtype=release      \
          --wrap-mode=nodownload   \
          -D platforms=x11,wayland \
          -D gallium-drivers=auto  \
          -D vulkan-drivers=auto   \
          -D valgrind=disabled     \
          -D video-codecs=all      \
          -D libunwind=disabled
}

build() {
    set -e
    cd build
    ninja
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install
}
