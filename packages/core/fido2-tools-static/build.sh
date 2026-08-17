#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# fido2-tools-static 1.17.0 — Statically-linked libfido2 tools subset for
# initramfs FIDO2-token LUKS unlock (D-001 EXPERIMENTAL).
#
# Distinct from packages/desktop/libfido2 (dynamic, system-wide lib + tools):
# this produces statically-linked binaries that live inside the FDE
# initramfs envelope where no dynamic loader is present.
#
# Architecture (verified against libfido2 1.17.0 CMakeLists.txt + Alpine
# main/libfido2 APKBUILD + Fedora .spec + Arch PKGBUILD FIDO2 hook
# precedent — NOTE: those distros wire FIDO2 via runtime initramfs
# generators we do NOT use; dracut/mkinitcpio RATIFIED-AGAINST.
# InterGenOS wires FIDO2 via our custom FDE-initramfs builder, D-001 I-D):
#
#   - libcbor built static (CBOR encoding/decoding — libfido2 dep).
#   - libfido2 built "mostly-static" + with tools, linked against:
#       * staging libcbor.a (static)
#       * system /usr/lib/libcrypto.a + libssl.a (openssl precursor fix
#         retained the static archives)
#       * system /usr/lib/libz.a (static)
#       * system libudev.so.1 (DYNAMIC — libudev only ships as a .so;
#         systemd meson `static-libudev` is opt-in and not enabled in our
#         packages/core/systemd build). NO distro ships fully-static
#         FIDO2 tools because of this; every shipping distro pairs the
#         static libfido2.a + dynamic libudev.so. The FDE initramfs
#         builder (D-001/I-D) bundles libudev.so.1 alongside the binaries
#         to match this pattern.
#   - Tools shipped: fido2-cred (enrollment) + fido2-assert (replay at
#     unlock) + fido2-token (recovery-shell management).
#
# Output: /usr/lib/intergen/fido2-tools-static/{fido2-cred, fido2-assert,
#         fido2-token}
#
# NOTE — DO NOT add -DCMAKE_EXE_LINKER_FLAGS="-static". libudev has no
# static archive available, so a fully-static link halts with "cannot find
# -ludev". The mostly-static build (drop the flag) is the only viable path
# without sourcing/building eudev or modifying systemd's meson options.
# This matches the upstream-canonical LINKING pattern (Alpine, Fedora,
# Arch — verified across all three). Their respective initramfs
# generators (dracut/mkinitcpio) are RATIFIED-AGAINST in InterGenOS;
# only the linking pattern is mirrored, into our custom FDE-initramfs
# builder.

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

    # Defensive env-var unset to prevent inheritance from prior packages
    # in the same chroot-build-core-extra.sh shell. Sibling static-binary
    # packages (cryptsetup-static, tpm2-tools-static) build earlier in the
    # same shell and export LDFLAGS/CFLAGS/PKG_CONFIG_PATH/CRYPTO_LIBS
    # pointing at their own per-package staging dirs (resolved via
    # ${PWD}/../staging-static). After install, the orchestrator cleans
    # up their per-package build dirs; any leaked env-var still pointing
    # at the now-deleted relative path breaks our link step with
    # "cannot find -lcbor" or similar. Caught empirically 2026-05-23
    # 10:01 CDT when tpm2-tools-static finally built successfully and
    # left stale LDFLAGS in the shell.
    unset LDFLAGS CFLAGS PKG_CONFIG_PATH CRYPTO_LIBS

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

    # === 2. libfido2 mostly-static + tools ===
    #
    # Flag rationale (verified against libfido2 1.17.0 CMakeLists.txt):
    # BUILD_SHARED_LIBS=OFF + BUILD_STATIC_LIBS=ON → static libfido2.a only
    # BUILD_TOOLS=ON → produces fido2-cred + fido2-assert + fido2-token
    #                   (tools link against libfido2.a per tools/CMakeLists.txt
    #                   when BUILD_SHARED_LIBS=OFF + BUILD_STATIC_LIBS=ON)
    # BUILD_MANPAGES=OFF → no asciidoc dep
    # BUILD_EXAMPLES=OFF → skip example apps (we only need tools)
    # FUZZ=OFF → drop fuzz harness
    # NFC_LINUX=OFF → drop NFC support (not used for FDE FIDO2 unlock)
    # USE_HIDAPI=OFF → use Linux-native USB HID via libudev (NOTE: on Linux
    #                   libudev is UNCONDITIONAL per CMakeLists.txt line 248
    #                   pkg_search_module(UDEV libudev REQUIRED); USE_HIDAPI=ON
    #                   would be additive, not a replacement)
    #
    # NO -DCMAKE_EXE_LINKER_FLAGS="-static" — libudev only ships as .so so
    # full-static link halts with "cannot find -ludev". Mostly-static
    # (static libfido2.a + libcbor.a + libcrypto.a + libssl.a + libz.a +
    # dynamic libudev.so.1) is the upstream-canonical pattern used by
    # every shipping distro for FIDO2 tools.
    echo "[fido2-tools-static] Building libfido2-${version} mostly-static + tools..."
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
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_C_FLAGS="-I${STAGING_DIR}/include"
    make -j"${IGOS_JOBS}"
}

check() {
    set -e

    # Mostly-static reality check (matches the empirical pattern we
    # established for tpm2-tools-static at packages/core/tpm2-tools-static/
    # build.sh check()). The original whitelist (libudev only) assumed
    # libcrypto + libssl + libz would be picked from staging .a archives,
    # but libtool prefers .so over .a in the same -L path when no -static
    # flag is forced for the executable link. Confirmed empirically
    # 2026-05-23 10:06 CDT after the env-var leak fix (commit 6d691d8e):
    # fido2-cred ldd shows libcrypto.so.3 + libz.so.1 + libc.so.6 dynamic
    # alongside the libudev.so.1 we always expected.
    #
    # Per D-001 D-OPTION-A 2026-05-23, accept the dynamic DSO chain as the
    # cost of EXPERIMENTAL FIDO2 unlock; build-fde-initramfs.sh's ldd loop
    # (extended in commit 87d4903d to cover both fido2 + tpm2 binaries)
    # auto-bundles these into the FDE initramfs envelope. Anything OUTSIDE
    # the whitelist still FAILS the check — catches future libfido2
    # upstream additions that would bloat the chain silently.
    local build_dir="${PWD}/build"
    for verb in fido2-cred fido2-assert fido2-token; do
        local bin
        bin="$(find "${build_dir}" -name "${verb}" -type f -executable | head -1)"
        if [ -z "${bin}" ]; then
            echo "FAIL: ${verb} not found in build tree" >&2
            return 1
        fi
        local deps
        deps="$(ldd "${bin}" 2>&1 || true)"
        local unexpected
        unexpected="$(echo "${deps}" | grep -E '=>' | grep -vE 'libc\.so|libdl\.so|libpthread\.so|libudev\.so|libm\.so|libresolv\.so|libssl\.so|libcrypto\.so|libz\.so|linux-vdso|ld-linux' || true)"
        if [ -n "${unexpected}" ]; then
            echo "FAIL: ${verb} has DSO deps OUTSIDE the whitelist:" >&2
            echo "${unexpected}" >&2
            echo "" >&2
            echo "Full ldd output for diagnosis:" >&2
            echo "${deps}" >&2
            return 1
        fi
        echo "PASS: ${verb} DSO deps within EXPERIMENTAL whitelist"
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
