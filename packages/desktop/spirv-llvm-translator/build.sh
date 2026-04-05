#!/bin/bash
# spirv-llvm-translator 21.1.4 — SPIR-V to LLVM IR translator
# BLFS 13.0

configure() {
    cmake -B build                                          \
          -DCMAKE_INSTALL_PREFIX=/usr                       \
          -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5                        \
          -DBUILD_SHARED_LIBS=ON                            \
          -DCMAKE_SKIP_INSTALL_RPATH=ON                     \
          -DLLVM_EXTERNAL_SPIRV_HEADERS_SOURCE_DIR=/usr
}

build() {
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    DESTDIR="$DESTDIR" cmake --install build
}
