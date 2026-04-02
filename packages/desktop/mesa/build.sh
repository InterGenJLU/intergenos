#!/bin/bash
# mesa 25.3.5 — OpenGL, Vulkan, and OpenCL implementation
# BLFS 13.0

configure() {
    # Apply xdemos patch if available
    if [ -f "${IGOS_SOURCES_DIR}/mesa-add_xdemos-4.patch" ]; then
        patch -Np1 -i "${IGOS_SOURCES_DIR}/mesa-add_xdemos-4.patch"
    fi

    mkdir build
    cd    build

    meson setup ..                 \
          --prefix=/usr            \
          --buildtype=release      \
          -D platforms=x11,wayland \
          -D gallium-drivers=auto  \
          -D vulkan-drivers=auto   \
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
