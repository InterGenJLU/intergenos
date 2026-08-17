#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2026 InterGenJLU
#
# rocprofiler-sdk 7.2.4 — profiling SDK + rocprofv3
# Source: projects/rocprofiler-sdk inside rocm-systems
#
# Tests/samples/docs stay off; libelf resolves from core elfutils and
# libdw from elfutils-libdw (core elfutils is LFS-exact, libelf only).
#
# HERMETICITY (see package.yml source comment): the tag archive ships
# external/ as empty gitlink dirs and configure git-fetches them. The 7
# staged commit archives are extracted below BEFORE cmake runs; the
# checkout helper returns early once <dir>/<TEST_FILE> exists
# (CMakeLists.txt; meson.build for perfetto), so configure never touches
# git. GHC_FS/FMT/GLOG/PYBIND11 flip OFF → else-branches
# find_package(... REQUIRED) against the system fmt/glog/pybind11 and
# std::filesystem. gotcha stays ON (no system package — vendored build).

# Patch source dir: resolve this script's own location (works under both the
# driver's IGOS_PACKAGE_DIR and a bare source of build.sh).
BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    set -e
    export ROCM_PATH=/opt/rocm

    cd projects/rocprofiler-sdk

    # cpack_add_component_group is called before upstream's include(CPack)
    # ever runs (packaging config line ~134 vs 243) — undefined command on
    # vanilla CMake 4.3. The patch loads CPackComponent at the top of the
    # packaging config.
    patch -p1 < "${IGOS_PACKAGE_DIR:-$BUILD_DIR}/patches/0001-packaging-include-cpackcomponent-before-use.patch"

    # Rule 5 — every secondary source tarball extracted explicitly.
    local sub
    for sub in ptl-48df4162:ptl \
               yaml-cpp-1d8ca1f3:yaml-cpp \
               cereal-40a30def:cereal \
               elfio-8ae6cec5:elfio \
               json-e41905fc:json \
               perfetto-eb5ef24c:perfetto \
               gotcha-b944da10:gotcha \
               fmt-0bffed89:fmt; do
        local tarball="${sub%%:*}" dest="${sub##*:}"
        mkdir -p "external/${dest}"
        tar -xzf "${IGOS_SOURCES}/${tarball}.tar.gz" \
            --strip-components=1 -C "external/${dest}"
    done
    # Fail loudly if any extraction missed its checkout sentinel — a
    # missing TEST_FILE would silently re-open the git-fetch path.
    local d
    for d in ptl yaml-cpp cereal elfio json gotcha fmt; do
        [ -f "external/${d}/CMakeLists.txt" ] || {
            echo "FATAL: external/${d}/CMakeLists.txt absent after staging" >&2
            return 1
        }
    done
    [ -f external/perfetto/meson.build ] || {
        echo "FATAL: external/perfetto/meson.build absent after staging" >&2
        return 1
    }

    # GCC-15 transitive-include class in the vendored trees — apply
    # after the extraction above.
    patch -p1 < "${IGOS_PACKAGE_DIR:-$BUILD_DIR}/patches/0002-yaml-cpp-emitterutils-include-cstdint.patch"
    patch -p1 < "${IGOS_PACKAGE_DIR:-$BUILD_DIR}/patches/0003-elfio-elf-types-include-cstdint.patch"
    patch -p1 < "${IGOS_PACKAGE_DIR:-$BUILD_DIR}/patches/0004-sdk-add-missing-fstream-includes.patch"
    patch -p1 < "${IGOS_PACKAGE_DIR:-$BUILD_DIR}/patches/0005-sdk-add-missing-array-includes.patch"

    # otf2 is a FetchContent URL download (external/otf2/CMakeLists.txt),
    # not a gitlink — pre-stage the declared tarball and point
    # FETCHCONTENT_SOURCE_DIR_OTF2-SOURCE at it so configure never
    # touches the network. Sha verified at source staging (upstream's
    # own OTF2_URL_HASH pin).
    mkdir -p external/otf2-prestaged
    tar -xzf "${IGOS_SOURCES}/otf2-3.0.3.tar.gz" \
        --strip-components=1 -C external/otf2-prestaged
    [ -f external/otf2-prestaged/configure ] || {
        echo "FATAL: external/otf2-prestaged/configure absent after staging" >&2
        return 1
    }

    mkdir -p build

    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DCMAKE_PREFIX_PATH=/opt/rocm \
        -DHIP_PLATFORM=amd \
        -DROCPROFILER_BUILD_TESTS=OFF \
        -DROCPROFILER_BUILD_SAMPLES=OFF \
        -DROCPROFILER_BUILD_DOCS=OFF \
        -DROCPROFILER_BUILD_GHC_FS=OFF \
        -DROCPROFILER_BUILD_GLOG=OFF \
        -DROCPROFILER_BUILD_PYBIND11=OFF \
        "-DFETCHCONTENT_SOURCE_DIR_OTF2-SOURCE=$(pwd)/external/otf2-prestaged" \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    export ROCM_PATH=/opt/rocm
    cd projects/rocprofiler-sdk
    # The otf2-build ExternalProject runs `make install -s` at BUILD time
    # into its in-tree prefix (build/external/otf2). The builder exports
    # DESTDIR for the package lifecycle, which redirects that install
    # into the staging dir and the SDK compile then can't find
    # otf2/*.h — strip it from the build phase only (do_install keeps
    # its own DESTDIR).
    env -u DESTDIR cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    cd projects/rocprofiler-sdk
    DESTDIR="$DESTDIR" cmake --install build
}
