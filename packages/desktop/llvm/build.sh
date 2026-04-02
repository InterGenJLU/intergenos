#!/bin/bash
# llvm 19.1.7 — LLVM compiler infrastructure
# BLFS 13.0

configure() {
    cmake -B build                    \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release  \
          -DCMAKE_INSTALL_PREFIX=/usr \
          -DCMAKE_BUILD_TYPE=Release \
          -DLLVM_ENABLE_PROJECTS=clang \
          -DLLVM_ENABLE_RTTI=ON \
          -DLLVM_BUILD_LLVM_DYLIB=ON \
          -DLLVM_LINK_LLVM_DYLIB=ON \
          -DLLVM_TARGETS_TO_BUILD=host;AMDGPU;BPF \
          -DCLANG_DEFAULT_PIE_ON_LINUX=ON
}

build() {
    cmake --build build -j${IGOS_JOBS}
}

do_install() {
    DESTDIR="$DESTDIR" cmake --install build
}
