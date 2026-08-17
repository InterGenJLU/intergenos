#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# tpm2-tools-static 5.7 — Statically-linked tpm2-tools subset for initramfs
# TPM2-sealed LUKS unlock (D-001 EXPERIMENTAL).
#
# Distinct from a (future) packages/desktop/tpm2-tools (dynamic, system-wide
# tool): this builds a directory of statically-linked tpm2_* binaries that
# go into the FDE initramfs envelope where no dynamic loader is present.
#
# Architecture:
#   - tpm2-tss built static with --disable-fapi (avoids libcurl + libgcrypt
#     dep needed by Feature API; we only use ESYS layer for seal/unseal).
#   - tpm2-tss built with --with-tcti=device (avoids dlopen of TCTI modules;
#     binary talks directly to /dev/tpmrm0).
#   - tpm2-tools is MOSTLY-STATIC, not fully-static. Upstream INSTALL.md
#     documents no static-binary build path; configure has no equivalent
#     to cryptsetup's --enable-static-cryptsetup; libtool strips -static
#     from LDFLAGS for the executable link.
#   - tpm2-tools 5.x is a SINGLE multicall binary that bundles all ~70
#     verbs into one executable. Even verbs we don't use (tpm2_getek-
#     certificate, FAPI tools) pull their transitive DSO deps — notably
#     the libcurl chain (libcurl + libnghttp2 + libidn2 + libunistring +
#     libssh2 + libpsl) used by EK-cert vendor-URL fetches. --disable-fapi
#     does NOT drop these (empirically validated 2026-05-23 09:53; FAPI
#     was hypothesis-tested in isolation, ldd output unchanged). The
#     curl-using verbs are unconditional in the multicall binary.
#   - Per D-001 D-OPTION-A 2026-05-23 (operator ratification): accept the
#     dynamic chain as the cost of TPM2 unlock being an EXPERIMENTAL
#     feature class. Plain passphrase + FIDO2-only installs incur ZERO
#     cost — the TPM2 binary + its DSO chain are bundled into the FDE
#     initramfs ONLY when HAVE_TPM2_TOOLS="yes" per build-fde-initramfs.sh
#     conditional (~5-10MB initramfs bloat for TPM2-enrolled installs).
#     Mirrors every shipping distro's tpm2-tools handling per
#     build-fde-initramfs.sh:158-159 (Alpine + Fedora + Arch precedent).
#   - check() whitelists the empirical dynamic dep set: libc + libdl +
#     libpthread + ld-linux + linux-vdso + libudev (defensive; tpm2
#     doesn't pull libudev but kept for sibling-static-package
#     consistency) + the curl chain (libcurl + libnghttp2 + libidn2 +
#     libunistring + libssh2 + libpsl) + libssl + libcrypto + libz +
#     libzstd. Anything OUTSIDE this set still FAILs the check (catches
#     future upstream additions that would silently bloat the chain).
#   - tpm2-tools 5.x ships as a BUSYBOX-STYLE multicall binary: a single
#     `tpm2` executable handles all ~70 verbs, with tpm2_<verb> as symlinks
#     to the multicall binary (created by `make install`; we replicate the
#     same 4 symlinks in do_install for the verbs the FDE init script uses).
#
# Output: /usr/lib/intergen/tpm2-tools-static/
#           tpm2                  (multicall binary, mostly-static)
#           tpm2_createprimary    → tpm2 (symlink)
#           tpm2_create           → tpm2 (symlink)
#           tpm2_load             → tpm2 (symlink)
#           tpm2_unseal           → tpm2 (symlink)

PKG_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

# Versions of secondary tarballs (extracted in build()). Pinned to existing
# packages where they exist so source bytes are shared.
TPM2_TSS_VER="4.1.3"
JSON_C_VER="0.18"

# Scratch staging dir for static archives + headers from secondary deps.
# Sits inside the per-package work dir (sibling to src/). Wiped at the start
# of build() so reruns are clean.
STAGING_DIR="${PWD}/../staging-static"

configure() {
    set -e
    # Defer all real configure work to build() — cryptsetup-static pattern.
    # tpm2-tools' ./configure needs staged libtss2-*.a + json-c.a at
    # link-flag-resolution time; those archives are produced inside build().
    echo "[tpm2-tools-static] configure() deferred to build() — needs staged deps first"
}

_extract_secondary() {
    local tarball="$1"  # e.g. tpm2-tss-4.1.3.tar.gz
    local destname="$2" # e.g. tpm2-tss-4.1.3

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

    local tpm2_tools_src="${PWD}"

    # === 1. json-c (tpm2-tss + tpm2-tools both consume) ===
    # CMAKE_POLICY_VERSION_MINIMUM=3.5 rescues json-c-0.18's apps/CMakeLists.txt
    # which declares cmake_minimum_required(VERSION 2.8); cmake 4.x has removed
    # pre-3.5 compat. Same fix pattern as cryptsetup-static/build.sh json-c.
    echo "[tpm2-tools-static] Building json-c-${JSON_C_VER} static..."
    local json_c_dir
    json_c_dir="$(_extract_secondary "json-c-${JSON_C_VER}-nodoc.tar.gz" "json-c-${JSON_C_VER}")"
    (
        set -e
        cd "${json_c_dir}"
        mkdir -p build && cd build
        cmake .. \
            -DCMAKE_INSTALL_PREFIX="${STAGING_DIR}" \
            -DCMAKE_INSTALL_LIBDIR=lib \
            -DBUILD_STATIC_LIBS=ON \
            -DBUILD_SHARED_LIBS=OFF \
            -DDISABLE_WERROR=ON \
            -DDISABLE_BSYMBOLIC=ON \
            -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
            -DCMAKE_BUILD_TYPE=Release
        make -j"${IGOS_JOBS}"
        make install
    )

    # === 2. tpm2-tss static (ESYS + device-TCTI only, no FAPI/no dlopen) ===
    #
    # Flag rationale (verified against tpm2-tss 4.1.3 configure.ac):
    # --disable-fapi          → drops libcurl + libgcrypt deps (Feature API
    #                            we don't use). ESYS layer is sufficient for
    #                            seal/unseal operations.
    # --disable-shared        → no .so emission; archives only
    # --enable-static         → produces libtss2-*.a archives
    # --enable-tcti-device    → device TCTI (talks /dev/tpmrm0 directly);
    #                            default-on but explicit-for-audit-clarity.
    #                            NOTE: --with-tcti=<name> was 1.x/2.x syntax
    #                            and is silently ignored in 4.x; use
    #                            per-TCTI --enable/--disable flags.
    # --disable-doxygen-doc   → no doc build (no doxygen dep)
    # --disable-tcti-libtpms  → drops libtpms simulator TCTI (tests only)
    # --disable-tcti-mssim    → drops Microsoft TPM simulator TCTI
    # --disable-tcti-swtpm    → drops swtpm simulator TCTI
    # --disable-tcti-pcap     → drops PCAP wrapper TCTI
    # --disable-tcti-cmd      → drops command-pipe TCTI
    #
    # NOTE: release tarballs come pre-bootstrapped (pre-generated configure +
    # VERSION). Do NOT run ./bootstrap — it expects git-clone consumers
    # (git describe for VERSION emit) and will print noise to logs.
    echo "[tpm2-tools-static] Building tpm2-tss-${TPM2_TSS_VER} static (ESYS + device-TCTI)..."
    local tpm2_tss_dir
    tpm2_tss_dir="$(_extract_secondary "tpm2-tss-${TPM2_TSS_VER}.tar.gz" "tpm2-tss-${TPM2_TSS_VER}")"
    (
        set -e
        cd "${tpm2_tss_dir}"
        PKG_CONFIG_PATH="${STAGING_DIR}/lib/pkgconfig" \
        CFLAGS="-I${STAGING_DIR}/include" \
        LDFLAGS="-L${STAGING_DIR}/lib" \
        ./configure \
            --prefix="${STAGING_DIR}" \
            --libdir="${STAGING_DIR}/lib" \
            --disable-shared \
            --enable-static \
            --disable-fapi \
            --enable-tcti-device \
            --disable-doxygen-doc \
            --disable-tcti-libtpms \
            --disable-tcti-mssim \
            --disable-tcti-swtpm \
            --disable-tcti-pcap \
            --disable-tcti-cmd
        make -j"${IGOS_JOBS}"
        make install
    )

    # === 3. tpm2-tools mostly-static against staging libtss2-*.a ===
    # MOSTLY-STATIC, not fully-static (see header comment for rationale).
    # tpm2-tools' configure has no --enable-static-binary; libtool strips
    # -static from LDFLAGS. Don't fight it. Instead: stage the heavy deps
    # as .a archives in STAGING_DIR/lib; libtool's default linker behavior
    # picks them up over .so when both exist in the same -L path; system
    # libs (libc/libdl/libpthread/libudev) remain dynamic and get bundled
    # into the FDE initramfs by build-fde-initramfs.sh's ldd loop.
    #
    # CRYPTO_LIBS lists libcrypto's transitive deps explicitly (-ldl +
    # -lpthread + -lz).
    #
    # The tpm2-tools build produces ALL tpm2_<verb> binaries (~70 of them).
    # We only ship 4 in do_install(); the rest are built-but-discarded.
    echo "[tpm2-tools-static] Configuring tpm2-tools-${version}..."
    cd "${tpm2_tools_src}"

    # tpm2-tools release tarballs are pre-bootstrapped — do NOT run
    # ./bootstrap (same as tpm2-tss above).

    export PKG_CONFIG_PATH="${STAGING_DIR}/lib/pkgconfig"
    export CFLAGS="-I${STAGING_DIR}/include ${CFLAGS:-}"
    export LDFLAGS="-L${STAGING_DIR}/lib ${LDFLAGS:-}"
    # libcrypto.a transitively needs libdl + libpthread + libz. Spell out
    # the link-line explicitly so symbols resolve cleanly.
    export CRYPTO_LIBS="-lcrypto -ldl -lpthread -lz"

    # --disable-fapi: tpm2-tools' Feature API tools (tpm2_fapi*) are the
    # likely libcurl-chain source observed in initial mostly-static ldd
    # output (libcurl + libnghttp2 + libidn2 + libunistring + libssh2 +
    # libpsl + libzstd + libz appearing as dynamic deps). FAPI tools fetch
    # vendor EK certs over HTTPS — not needed for FDE seal/unseal which
    # uses the ESYS layer directly. Companion to tpm2-tss --disable-fapi
    # already passed at line ~125.
    ./configure \
        --prefix=/usr \
        --disable-shared \
        --disable-hardening \
        --disable-unit \
        --disable-doxygen-doc \
        --disable-fapi

    echo "[tpm2-tools-static] Building tpm2-tools..."
    make -j"${IGOS_JOBS}"
}

check() {
    set -e

    # tpm2-tools 5.x is a single multicall binary at tools/tpm2; per-verb
    # tpm2_<verb> are symlinks created by `make install`. We don't run
    # `make install` (we install our 4 verbs directly in do_install), so
    # check the multicall binary directly.
    local bin="${PWD}/tools/tpm2"
    if [ ! -x "${bin}" ]; then
        echo "FAIL: tools/tpm2 multicall binary missing or not executable" >&2
        return 1
    fi

    # Mostly-static-with-multicall-overhead contract validation. Per
    # D-001 D-OPTION-A 2026-05-23, the tpm2-tools 5.x multicall binary
    # bundles ALL ~70 verbs; verbs we don't use (tpm2_getekcertificate +
    # FAPI tools) pull the libcurl chain as transitive DSO deps that no
    # configure flag drops (--disable-fapi was empirically tested 09:53;
    # ldd output unchanged). The whitelist below is the empirically-
    # observed dep set; anything OUTSIDE it FAILS so future upstream
    # additions don't silently bloat the FDE initramfs.
    local deps
    deps="$(ldd "${bin}" 2>&1 || true)"
    local unexpected
    unexpected="$(echo "${deps}" | grep -E '=>' | grep -vE 'libc\.so|libdl\.so|libpthread\.so|libudev\.so|libm\.so|libresolv\.so|libcurl\.so|libnghttp2\.so|libidn2\.so|libunistring\.so|libssh2\.so|libpsl\.so|libssl\.so|libcrypto\.so|libzstd\.so|libz\.so|linux-vdso|ld-linux' || true)"
    if [ -n "${unexpected}" ]; then
        echo "FAIL: tpm2 multicall binary has DSO deps OUTSIDE the whitelist:" >&2
        echo "${unexpected}" >&2
        echo "" >&2
        echo "Full ldd output for diagnosis:" >&2
        echo "${deps}" >&2
        return 1
    fi
    echo "PASS: tpm2 multicall binary DSO deps within EXPERIMENTAL whitelist"

    for verb in createprimary create load unseal; do
        if "${bin}" "${verb}" --help >/dev/null 2>&1; then
            echo "PASS: tpm2 ${verb} --help smoke-test OK"
        else
            echo "FAIL: tpm2 ${verb} --help did not run" >&2
            return 1
        fi
    done

    echo "PASS: tpm2 multicall mostly-static + 4 FDE verbs smoke-tested"
}

do_install() {
    set -e

    install -d "${DESTDIR}/usr/lib/intergen/tpm2-tools-static"

    local bin="${PWD}/tools/tpm2"
    if [ ! -x "${bin}" ]; then
        echo "FAIL: tools/tpm2 not found in build tree" >&2
        return 1
    fi

    # Install single multicall binary + 4 symlinks for the FDE verbs the
    # init script calls. Mirrors the upstream `make install` layout (which
    # also creates tpm2_<verb> symlinks to /usr/bin/tpm2).
    install -m 755 "${bin}" "${DESTDIR}/usr/lib/intergen/tpm2-tools-static/tpm2"
    for verb in tpm2_createprimary tpm2_create tpm2_load tpm2_unseal; do
        ln -sf tpm2 "${DESTDIR}/usr/lib/intergen/tpm2-tools-static/${verb}"
    done
}
