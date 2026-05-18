#!/bin/bash
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
#   - tpm2-tools built with LDFLAGS=-static; links against staging libtss2-*.a
#     and against system /usr/lib/libcrypto.a (retained as of openssl
#     precursor fix on the same branch).
#   - Only 4 binaries shipped: tpm2_createprimary + tpm2_create + tpm2_load
#     + tpm2_unseal. These are the operations the FDE init script calls.
#
# Output: /usr/lib/intergen/tpm2-tools-static/{tpm2_createprimary,
#         tpm2_create, tpm2_load, tpm2_unseal}

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
    echo "[tpm2-tools-static] Building json-c-${JSON_C_VER} static..."
    local json_c_dir
    json_c_dir="$(_extract_secondary "json-c-${JSON_C_VER}-nodoc.tar.gz" "json-c-${JSON_C_VER}")"
    (
        cd "${json_c_dir}"
        mkdir -p build && cd build
        cmake .. \
            -DCMAKE_INSTALL_PREFIX="${STAGING_DIR}" \
            -DCMAKE_INSTALL_LIBDIR=lib \
            -DBUILD_STATIC_LIBS=ON \
            -DBUILD_SHARED_LIBS=OFF \
            -DDISABLE_WERROR=ON \
            -DDISABLE_BSYMBOLIC=ON \
            -DCMAKE_BUILD_TYPE=Release
        make -j"${IGOS_JOBS}"
        make install
    )

    # === 2. tpm2-tss static (ESYS + device-TCTI only, no FAPI/no dlopen) ===
    # --disable-fapi          : drops libcurl + libgcrypt deps (Feature API
    #                            we don't use). ESYS layer is enough for
    #                            seal/unseal operations.
    # --disable-shared        : no .so emission; archives only
    # --enable-static         : produces libtss2-*.a archives
    # --with-tcti=device      : compile in only the /dev/tpmrm0 TCTI;
    #                            avoids dlopen-based TCTI module loader
    # --disable-doxygen-doc   : no doc build (no doxygen dep)
    # --disable-tcti-libtpms  : drops libtpms simulator TCTI (only for tests)
    # --disable-tcti-mssim    : drops Microsoft TPM simulator TCTI
    # --disable-tcti-swtpm    : drops swtpm simulator TCTI
    # --disable-tcti-pcap     : drops PCAP wrapper TCTI
    # --disable-tcti-cmd      : drops command-pipe TCTI
    echo "[tpm2-tools-static] Building tpm2-tss-${TPM2_TSS_VER} static (ESYS + device-TCTI)..."
    local tpm2_tss_dir
    tpm2_tss_dir="$(_extract_secondary "tpm2-tss-${TPM2_TSS_VER}.tar.gz" "tpm2-tss-${TPM2_TSS_VER}")"
    (
        cd "${tpm2_tss_dir}"
        ./bootstrap || true   # may already be bootstrapped in tarball
        PKG_CONFIG_PATH="${STAGING_DIR}/lib/pkgconfig" \
        CFLAGS="-I${STAGING_DIR}/include" \
        LDFLAGS="-L${STAGING_DIR}/lib" \
        ./configure \
            --prefix="${STAGING_DIR}" \
            --libdir="${STAGING_DIR}/lib" \
            --disable-shared \
            --enable-static \
            --disable-fapi \
            --with-tcti=device \
            --disable-doxygen-doc \
            --disable-tcti-libtpms \
            --disable-tcti-mssim \
            --disable-tcti-swtpm \
            --disable-tcti-pcap \
            --disable-tcti-cmd
        make -j"${IGOS_JOBS}"
        make install
    )

    # === 3. tpm2-tools statically linked against staging libtss2-*.a ===
    # Static-link the binary at link time. tpm2-tools' configure doesn't
    # support --enable-static-binary directly; we force it via LDFLAGS=-static.
    # PKG_CONFIG_PATH points at staging so tss2-esys.pc / tss2-sys.pc resolve
    # to the .a archives we just built. CRYPTO_LIBS lists the static libcrypto
    # deps explicitly (-ldl + -lpthread + -lz pulled in by libcrypto.a).
    #
    # The tpm2-tools build produces ALL tpm2_<verb> binaries (~70 of them).
    # We only ship 4 in do_install(); the rest are built-but-discarded.
    echo "[tpm2-tools-static] Configuring tpm2-tools-${version}..."
    cd "${tpm2_tools_src}"

    ./bootstrap || true   # may already be bootstrapped in tarball

    export PKG_CONFIG_PATH="${STAGING_DIR}/lib/pkgconfig"
    export CFLAGS="-I${STAGING_DIR}/include ${CFLAGS:-}"
    export LDFLAGS="-L${STAGING_DIR}/lib -static ${LDFLAGS:-}"
    # libcrypto.a transitively needs libdl + libpthread + libz. Spell out
    # the link-line explicitly so static-link doesn't drop symbols.
    export CRYPTO_LIBS="-lcrypto -ldl -lpthread -lz"

    ./configure \
        --prefix=/usr \
        --disable-shared \
        --disable-hardening \
        --disable-unit \
        --disable-doxygen-doc

    echo "[tpm2-tools-static] Building tpm2-tools..."
    make -j"${IGOS_JOBS}"
}

check() {
    set -e

    local bin_dir="${PWD}/tools/misc"
    # tpm2-tools 5.x produces tpm2_<verb> binaries under tools/ in various
    # subdirs. The 4 we care about are typically under tools/misc or
    # tools/, depending on minor version. Locate them.
    local createprimary load create unseal
    createprimary="$(find "${PWD}/tools" -name tpm2_createprimary -type f | head -1)"
    create="$(find "${PWD}/tools" -name tpm2_create -type f | head -1)"
    load="$(find "${PWD}/tools" -name tpm2_load -type f | head -1)"
    unseal="$(find "${PWD}/tools" -name tpm2_unseal -type f | head -1)"

    for bin in "${createprimary}" "${create}" "${load}" "${unseal}"; do
        if [ -z "${bin}" ] || [ ! -x "${bin}" ]; then
            echo "FAIL: tpm2-tools build did not produce all 4 required binaries" >&2
            echo "  createprimary=${createprimary} create=${create} load=${load} unseal=${unseal}" >&2
            return 1
        fi
        if file "${bin}" | grep -q "statically linked"; then
            echo "PASS: $(basename "${bin}") is statically linked"
        else
            echo "FAIL: $(basename "${bin}") is NOT statically linked" >&2
            file "${bin}" >&2
            return 1
        fi
        if ! "${bin}" --help >/dev/null 2>&1; then
            echo "FAIL: $(basename "${bin}") --help did not run" >&2
            return 1
        fi
    done

    echo "PASS: all 4 tpm2 tools statically linked + smoke-tested"
}

do_install() {
    set -e

    install -d "${DESTDIR}/usr/lib/intergen/tpm2-tools-static"

    for verb in tpm2_createprimary tpm2_create tpm2_load tpm2_unseal; do
        local bin
        bin="$(find "${PWD}/tools" -name "${verb}" -type f | head -1)"
        if [ -z "${bin}" ]; then
            echo "FAIL: ${verb} not found in build tree" >&2
            return 1
        fi
        install -m 755 "${bin}" \
            "${DESTDIR}/usr/lib/intergen/tpm2-tools-static/${verb}"
    done
}
