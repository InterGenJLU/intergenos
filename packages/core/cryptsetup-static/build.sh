#!/bin/bash
# cryptsetup-static 2.8.4 — Statically-linked cryptsetup for initramfs LUKS unlock.
#
# Distinct from packages/core/cryptsetup (dynamic, system-wide tool): this
# binary statically embeds json-c + popt + libdevmapper + libargon2 + glibc so
# it can run inside the FDE initramfs envelope before any other library is
# mounted. installer/init/build-fde-initramfs.sh (D-001/I-A) bundles it into
# /usr/lib/intergen/fde-initramfs.cpio.gz; fde-init.sh exec's it to unlock the
# LUKS2 root volume on early-boot.
#
# Architecture choice — crypto backend = kernel + internal argon2:
#   - Kernel AF_ALG handles AES-XTS volume encryption + HMAC-SHA256 integrity.
#     No userspace crypto library needs to be embedded statically (no libgcrypt,
#     no libssl, no libnettle). Smallest static binary + smallest audit
#     surface inside the initramfs envelope.
#   - cryptsetup's bundled libargon2 (--enable-internal-argon2) supplies the
#     argon2id PBKDF that LUKS2 requires (kernel does not support argon2 —
#     deliberate upstream choice, argon2 is heavy + lives in userspace).
#   - Result: 4 external tarballs total (cryptsetup itself + json-c + popt +
#     lvm2-for-libdevmapper). Matches D-001 dispatch scope exactly.
#
# Output: /usr/lib/intergen/cryptsetup-static  (single binary, no shared deps)

PKG_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

# Versions of secondary tarballs (extracted in build()). Pinned to the same
# versions as the dynamic packages.yml entries so source bytes are shared
# (one tarball, two consumers).
JSON_C_VER="0.18"
POPT_VER="1.19"
LVM2_VER="2.03.38"
UTIL_LINUX_VER="2.41.3"

# Scratch staging dir for static archives + headers from the secondary deps.
# Sits inside the per-package work dir (sibling to src/, which holds extracted
# cryptsetup). Wiped at the start of build() so reruns are clean.
STAGING_DIR="${PWD}/../staging-static"

configure() {
    set -e

    # configure() runs after orchestrator extraction of source[0] (cryptsetup),
    # with PWD == src/. We defer real configuration until build() because
    # cryptsetup's ./configure needs the staged static archives to exist at
    # link-flag-resolution time — and those archives are produced by build()
    # of the secondary deps below. So configure() is a no-op here; the real
    # configure invocation lives inside build() after staging is populated.
    echo "[cryptsetup-static] configure() deferred to build() — needs staged deps first"
}

# Helper: extract a secondary tarball from ${IGOS_SOURCES} into a scratch
# build dir under STAGING_DIR/build/<name>. Returns the absolute path to the
# extracted top-level dir on stdout. Pattern matches apparmor's manual
# extraction of source[1] (orchestrator only auto-extracts source[0]).
_extract_secondary() {
    local tarball="$1"  # e.g. json-c-0.18-nodoc.tar.gz
    local destname="$2" # e.g. json-c-0.18 (subdir name to land at)

    local build_root="${STAGING_DIR}/build"
    mkdir -p "${build_root}"

    rm -rf "${build_root:?}/${destname}"
    mkdir -p "${build_root}/${destname}"

    # --strip-components=1 collapses the tarball's top-level versioned dir
    # so the dest is the source root directly.
    tar -xf "${IGOS_SOURCES}/${tarball}" \
        -C "${build_root}/${destname}" \
        --strip-components=1 \
        --no-same-owner --no-same-permissions

    echo "${build_root}/${destname}"
}

build() {
    set -e

    # Wipe + recreate staging on every build so reruns are deterministic.
    rm -rf "${STAGING_DIR}"
    mkdir -p "${STAGING_DIR}"/{include,lib,lib/pkgconfig}

    local cryptsetup_src="${PWD}"

    # === 1. json-c (LUKS2 metadata parsing) ===
    # CMake-based. -DBUILD_STATIC_LIBS=ON + -DBUILD_SHARED_LIBS=OFF yields a
    # libjson-c.a in lib/ + a json-c.pc that cryptsetup's pkg-config picks up.
    echo "[cryptsetup-static] Building json-c-${JSON_C_VER} static..."
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

    # === 2. popt (CLI option parsing) ===
    # Autotools. --enable-static --disable-shared yields libpopt.a + popt.pc.
    echo "[cryptsetup-static] Building popt-${POPT_VER} static..."
    local popt_dir
    popt_dir="$(_extract_secondary "popt-${POPT_VER}.tar.gz" "popt-${POPT_VER}")"
    (
        cd "${popt_dir}"
        ./configure \
            --prefix="${STAGING_DIR}" \
            --libdir="${STAGING_DIR}/lib" \
            --enable-static \
            --disable-shared
        make -j"${IGOS_JOBS}"
        make install
    )

    # === 3. libdevmapper (from lvm2 tree) ===
    # We only need libdevmapper.a, not full LVM. Disable lvm/fsadm/dmeventd/
    # dmfilemapd/cmdlib + force static_link, then build the device-mapper
    # subtree only. Upstream lvm2 supports this via --enable-static_link +
    # `make device-mapper` (per device-mapper/Makefile.in).
    echo "[cryptsetup-static] Building libdevmapper from LVM2.${LVM2_VER} static..."
    local lvm2_dir
    lvm2_dir="$(_extract_secondary "LVM2.${LVM2_VER}.tgz" "LVM2.${LVM2_VER}")"
    (
        cd "${lvm2_dir}"
        ./configure \
            --prefix="${STAGING_DIR}" \
            --libdir="${STAGING_DIR}/lib" \
            --enable-static_link \
            --disable-lvm2cmd \
            --disable-fsadm \
            --disable-dmeventd \
            --disable-dmfilemapd \
            --disable-cmdlib \
            --disable-blkid_wiping \
            --disable-udev_sync \
            --disable-udev_rules \
            --disable-selinux \
            --with-default-locking-dir=/run/lock/lvm \
            --without-systemd-run
        make device-mapper
        make install_device-mapper
    )

    # === 4. libuuid (from util-linux tree) ===
    # cryptsetup's configure.ac line ~ does AC_CHECK_LIB(uuid, uuid_clear,…)
    # UNCONDITIONALLY — no --without-uuid escape. Without staging libuuid.a
    # the configure halts with "libuuid required". Build just libuuid from
    # the util-linux tree, no other util-linux programs/libs needed.
    echo "[cryptsetup-static] Building libuuid from util-linux-${UTIL_LINUX_VER} static..."
    local util_linux_dir
    util_linux_dir="$(_extract_secondary "util-linux-${UTIL_LINUX_VER}.tar.xz" "util-linux-${UTIL_LINUX_VER}")"
    (
        cd "${util_linux_dir}"
        ./configure \
            --prefix="${STAGING_DIR}" \
            --libdir="${STAGING_DIR}/lib" \
            --disable-all-programs \
            --enable-libuuid \
            --enable-static \
            --disable-shared
        make -j"${IGOS_JOBS}"
        make install
    )

    # === 5. cryptsetup itself, statically linked ===
    # Staging now holds: libjson-c.a + libpopt.a + libdevmapper.a + libuuid.a
    # + their .pc files + headers. Cryptsetup configure.ac auto-detects
    # static archives via the staged PKG_CONFIG_PATH + AC_CHECK_LIB.
    #
    # Flag rationale (verified against cryptsetup 2.8.4 configure.ac):
    # --enable-static-cryptsetup   → produce cryptsetup.static; configure
    #                                 injects -static into LIBS itself; do NOT
    #                                 set LDFLAGS=-static (double-static link)
    # --with-crypto_backend=kernel → AF_ALG kernel crypto (no openssl/gcrypt
    #                                 userspace static dep). Argon2 KDF for
    #                                 LUKS2 is internal (default; do NOT pass
    #                                 --enable-internal-argon2 — that flag
    #                                 does not exist; internal is default and
    #                                 the external opt-in is --enable-libargon2)
    # --disable-blkid              → drop libblkid dep (default-on requires
    #                                 staging libblkid.a; we don't need fs
    #                                 detection for unlock-only initrd path)
    # --disable-asciidoc           → no manpage generation
    # --disable-ssh-token          → no SSH plugin (dlopen-based, incompatible
    #                                 with static binary)
    # --disable-external-tokens    → no plugin loader at all (TPM2/FIDO2 are
    #                                 piped-key paths via cryptsetup --key-file=-,
    #                                 not via cryptsetup's plugin model)
    # --disable-luks2-reencryption → installer-tier operation, not initrd
    # --disable-keyring            → no kernel-keyring path (passphrase via stdin)
    # --disable-veritysetup        → dm-verity standalone, not LUKS2 unlock
    # --disable-integritysetup     → dm-integrity standalone, not LUKS2 unlock
    # --disable-nls                → no gettext runtime (smaller static binary)
    echo "[cryptsetup-static] Configuring cryptsetup-${version}..."
    cd "${cryptsetup_src}"

    export PKG_CONFIG_PATH="${STAGING_DIR}/lib/pkgconfig"
    export CFLAGS="-I${STAGING_DIR}/include ${CFLAGS:-}"

    ./configure \
        --prefix=/usr \
        --enable-static-cryptsetup \
        --with-crypto_backend=kernel \
        --disable-blkid \
        --disable-asciidoc \
        --disable-ssh-token \
        --disable-external-tokens \
        --disable-luks2-reencryption \
        --disable-keyring \
        --disable-veritysetup \
        --disable-integritysetup \
        --disable-nls

    echo "[cryptsetup-static] Building cryptsetup.static..."
    make -j"${IGOS_JOBS}" cryptsetup.static
}

check() {
    set -e

    local bin="${PWD}/cryptsetup.static"

    # Static-linkage self-check. If the binary needs any shared library at
    # runtime, the initramfs unlock will fail at boot — catch it here.
    if file "${bin}" | grep -q "statically linked"; then
        echo "PASS: cryptsetup.static is statically linked"
    else
        echo "FAIL: cryptsetup.static is NOT statically linked" >&2
        file "${bin}" >&2
        return 1
    fi

    # Smoke-test: --version should work standalone with no shared deps.
    "${bin}" --version || {
        echo "FAIL: cryptsetup.static --version did not run" >&2
        return 1
    }

    # Verify the binary advertises LUKS2 support (it must — that's the whole
    # point of bundling it). cryptsetup --help lists supported types.
    if "${bin}" --help 2>&1 | grep -q "LUKS2"; then
        echo "PASS: cryptsetup.static reports LUKS2 support"
    else
        echo "FAIL: cryptsetup.static does not report LUKS2 support" >&2
        return 1
    fi
}

do_install() {
    set -e

    # Single binary, no other artifacts. /usr/lib/intergen is the InterGenOS
    # private prefix for build-initramfs-time payloads (mirrors where the
    # FDE initramfs builder will read from per D-001/I-A scope).
    install -d "${DESTDIR}/usr/lib/intergen"
    install -m 755 "${PWD}/cryptsetup.static" \
        "${DESTDIR}/usr/lib/intergen/cryptsetup-static"
}
