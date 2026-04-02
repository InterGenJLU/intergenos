#!/bin/bash
# llvm 21.1.8 — LLVM compiler infrastructure
# BLFS 13.0
# Note: requires clang, cmake-modules, and third-party tarballs in sources dir

pre_configure() {
    # Extract additional required tarballs
    tar -xf "${IGOS_SOURCES_DIR}/llvm-cmake-${version}.src.tar.xz"
    tar -xf "${IGOS_SOURCES_DIR}/llvm-third-party-${version}.src.tar.xz"

    # Fix paths to extracted cmake and third-party directories
    sed "/LLVM_COMMON_CMAKE_UTILS/s@../cmake@cmake-${version}.src@" \
        -i CMakeLists.txt
    sed "/LLVM_THIRD_PARTY_DIR/s@../third-party@third-party-${version}.src@" \
        -i cmake/modules/HandleLLVMOptions.cmake

    # Extract clang into the source tree
    tar -xf "${IGOS_SOURCES_DIR}/clang-${version}.src.tar.xz" -C tools
    mv tools/clang-${version}.src tools/clang

    # Extract compiler-rt if available
    if [ -f "${IGOS_SOURCES_DIR}/compiler-rt-${version}.src.tar.xz" ]; then
        tar -xf "${IGOS_SOURCES_DIR}/compiler-rt-${version}.src.tar.xz" -C projects
        mv projects/compiler-rt-${version}.src projects/compiler-rt
    fi

    # Fix Python scripts to use python3
    grep -rl '#!.*python' | xargs sed -i '1s/python$/python3/'

    # Ensure FileCheck is installed (needed by rust test suite and others)
    sed 's/utility/tool/' -i utils/FileCheck/CMakeLists.txt
}

configure() {
    pre_configure

    mkdir -v build
    cd       build

    CC=gcc CXX=g++                                   \
    cmake -D CMAKE_INSTALL_PREFIX=/usr               \
          -D CMAKE_SKIP_INSTALL_RPATH=ON             \
          -D LLVM_ENABLE_FFI=ON                      \
          -D CMAKE_BUILD_TYPE=Release                \
          -D LLVM_BUILD_LLVM_DYLIB=ON                \
          -D LLVM_LINK_LLVM_DYLIB=ON                 \
          -D LLVM_ENABLE_RTTI=ON                     \
          -D LLVM_TARGETS_TO_BUILD="host;AMDGPU"     \
          -D LLVM_BINUTILS_INCDIR=/usr/include       \
          -D LLVM_INCLUDE_BENCHMARKS=OFF             \
          -D CLANG_DEFAULT_PIE_ON_LINUX=ON           \
          -D CLANG_CONFIG_FILE_SYSTEM_DIR=/etc/clang \
          -W no-dev -G Ninja ..
}

build() {
    cd build
    ninja
}

do_install() {
    cd build
    DESTDIR="$DESTDIR" ninja install
}

post_install() {
    # Create clang SSP configuration files
    mkdir -pv /etc/clang
    for i in clang clang++; do
        echo -fstack-protector-strong > /etc/clang/$i.cfg
    done
}
