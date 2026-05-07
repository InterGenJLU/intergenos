#!/bin/bash
# llama-cpp b5545 — LLM inference engine
# https://github.com/ggml-org/llama.cpp
#
# Builds: llama-server (HTTP API), llama-cli, libllama.so, libggml.so
# Vulkan GPU acceleration auto-detected at build time if headers present.

configure() {
    set -e
    mkdir build
    cd    build

    cmake .. \
        -DCMAKE_INSTALL_PREFIX=/usr \
        -DCMAKE_BUILD_TYPE=Release \
        -DLLAMA_BUILD_SERVER=ON \
        -DLLAMA_CURL=ON \
        -DBUILD_SHARED_LIBS=ON
}

build() {
    set -e
    cd build
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    cd build
    make DESTDIR="$DESTDIR" install
}
