#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# rocprofiler-register 7.2.4 — profiler registration shim
# Source: projects/rocprofiler-register inside the rocm-systems monorepo
#
# A small library the HIP runtime (and rocr) dlopen-register against so
# an external profiler can attach. hipamd's configure hard-requires its
# CMake package (hipamd/src/CMakeLists.txt:288 find_package REQUIRED) —
# surfaced live at the first rocm-hip chroot build. Tests/samples off:
# the shim's build tests pull GoogleTest via FetchContent (offline-fatal
# in the chroot) and are not part of the shipped surface.
#
# BUILD_GLOG/BUILD_FMT OFF: the bundled path is an unpinned git-submodule
# clone (fatal on a tarball build — no .git); the system fmt + glog
# packages satisfy the find_package(REQUIRED) branch instead (see
# package.yml rationale).

configure() {
    set -e
    cd projects/rocprofiler-register

    # Upstream latent ordering bug, exposed by the system-glog build: the
    # packaging module calls cpack_add_component_group (line 84) before its
    # own include(CPack) (line 199). The BUNDLED glog's CMakeLists.txt:27
    # include(CPack) used to define the macro globally and masked this;
    # include CPackComponent explicitly instead — probe-proven on the
    # chroot's cmake 4.3.1 (decided 2026-07-16). Idempotent + fail-loud.
    grep -q '^include(CPackComponent)' cmake/rocprofiler_register_config_packaging.cmake || \
        sed -i '1i include(CPackComponent)' cmake/rocprofiler_register_config_packaging.cmake
    grep -q '^include(CPackComponent)' cmake/rocprofiler_register_config_packaging.cmake || \
        { echo "FATAL: CPackComponent patch did not apply"; exit 1; }

    # Same latent-class sibling (fmt 11+ turned fmt/core.h into a base.h
    # alias that no longer provides fmt::format): migrate the three files
    # that reach fmt::format through fmt/core.h to fmt/format.h — the
    # documented fmt migration, probe-proven against the system fmt 12.2.0
    # (full build + install green in the chroot scratch, decided 2026-07-16).
    # Idempotent + fail-loud.
    sed -i 's|include <fmt/core.h>|include <fmt/format.h>|; s|include "fmt/core.h"|include "fmt/format.h"|' \
        source/lib/rocprofiler-register/details/dl.cpp \
        source/lib/rocprofiler-register/details/scope_destructor.hpp \
        source/lib/rocprofiler-register/details/environment.hpp
    if grep -rq 'fmt/core\.h' source/lib/rocprofiler-register/; then
        echo "FATAL: fmt/core.h migration did not apply cleanly"; exit 1
    fi

    mkdir -p build

    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DCMAKE_PREFIX_PATH=/opt/rocm \
        -DROCPROFILER_REGISTER_BUILD_TESTS=OFF \
        -DROCPROFILER_REGISTER_BUILD_SAMPLES=OFF \
        -DROCPROFILER_REGISTER_BUILD_GLOG=OFF \
        -DROCPROFILER_REGISTER_BUILD_FMT=OFF \
        -DCMAKE_FIND_PACKAGE_TARGETS_GLOBAL=ON \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    cd projects/rocprofiler-register
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    cd projects/rocprofiler-register
    DESTDIR="$DESTDIR" cmake --install build
}
