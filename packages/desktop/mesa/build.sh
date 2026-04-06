#!/bin/bash
# mesa 25.3.5 — OpenGL, Vulkan, and OpenCL implementation
# BLFS 13.0

configure() {
    # Note: xdemos patch skipped — causes meson "already visited" error
    # in Mesa 25.x where xdemos are included by default

    mkdir build
    cd    build

    meson setup ..                 \
          --prefix=/usr            \
          --libdir=/usr/lib        \
          --buildtype=release      \
          --wrap-mode=nofallback   \
          -D platforms=x11,wayland \
          -D gallium-drivers=auto  \
          -D vulkan-drivers=amd,intel,swrast,virtio \
          -D valgrind=disabled     \
          -D video-codecs=all      \
          -D libunwind=disabled
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
