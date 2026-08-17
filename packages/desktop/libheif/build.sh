#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# libheif 1.21.2 — HEIF and AVIF file format decoder and encoder
# BLFS 13.0

configure() {
    set -e
    # InterGenOS patches — applied from packages/desktop/libheif/patches/.
    # The build environment sets IGOS_PACKAGE_DIR to the package recipe
    # directory; fall back to the canonical workspace path if unset (some
    # surgical-rebuild invocations don't propagate it).
    local patches_dir="${IGOS_PACKAGE_DIR:-/mnt/intergenos/packages/desktop/libheif}/patches"
    if [ -d "$patches_dir" ]; then
        for p in "$patches_dir"/*.patch; do
            [ -f "$p" ] || continue
            echo "Applying patch: $(basename "$p")"
            patch -p1 -i "$p"
        done
    fi

    mkdir -p build
    cd    build

    # Codec enablement (post-burn sweep, decided "we NEED these codecs").
    # libheif's CMakeLists.txt gates each codec's find_package() behind a
    # WITH_<codec> option. For DAV1D / SvtEnc / JPEG / OpenJPEG those options
    # default OFF (upstream plugin_option defaultEnabled=OFF at CMakeLists.txt
    # :214,237,251-252,259-260) — unlike the core codecs libde265/x265/x264/aom
    # which default ON. With the options unset, find_package() was never even
    # invoked for these four despite them being declared build deps that ship
    # their .pc / CMake-config correctly, so the configure summary printed them
    # as "- disabled" and the shipped libheif silently lacked AV1/JPEG/JPEG2000.
    # Enable each as a BUILT-IN (WITH_*_PLUGIN=OFF → compiled in, not a dlopen
    # plugin), then hard-verify below.
    cmake -D CMAKE_INSTALL_PREFIX=/usr \
          -D CMAKE_BUILD_TYPE=Release  \
          -D CMAKE_POLICY_VERSION_MINIMUM=3.5 \
          -D WITH_GDK_PIXBUF=OFF       \
          -D WITH_OpenH264_DECODER=OFF \
          -D WITH_DAV1D=ON              -D WITH_DAV1D_PLUGIN=OFF            \
          -D WITH_SvtEnc=ON             -D WITH_SvtEnc_PLUGIN=OFF           \
          -D WITH_JPEG_DECODER=ON       -D WITH_JPEG_DECODER_PLUGIN=OFF     \
          -D WITH_JPEG_ENCODER=ON       -D WITH_JPEG_ENCODER_PLUGIN=OFF     \
          -D WITH_OpenJPEG_DECODER=ON   -D WITH_OpenJPEG_DECODER_PLUGIN=OFF \
          -D WITH_OpenJPEG_ENCODER=ON   -D WITH_OpenJPEG_ENCODER_PLUGIN=OFF \
          -G Ninja .. 2>&1 | tee cmake-configure.log
    local cmake_rc=${PIPESTATUS[0]}
    [ "$cmake_rc" -eq 0 ] || { echo "FATAL: libheif cmake configure failed (rc=$cmake_rc)" >&2; exit 1; }

    # Make absence LOUD. libheif has no global "fail if a requested codec is
    # missing" switch: with WITH_<codec>=ON but the dep undetected it prints
    # "- not found" and silently continues. These codecs are declared build
    # deps, so anything short of "+ built-in" is a real silent loss — HALT.
    local codec
    for codec in "Dav1d AV1 decoder" "SVT AV1 encoder" "JPEG decoder" \
                 "JPEG encoder" "OpenJPEG J2K decoder" "OpenJPEG J2K encoder"; do
        if ! grep -Eq "^[[:space:]]*(-- )?${codec}[[:space:]]*:[[:space:]]*[+] built-in" cmake-configure.log; then
            echo "FATAL: libheif codec '${codec}' is not built-in — a declared build dep was not detected by CMake." >&2
            grep -E "^[[:space:]]*(-- )?${codec}[[:space:]]*:" cmake-configure.log >&2 \
                || echo "  (no configure-summary line found for '${codec}')" >&2
            exit 1
        fi
    done
}

build() {
    set -e
    cd build
    ninja
}

check() {
    set -e
    cd build
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        ninja test
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install
}
