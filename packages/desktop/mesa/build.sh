#!/bin/bash
# mesa 24.3.4 — OpenGL, Vulkan, and OpenCL implementation
# BLFS 13.0

configure() {
    mkdir build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --buildtype=release \
          -Dplatforms=x11,wayland \
          -Dgallium-drivers=radeonsi,nouveau,swrast,iris,zink \
          -Dvulkan-drivers=amd,intel,swrast \
          -Dvulkan-layers=device-select,overlay \
          -Dglx=dri \
          -Degl=enabled \
          -Dllvm=enabled \
          -Dgbm=enabled \
          -Dgles2=enabled \
          -Dvalgrind=disabled
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}
