#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# amdsmi 7.2.4 — AMD SMI library + amd-smi CLI
# Source: projects/amdsmi inside rocm-systems, + the pinned
# esmi_ib_library tarball (source[1], Rule-5 explicit extraction).
#
# Patch 0001 (git-tracked; dry-run verified against the pinned tarball
# at authoring) removes the
# upstream unpinned configure-time `git clone` of esmi_ib_library and
# fail-closes on the staged copy instead — hermetic build, capability
# retained. ENABLE_LDCONFIG=OFF: the install step must never mutate the
# build host's linker cache (DESTDIR discipline); /opt/rocm/lib is
# already on the shipped loader path via rocm-hip's
# /etc/ld.so.conf.d/rocm.conf.

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    set -e

    # Rule 5: source[1] is extracted explicitly, into the exact directory
    # the (patched) CMakeLists asserts on.
    tar -xzf "${IGOS_SOURCES}/esmi_ib_library-esmi_pkg_ver-4.2.tar.gz" \
        -C projects/amdsmi
    mv projects/amdsmi/esmi_ib_library-esmi_pkg_ver-4.2 projects/amdsmi/esmi_ib_library
    [ -d projects/amdsmi/esmi_ib_library/src ] || {
        echo "FATAL: staged esmi_ib_library has no src/ - wrong tarball layout" >&2
        exit 1
    }

    cd projects/amdsmi
    patch -p1 < "${IGOS_PACKAGE_DIR:-$BUILD_DIR}/patches/0001-esmi-offline-staging.patch"
    # The vendored include/amd_smi/impl/amdgpu_drm.h redefines
    # drm_color_ctm_3x4 against libdrm >= 2.4.126's drm_mode.h (identical
    # layout, duplicate definition). Fix = the upstream/vendor one (also
    # shipped by Debian as amdsmi 0005-Fix-compilation-against-newer-
    # libdrm.patch): include the system <libdrm/amdgpu_drm.h> first, so
    # the vendored copy no-ops on its identical __AMDGPU_DRM_H__ guard.
    patch -p1 < "${IGOS_PACKAGE_DIR:-$BUILD_DIR}/patches/0002-include-system-amdgpu-drm-before-vendored-copy.patch"

    mkdir -p build
    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DCMAKE_PREFIX_PATH=/opt/rocm \
        -DBUILD_TESTS=OFF \
        -DBUILD_EXAMPLES=OFF \
        -DENABLE_ESMI_LIB=ON \
        -DENABLE_LDCONFIG=OFF \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    cd projects/amdsmi
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    cd projects/amdsmi
    DESTDIR="$DESTDIR" cmake --install build
}
