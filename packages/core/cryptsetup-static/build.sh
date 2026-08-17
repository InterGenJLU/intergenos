#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
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
    # CMAKE_POLICY_VERSION_MINIMUM=3.5 rescues json-c-0.18's apps/CMakeLists.txt
    # which declares cmake_minimum_required(VERSION 2.8); cmake 4.x has removed
    # pre-3.5 compat. The flag is the upstream-blessed escape hatch — it does
    # not change json-c's API, only the assumed policy floor for old projects.
    echo "[cryptsetup-static] Building json-c-${JSON_C_VER} static..."
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

    # === 2. popt (CLI option parsing) ===
    # Autotools. --enable-static --disable-shared yields libpopt.a + popt.pc.
    echo "[cryptsetup-static] Building popt-${POPT_VER} static..."
    local popt_dir
    popt_dir="$(_extract_secondary "popt-${POPT_VER}.tar.gz" "popt-${POPT_VER}")"
    (
        set -e
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
        set -e
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
        # We need ONLY libdevmapper.a + its header — never any dm tool.
        #
        # LVM2 2.03.38 relocated dmsetup into libdm/dm-tools/, and libdm's own
        # Makefile declares `SUBDIRS = dm-tools`. Because GNU make COMBINES the
        # prerequisites of recipe-less rules for the same target, both the bare
        # `install` AND `install_device-mapper` targets (whether invoked at the
        # top level or via `make -C libdm`) gain a `dm-tools.install*` SUBDIRS
        # prerequisite from make.tmpl — so they recurse into dm-tools and LINK
        # `dmsetup.static`, which fails `undefined reference to main` under our
        # device-mapper-only static config (NOT a parallel race; -j1 won't help).
        # Alpine's lvm2 recipe sidesteps the same wall by avoiding the
        # `install_device-mapper` target entirely.
        #
        # Fix: build the library with `make device-mapper` (proven to produce
        # libdm/ioctl/libdevmapper.a), then install via libdm's BLESSED
        # non-recursing leaf targets `install_ioctl_static` + `install_include`
        # (libdm/Makefile.in — neither has a SUBDIRS prerequisite, so neither
        # enters dm-tools). These are the exact libdm-local sub-targets that
        # `install_device-mapper` already ran successfully before dm-tools blew
        # up — they install libdevmapper.a + libdevmapper.h into the configured
        # STAGING_DIR (--prefix/--libdir above). cryptsetup's configure finds
        # them via AC_CHECK_LIB(devmapper,…) — no devmapper.pc required.
        # Research: LVM2 v2_03_38 Makefiles + Alpine APKBUILD. (2026-06-02)
        make device-mapper
        make -C libdm install_ioctl_static install_include
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
        set -e
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
    # veritysetup IS enabled: live-ISO uses dm-verity to lazily verify the
    # squashfs at block-read time (lever-4 fix for the 73-second sha256
    # boot-wait — pre-mount whole-file hash replaced by per-block verify
    # against a merkle hashtree). Same source tarball produces a separate
    # static binary `veritysetup.static` alongside `cryptsetup.static`; both
    # get installed under /usr/lib/intergen/. The verify is hardware-fast
    # (CPU's SHA-NI) and the merkle structure means each 4KiB block needs
    # ~one cache-line of hashtree → negligible vs the 9 GiB linear scan.
    # --disable-integritysetup     → dm-integrity standalone, not used here
    # --disable-nls                → no gettext runtime (smaller static binary)
    echo "[cryptsetup-static] Configuring cryptsetup-${version}..."
    cd "${cryptsetup_src}"

    export PKG_CONFIG_PATH="${STAGING_DIR}/lib/pkgconfig"
    export CFLAGS="-I${STAGING_DIR}/include ${CFLAGS:-}"
    # AC_CHECK_LIB(popt,...) in cryptsetup configure.ac is a direct gcc-link
    # test (not pkg-config-mediated); without -L${STAGING_DIR}/lib the linker
    # cannot find libpopt.a in our staged tree even though popt.pc is present.
    # Do NOT add -static here — --enable-static-cryptsetup injects -static into
    # LIBS itself (see flag rationale above); double-static causes link errors.
    export LDFLAGS="-L${STAGING_DIR}/lib ${LDFLAGS:-}"

    # --disable-shared           → no libcryptsetup.so emission. cryptsetup
    #                                 defaults to building BOTH cryptsetup.static
    #                                 (binary) AND libcryptsetup.so (shared lib).
    #                                 The shared lib link consumes our staged
    #                                 libuuid.a, which was built --disable-shared
    #                                 hence local-exec TLS — incompatible with
    #                                 -shared (relocation R_X86_64_TPOFF32 against
    #                                 'uuidd_cache'). We only want the static
    #                                 binary anyway; matches sibling static
    #                                 packages tpm2-tools-static (--disable-shared)
    #                                 + fido2-tools-static (BUILD_SHARED_LIBS=OFF).
    ./configure \
        --prefix=/usr \
        --enable-static-cryptsetup \
        --disable-shared \
        --with-crypto_backend=kernel \
        --disable-blkid \
        --disable-asciidoc \
        --disable-ssh-token \
        --disable-external-tokens \
        --disable-luks2-reencryption \
        --disable-keyring \
        --disable-integritysetup \
        --disable-nls

    echo "[cryptsetup-static] Building cryptsetup.static..."
    make -j"${IGOS_JOBS}" cryptsetup.static

    # veritysetup.static — separate upstream Makefile target. cryptsetup's
    # autotools build emits cryptsetup.static AND veritysetup.static when
    # --enable-veritysetup (the default; we no longer pass --disable-veritysetup).
    # The two static binaries share the same staged-static libs (json-c, popt,
    # libdevmapper, libuuid, libargon2) — no extra link-time setup needed.
    echo "[cryptsetup-static] Building veritysetup.static..."
    make -j"${IGOS_JOBS}" veritysetup.static
}

check() {
    set -e

    local cryptsetup_bin="${PWD}/cryptsetup.static"
    local veritysetup_bin="${PWD}/veritysetup.static"

    # Static-linkage self-checks. If either binary needs any shared library
    # at runtime, the initramfs use will fail at boot — catch it here.
    for bin in "$cryptsetup_bin" "$veritysetup_bin"; do
        if file "${bin}" | grep -q "statically linked"; then
            echo "PASS: $(basename "$bin") is statically linked"
        else
            echo "FAIL: $(basename "$bin") is NOT statically linked" >&2
            file "${bin}" >&2
            return 1
        fi
    done

    # Smoke-test: --version should work standalone with no shared deps.
    "${cryptsetup_bin}" --version || {
        echo "FAIL: cryptsetup.static --version did not run" >&2
        return 1
    }
    "${veritysetup_bin}" --version || {
        echo "FAIL: veritysetup.static --version did not run" >&2
        return 1
    }

    # cryptsetup must advertise LUKS2 support (LUKS unlock is its job).
    if "${cryptsetup_bin}" --help 2>&1 | grep -q "LUKS2"; then
        echo "PASS: cryptsetup.static reports LUKS2 support"
    else
        echo "FAIL: cryptsetup.static does not report LUKS2 support" >&2
        return 1
    fi

    # veritysetup must advertise verity commands (open + format are the two
    # we depend on — build-side format and initramfs-side open).
    if "${veritysetup_bin}" --help 2>&1 | grep -qE "open|format"; then
        echo "PASS: veritysetup.static reports verity command support"
    else
        echo "FAIL: veritysetup.static does not report verity commands" >&2
        return 1
    fi
}

do_install() {
    set -e

    # Two static binaries, no other artifacts. /usr/lib/intergen is the
    # InterGenOS private prefix for build-initramfs-time payloads.
    # cryptsetup-static — LUKS2 unlock in the FDE initramfs (D-001).
    # veritysetup-static — dm-verity open in the live-ISO initramfs (lever 4).
    install -d "${DESTDIR}/usr/lib/intergen"
    install -m 755 "${PWD}/cryptsetup.static" \
        "${DESTDIR}/usr/lib/intergen/cryptsetup-static"
    install -m 755 "${PWD}/veritysetup.static" \
        "${DESTDIR}/usr/lib/intergen/veritysetup-static"
}
