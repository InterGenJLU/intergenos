#!/bin/bash
# glslang 16.2.0 — GLSL/HLSL to SPIR-V compiler
# BLFS 13.0

configure() {
    cmake -B build                    \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release  \
          -DBUILD_SHARED_LIBS=ON \
          -DALLOW_EXTERNAL_SPIRV_TOOLS=ON
}

build() {
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    DESTDIR="$DESTDIR" cmake --install build
}
