#!/bin/bash
# webkitgtk-gtk3 2.50.5 — Web content engine for GTK (GTK-3 version)
# BLFS 13.0

configure() {
    set -e
    mkdir -vp build
    cd        build

    cmake -D CMAKE_BUILD_TYPE=Release         \
          -D CMAKE_INSTALL_PREFIX=/usr        \
          -D CMAKE_POLICY_VERSION_MINIMUM=3.5 \
          -D CMAKE_SKIP_INSTALL_RPATH=ON      \
          -D PORT=GTK                         \
          -D LIB_INSTALL_DIR=/usr/lib         \
          -D USE_LIBBACKTRACE=OFF             \
          -D USE_LIBHYPHEN=OFF                \
          -D ENABLE_GAMEPAD=OFF               \
          -D ENABLE_MINIBROWSER=ON            \
          -D ENABLE_DOCUMENTATION=OFF         \
          -D USE_WOFF2=OFF                    \
          -D USE_GTK4=OFF                     \
          -D ENABLE_WEBDRIVER=OFF             \
          -D ENABLE_BUBBLEWRAP_SANDBOX=ON     \
          -D USE_SYSPROF_CAPTURE=NO           \
          -D ENABLE_SPEECH_SYNTHESIS=OFF      \
          -W no-dev -G Ninja ..
}

build() {
    set -e
    cd build
    # Limit parallelism — WebCore unified sources each use ~2GB RAM.
    # 16 parallel jobs on 32GB RAM triggers OOM killer.
    local jobs=${IGOS_JOBS}
    [ "$jobs" -gt 8 ] && jobs=8
    ninja -j${jobs}
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install
}
