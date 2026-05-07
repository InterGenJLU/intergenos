#!/bin/bash
# shaderc 2026.1 — Google GLSL/HLSL to SPIR-V shader compiler (glslc)
# BLFS 13.0

configure() {
    set -e
    # Use system glslang and spirv-tools per BLFS
    sed '/build-version/d'   -i glslc/CMakeLists.txt
    sed '/third_party/d'     -i CMakeLists.txt
    sed 's|SPIRV|glslang/&|' -i libshaderc_util/src/compiler.cc

    echo '"2026.1"' > glslc/src/build-version.inc

    mkdir build
    cd    build

    cmake -D CMAKE_INSTALL_PREFIX=/usr \
          -D CMAKE_BUILD_TYPE=Release  \
          -D SHADERC_SKIP_TESTS=ON     \
          -G Ninja ..
}

build() {
    set -e
    cd build
    ninja glslc/glslc
}

do_install() {
    set -e
    install -v -m755 -d "${DESTDIR}/usr/bin"
    install -v -m755 build/glslc/glslc "${DESTDIR}/usr/bin"
}
