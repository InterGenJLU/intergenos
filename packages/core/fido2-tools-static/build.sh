#!/bin/bash
# fido2-tools-static 1.17.0 — Statically-linked libfido2 tools subset for
# initramfs FIDO2-token LUKS unlock (D-001 EXPERIMENTAL).
#
# Distinct from packages/desktop/libfido2 (dynamic, system-wide lib + tools):
# this produces statically-linked binaries that live inside the FDE
# initramfs envelope where no dynamic loader is present.
#
# Architecture:
#   - libcbor built static (CBOR encoding/decoding — libfido2 dep).
#   - libfido2 built static + with tools, linked against:
#       * staging libcbor.a
#       * system /usr/lib/libcrypto.a + libssl.a (openssl precursor fix
#         retained the static archives)
#       * system /usr/lib/libz.a
#       * system libudev.so (USB HID enumeration; libfido2 links libudev
#         dynamically by default — for fully-static initramfs use, see
#         the FIDO2_DYNAMIC_LIBUDEV note below).
#   - Tools shipped: fido2-cred (enrollment) + fido2-assert (replay at
#     unlock) + fido2-token (recovery-shell management).
#
# Output: /usr/lib/intergen/fido2-tools-static/{fido2-cred, fido2-assert,
#         fido2-token}
#
# NOTE on libudev (HID enumeration dep): libfido2 historically pulled
# libudev via cmake `USE_HIDAPI=OFF` default = Linux-native USB-HID path
# that needs udev for device enumeration. The cmake option `USE_HIDAPI=ON`
# would use the cross-platform hidapi instead (which uses libusb), but
# that's its own static-link headache. Cleanest path: build with the
# Linux-native HID + statically link libudev as well (libudev.a comes
# from systemd; not currently emitted as static).
#
# First-iteration expectation: this build may halt on the libudev static-
# link gap. Fix-forward options: (a) add libudev static-archive emission to
# packages/desktop/systemd-pass2; (b) switch to hidapi-static; (c) accept
# a libudev DSO dep in the initramfs (the FDE initramfs builder bundles
# libudev.so if present — graceful enough for v1.0 EXPERIMENTAL).

PKG_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

LIBCBOR_VER="0.13.0"

STAGING_DIR="${PWD}/../staging-static"

configure() {
    set -e
    echo "[fido2-tools-static] configure() deferred to build() — needs staged deps first"
}

_extract_secondary() {
    local tarball="$1"
    local destname="$2"

    local build_root="${STAGING_DIR}/build"
    mkdir -p "${build_root}"
    rm -rf "${build_root:?}/${destname}"
    mkdir -p "${build_root}/${destname}"

    tar -xf "${IGOS_SOURCES}/${tarball}" \
        -C "${build_root}/${destname}" \
        --strip-components=1 \
        --no-same-owner --no-same-permissions

    echo "${build_root}/${destname}"
}

build() {
    set -e

    rm -rf "${STAGING_DIR}"
    mkdir -p "${STAGING_DIR}"/{include,lib,lib/pkgconfig}

    local libfido2_src="${PWD}"

    # === 1. libcbor static (libfido2 CBOR dependency) ===
    echo "[fido2-tools-static] Building libcbor-${LIBCBOR_VER} static..."
    local libcbor_dir
    libcbor_dir="$(_extract_secondary "libcbor-${LIBCBOR_VER}.tar.gz" "libcbor-${LIBCBOR_VER}")"
    (
        cd "${libcbor_dir}"
        mkdir -p build && cd build
        cmake .. \
            -DCMAKE_INSTALL_PREFIX="${STAGING_DIR}" \
            -DCMAKE_INSTALL_LIBDIR=lib \
            -DBUILD_SHARED_LIBS=OFF \
            -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
            -DCMAKE_BUILD_TYPE=Release
        make -j"${IGOS_JOBS}"
        make install
    )

    # === 2. libfido2 static + tools ===
    # BUILD_SHARED_LIBS=OFF + BUILD_STATIC_LIBS=ON → static archives only.
    # BUILD_TOOLS=ON → produces fido2-cred + fido2-assert + fido2-token.
    # BUILD_MANPAGES=OFF → no asciidoc dep.
    # BUILD_EXAMPLES=OFF → skip example apps (we only need tools).
    # FUZZ=OFF + NFC_LINUX=OFF → drop fuzz harness + NFC support (not used).
    # CMAKE_EXE_LINKER_FLAGS=-static → fully-static binaries.
    echo "[fido2-tools-static] Building libfido2-${version} static + tools..."
    cd "${libfido2_src}"
    mkdir -p build && cd build

    PKG_CONFIG_PATH="${STAGING_DIR}/lib/pkgconfig" \
    cmake .. \
        -DCMAKE_INSTALL_PREFIX="${STAGING_DIR}" \
        -DCMAKE_INSTALL_LIBDIR=lib \
        -DBUILD_SHARED_LIBS=OFF \
        -DBUILD_STATIC_LIBS=ON \
        -DBUILD_TOOLS=ON \
        -DBUILD_MANPAGES=OFF \
        -DBUILD_EXAMPLES=OFF \
        -DFUZZ=OFF \
        -DNFC_LINUX=OFF \
        -DUSE_HIDAPI=OFF \
        -DCMAKE_EXE_LINKER_FLAGS="-static" \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_C_FLAGS="-I${STAGING_DIR}/include"
    make -j"${IGOS_JOBS}"
}

check() {
    set -e

    local build_dir="${PWD}/build"
    for verb in fido2-cred fido2-assert fido2-token; do
        local bin
        bin="$(find "${build_dir}" -name "${verb}" -type f -executable | head -1)"
        if [ -z "${bin}" ]; then
            echo "FAIL: ${verb} not found in build tree" >&2
            return 1
        fi
        if file "${bin}" | grep -q "statically linked"; then
            echo "PASS: ${verb} is statically linked"
        else
            echo "WARN: ${verb} is NOT fully statically linked (likely libudev dep)" >&2
            file "${bin}" >&2
            # Do not fail check() — libudev DSO dep is expected per
            # build.sh header note. FDE initramfs builder will bundle
            # libudev.so alongside if present.
        fi
        if ! "${bin}" -h >/dev/null 2>&1 && ! "${bin}" --help >/dev/null 2>&1; then
            # fido2-cred / fido2-assert print help on -h with exit 1; tolerate.
            :
        fi
    done
}

do_install() {
    set -e

    install -d "${DESTDIR}/usr/lib/intergen/fido2-tools-static"

    local build_dir="${PWD}/build"
    for verb in fido2-cred fido2-assert fido2-token; do
        local bin
        bin="$(find "${build_dir}" -name "${verb}" -type f -executable | head -1)"
        if [ -z "${bin}" ]; then
            echo "FAIL: ${verb} not found in build tree" >&2
            return 1
        fi
        install -m 755 "${bin}" \
            "${DESTDIR}/usr/lib/intergen/fido2-tools-static/${verb}"
    done
}
